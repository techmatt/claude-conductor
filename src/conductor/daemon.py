"""The daemon: one polling loop per control folder.

Each tick: signals → requests → questions → stages → running executors and merges →
schedule → inbox. Single-threaded; executors are separate processes the loop polls,
so several repos (and disjoint items within one) run at once.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import psutil

from . import config as config_mod
from . import (executor, git, globs, guard, inbox, items, notify, procs, questions, request, state, talk, targets,
               worktrees)
from .config import RUNTIME, Config
from .events import QUESTION_KINDS, Event, EventLog
from .util import clock, now, parse_stamp, read_text, retry_io, stamp, write_text

INBOX_REFRESH_SECONDS = 30
TICK_FAILURES_BEFORE_EXIT = 3     # consecutive ticks failing on an OSError
SIGNAL_SETTLE_SECONDS = 1.0
OUTCOME_KINDS = {"answered": "answer", "default": "default"}     # anything else: question_closed


class DaemonError(RuntimeError):
    pass


@dataclass
class Run:
    """An executor process: an item's run (record `running/<repo>/NNN.run.json`) or a
    stage (`running/<repo>/NNN.stage.json`)."""
    rec: dict
    rec_path: Path
    md_path: Path
    popen: subprocess.Popen | None = None
    proc: psutil.Process | None = None          # adopted after a restart
    tamper: list[str] = field(default_factory=list)

    @property
    def item_id(self) -> str:
        return self.rec["item"]

    def exited(self) -> bool:
        if self.popen is not None:
            return self.popen.poll() is not None
        if self.proc is not None:
            try:
                return not self.proc.is_running() or self.proc.status() == psutil.STATUS_ZOMBIE
            except psutil.Error:
                return True
        return True

    def pid(self) -> int | None:
        return self.popen.pid if self.popen else (self.proc.pid if self.proc else None)


def _strip_comment(value: str) -> str:
    """`42   # note` → `42`; `#42` stays (a `#` only starts a comment after whitespace)."""
    return re.split(r"\s+#", value.strip(), maxsplit=1)[0].strip()


def _signal_by(text: str) -> str | None:
    """The `by:` of a signal file (the first one), so even an invalid file is attributed."""
    for ln in text.splitlines():
        key, sep, value = ln.strip().partition(":")
        if sep and key.strip().lower() == "by" and _strip_comment(value):
            return _strip_comment(value)
    return None


def _file_hash(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


class Daemon:
    def __init__(self, cfg: Config, echo=print, notifier=None):
        """`notifier(title, body, tag)` raises desktop toasts; default: Windows toasts."""
        self.cfg = cfg
        self.echo = echo
        self.notifier = notifier if notifier is not None else notify.Toaster()
        self.log = EventLog(cfg.events_db, on_append=self._on_event)
        self.runs: dict[str, Run] = {}              # executing, or finished and waiting to merge
        self.stages: dict[str, Run] = {}            # staging processes still running
        self.no_stage: set[str] = set()             # a stage failed or was discarded: launch fresh
        self.snapshot: guard.Snapshot | None = None
        self.enqueued = self._live_enqueued()
        self.holds = {e.item: e.detail.get("reason") for e in self.log.query(kinds=["hold"])}
        self.q_seen = {(e.kind, e.detail.get("question")) for e in self.log.query(kinds=QUESTION_KINDS)}
        self.progress_seen = {(e.item, e.detail.get("seq")) for e in self.log.query(kinds=["progress"])}
        self.delivered_seen = {e.detail.get("tell") for e in self.log.query(kinds=["delivered"])}
        self._progress_sizes: dict[str, int] = {}
        self.runner = targets.Runner(cfg, self.log)
        self.blocked: set[str] = set()          # items waiting on a blocking question
        self.invalid_seen: dict[str, str] = {}
        self._inbox_key: tuple | None = None
        self._inbox_at = 0.0
        self._lock_file = cfg.state_dir / "daemon.json"
        self.stop_requested: dict | None = None     # set by a `stop: daemon` signal
        self._by: str | None = None                 # `by:` of the signal file being applied
        self._reorder_stuck: str | None = None      # why the journalled reorder is not finished

    def _live_enqueued(self) -> set[str]:
        """Ids whose `enqueue` still stands: a cancel or a reorder retires an id, so the
        same name queued again is enqueued again."""
        live: dict[str, bool] = {}
        for e in self.log.query(kinds=["enqueue", "cancel", "reorder"]):
            if e.kind == "enqueue":
                live[e.item] = True
            elif e.kind == "cancel" and e.detail.get("from") == "queue":
                live[e.item] = False
            elif e.kind == "reorder" and e.repo != "proposed":
                for name in set(e.detail.get("before", [])) - set(e.detail.get("after", [])):
                    live[f"{e.repo}/{name}"] = False
        return {i for i, on in live.items() if on}

    def _forget(self, item_id: str) -> None:
        """An id left the queue without running (cancel, reorder): drop what the daemon
        remembers about it so the same name can come back as a new item."""
        self.enqueued.discard(item_id)
        self.holds.pop(item_id, None)
        self.invalid_seen.pop(item_id, None)
        self.no_stage.discard(item_id)

    # ---------------------------------------------------------------- lifecycle
    def _on_event(self, ev: Event) -> None:
        if self.echo:
            self.echo(f"{clock(ev.ts)} #{ev.id} {inbox.describe(ev)}")
        if not (self.cfg.toasts and self.notifier):
            return
        msg = notify.message(self.cfg.project, ev.kind, ev.item, ev.repo, ev.detail)
        if msg:
            try:
                self.notifier(*msg, f"{self.cfg.project}#{ev.id}")
            except Exception as exc:     # a toast never stops the daemon
                self.log.append("warning", note=f"toast for #{ev.id} ({ev.kind}) failed: {exc!r}")

    def acquire(self) -> None:
        if self._lock_file.is_file():
            try:
                other = json.loads(self._lock_file.read_text(encoding="utf-8"))
            except ValueError:
                other = {}
            if other.get("pid") != os.getpid() and procs.alive(other.get("pid"), other.get("create_time")):
                raise DaemonError(f"conductor is already running for {self.cfg.control} (pid {other['pid']})")
        write_text(self._lock_file, json.dumps({
            "pid": os.getpid(), "create_time": procs.create_time(os.getpid()), "started": stamp(),
        }))

    def release(self) -> None:
        try:
            self._lock_file.unlink()
        except OSError:
            pass
        self.log.close()

    def startup(self) -> None:
        for d in ("queue", "proposed", "running", "done", "reports", "questions", "requests", "signals"):
            self.cfg.path(d).mkdir(parents=True, exist_ok=True)
        for repo in self.cfg.repos:
            self.cfg.path("queue", repo).mkdir(exist_ok=True)
        self.log.append("daemon_start", pid=os.getpid(), control=str(self.cfg.control))
        self._check_ignores()
        # Items left in running/ are judged against the snapshot they launched under.
        self.snapshot = guard.load(self.cfg.state_dir)
        self._recover_reorder()
        self._recover_signal()
        self.recover()
        if not self._active():
            if self.snapshot and (changes := guard.diff(self.cfg.control, self.snapshot)):
                self.log.append("drift", paths=guard.changed_paths(changes), changes=changes,
                                note="control folder changed while conductor was not running; accepted")
            self.snapshot = guard.take(self.cfg.control, self.cfg.state_dir, stamp())
        elif self.snapshot is None:
            self.snapshot = guard.take(self.cfg.control, self.cfg.state_dir, stamp())

    def _check_ignores(self) -> None:
        probes = [n if "." in n and n != ".conductor" else f"{n}/probe" for n in RUNTIME]
        missing = [p.split("/")[0] for p in probes
                   if subprocess.run(["git", "-C", str(self.cfg.control), "check-ignore", "-q", p],
                                                     **git.NO_WINDOW).returncode]
        if missing:
            self.log.append("warning", note=f"not git-ignored in the control folder: {missing}")

    def run_forever(self) -> None:
        self.acquire()
        try:
            self.startup()
            failures = 0
            while True:
                try:
                    self.tick()
                    failures = 0
                except OSError as exc:
                    # A file another process held open (on Windows a rename or replace
                    # then fails), past util.retry_io's wait. Recover as a restart would,
                    # without letting go of the lock; give up after a few in a row.
                    failures += 1
                    if failures >= TICK_FAILURES_BEFORE_EXIT:
                        self._log_stop("error", error=repr(exc))
                        raise
                    try:
                        self._recover_in_place(exc)
                    except OSError:
                        pass            # the next tick fails again and counts, or works
                if self.stop_requested is not None:
                    self._log_stop("signal", **self.stop_requested)
                    break
                time.sleep(self.cfg.poll_seconds)
        except KeyboardInterrupt:
            self._log_stop("interrupt")
        finally:
            self.write_inbox(force=True)
            self.release()

    def _recover_in_place(self, exc: OSError) -> None:
        """A tick died on an OSError part-way through: forget what this process holds in
        memory and rebuild it from disk with the restart rule, exactly as a restart would
        (live executors and stages are adopted, a half-done launch is requeued)."""
        self.log.append("warning", note=f"a tick failed: {exc!r}; recovering as after a restart")
        self.runs, self.stages, self.no_stage, self.blocked = {}, {}, set(), set()
        self.enqueued = self._live_enqueued()
        self._recover_reorder()
        self._recover_signal()
        self.recover()

    def _log_stop(self, reason: str, **extra) -> None:
        self.log.append("daemon_stop", reason=reason, running=sorted(self.runs), staging=sorted(self.stages),
                        note="executors keep running; the next start adopts them", **extra)

    def tick(self) -> None:
        self._retry_reorder()
        self.process_signals()
        if self.stop_requested is not None:
            return                  # `stop: daemon`: nothing more starts; run_forever exits
        self.process_requests()
        self.process_questions()
        self.process_talk()
        self.runner.poll()
        self.check_stages()
        self.check_runs()
        self.process_merges()
        self.schedule()
        self.write_inbox()

    def _active(self) -> dict[str, Run]:
        """Executor processes that may be running: guard attribution covers these."""
        active = {i: r for i, r in self.runs.items() if r.rec.get("phase", "exec") == "exec"}
        return {**active, **self.stages}

    # ---------------------------------------------------------------- recovery
    def recover(self) -> None:
        """Restart rule: never re-run. Adopt a live executor, harvest a finished one,
        requeue one that provably never launched, resume an interrupted merge, and mark
        anything else red. Stages are adopted, harvested or discarded."""
        for repo in self.cfg.repos:
            rdir = self.cfg.path("running", repo)
            if not rdir.is_dir():
                continue
            for rec_path in sorted(rdir.glob("*.run.json")):
                md = rec_path.with_name(rec_path.name.removesuffix(".run.json") + ".md")
                rec = state.read_run(rec_path)
                if not md.is_file():
                    # The launch died before the item moved; it is still queued.
                    if rec.get("worktree") and not rec.get("staged"):
                        self._discard_worktree(rec, "launch interrupted before the item moved")
                    rec_path.unlink()
                    continue
                run = Run(rec, rec_path, md)
                item_id = rec["item"]
                if self.log.last("finish", item=item_id):
                    self._move_to_done(run)       # finish was logged; only the move was lost
                    continue
                if rec.get("phase") == "merge":
                    self.runs[item_id] = run
                    self.log.append("recover", item=item_id, note="merge interrupted by a restart; resumed")
                    continue
                if rec.get("phase") == "concluding":
                    self.runs[item_id] = run
                    self.log.append("recover", item=item_id, note="judged before the restart; concluding")
                    self._settle(run)
                    continue
                # No pid and no process: never launched, unless the executor left output
                # (spawned, then exited before its pid was saved).
                if (rec.get("pid") is None and not procs.find_by_marker(rec["session_id"])
                        and not (rec.get("log") and Path(rec["log"]).exists())):
                    if rec.get("worktree"):
                        self._discard_worktree(rec, "never launched; requeued")
                    os.replace(md, self.cfg.path("queue", repo, md.name))
                    rec_path.unlink()
                    self.log.append("requeue", item=item_id, paths=[f"queue/{repo}/{md.name}"],
                                    reason="daemon stopped before the executor was launched")
                    continue
                proc = None
                if procs.alive(rec.get("pid"), rec.get("create_time")):
                    proc = psutil.Process(rec["pid"])
                else:
                    proc = procs.find_by_marker(rec["session_id"])
                if proc is not None:
                    run.proc = proc
                    self.runs[item_id] = run
                    self.log.append("adopt", item=item_id, pid=proc.pid, session_id=rec["session_id"])
                    continue
                self.runs[item_id] = run
                if executor.read_result(Path(rec["log"])) is not None:
                    self.log.append("recover", item=item_id, note="executor finished while conductor was down")
                    self.finish(run)
                else:
                    self.finish(run, interrupted=True)
            for md in sorted(rdir.glob("*.md")):
                if md.with_name(md.stem + ".run.json").exists():
                    continue
                item_id = f"{repo}/{md.stem}"
                if self.log.last("finish", item=item_id):
                    os.replace(md, self.cfg.path("done", repo, md.name))
                else:
                    os.replace(md, self.cfg.path("queue", repo, md.name))
                    self.log.append("requeue", item=item_id, reason="no run record in running/")
            for spath in sorted(rdir.glob("*.stage.json")):
                self._recover_stage(spath)

    def _recover_stage(self, spath: Path) -> None:
        rec = state.read_run(spath)
        run = Run(rec, spath, self.cfg.path("queue", rec["repo"], f"{rec['name']}.md"))
        item_id = rec["item"]
        if item_id in self.runs or spath.with_name(f"{rec['name']}.run.json").exists():
            spath.unlink()          # the resume launch got far enough; the run owns the worktree
            return
        if self.log.last("start", item=item_id):
            self._discard_stage(run, "the item already started")
            return
        if rec.get("phase") == "staged":
            return                                  # waiting for the lock; nothing runs
        if rec.get("pid") is None and not procs.find_by_marker(rec["session_id"]):
            self._discard_stage(run, "the daemon stopped before the stage launched")
            return
        if procs.alive(rec.get("pid"), rec.get("create_time")):
            run.proc = psutil.Process(rec["pid"])
        else:
            run.proc = procs.find_by_marker(rec["session_id"])
        if run.proc is not None:
            self.stages[item_id] = run
            self.log.append("adopt", item=item_id, pid=run.proc.pid, session_id=rec["session_id"], stage=True)
            return
        if executor.read_result(Path(rec["log"])) is None:
            self._discard_stage(run, "the stage was interrupted by a restart and left no result")
            return
        self.stages[item_id] = run
        self._stage_exited(run)

    # ---------------------------------------------------------------- guard
    def _guard_check(self, context: str) -> None:
        """Compare the control folder to the snapshot while executors run; revert and
        pin the change on the running items."""
        if not self.snapshot:
            return
        changes = guard.diff(self.cfg.control, self.snapshot)
        if not changes:
            return
        active = self._active()
        if not active:
            self.log.append("drift", paths=guard.changed_paths(changes), changes=changes,
                            note=f"changed while no executor ran ({context}); accepted")
            self.snapshot = guard.take(self.cfg.control, self.cfg.state_dir, stamp())
            return
        guard.restore(self.cfg.control, self.cfg.state_dir, self.snapshot)
        running = sorted(active)
        self.log.append("tamper", item=running[0] if len(running) == 1 else None,
                        paths=guard.changed_paths(changes), changes=changes,
                        concurrent=running if len(running) > 1 else [], found=context)
        for run in active.values():
            run.tamper += changes

    # ---------------------------------------------------------------- signals
    def process_signals(self) -> None:
        sdir = self.cfg.path("signals")
        if not sdir.is_dir():
            return
        for f in sorted(p for p in sdir.iterdir() if p.is_file()):
            if f.suffix in (".bad", ".tmp") or time.time() - f.stat().st_mtime < SIGNAL_SETTLE_SECONDS:
                continue
            rel = f"signals/{f.name}"
            by, applied = None, []
            try:
                text = read_text(f)
                by = _signal_by(text)
                lines = self._parse_signal(text)        # every line checked before any applies
                with self._attributed(by):
                    for key, value in lines:
                        # Journalled before each line: a crash from here until the file
                        # is gone must never apply its lines a second time.
                        write_text(self._signal_journal, json.dumps({"rel": rel, "applied": applied}))
                        self._apply_signal(key, value, rel)
                        applied.append(f"{key}: {value}")
                retry_io(f.unlink)
            except (ValueError, UnicodeDecodeError, OSError) as exc:
                with self._attributed(by):
                    extra = {"applied": applied} if applied else {}
                    self.log.append("invalid", paths=[rel], path=rel, error=str(exc), **extra)
                try:
                    os.replace(f, f.with_name(f.name + ".bad"))
                except OSError:
                    pass
            self._signal_journal.unlink(missing_ok=True)

    @property
    def _signal_journal(self) -> Path:
        return self.cfg.state_dir / "signal.json"

    def _recover_signal(self) -> None:
        """A signal file the daemon was applying when it died: some of its lines may
        have applied, so it is set aside as `.bad` (never applied again)."""
        if not self._signal_journal.is_file():
            return
        try:
            j = json.loads(self._signal_journal.read_text(encoding="utf-8"))
            sig = self.cfg.path(*j["rel"].split("/"))
            if sig.is_file():
                os.replace(sig, sig.with_name(sig.name + ".bad"))
                self.log.append("warning", paths=[j["rel"]], applied=j.get("applied", []),
                                note=f"{j['rel']} was interrupted part-way by a crash; set aside as .bad. Lines "
                                     f"applied before the line that was running: {j.get('applied') or 'none'}; "
                                     "that line and the ones after it may not have applied")
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self.log.append("warning", note=f"could not read {self.cfg.rel(self._signal_journal)}: {exc!r}")
        self._signal_journal.unlink(missing_ok=True)

    @contextmanager
    def _attributed(self, by: str | None):
        """Every event appended inside carries `detail.by` (side effects included)."""
        if not by:
            yield
            return
        plain = self.log.append

        def append(kind, item=None, repo=None, paths=(), **detail):
            detail.setdefault("by", by)
            return plain(kind, item, repo, paths, **detail)

        self.log.append, self._by = append, by
        try:
            yield
        finally:
            del self.log.append         # back to the class method
            self._by = None

    def _parse_signal(self, text: str) -> list[tuple[str, str]]:
        """`key: value` lines (blank lines, `#` lines and trailing ` # comments` ignored),
        syntax-checked as a whole; `by:` is taken out. What a verb acts on is checked when
        it applies."""
        out: list[tuple[str, str]] = []
        for n, ln in enumerate(text.splitlines(), 1):
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            key, sep, value = s.partition(":")
            key = key.strip().lower()
            # A message is free text: a ` # ` in it is part of it, not a comment.
            value = value.strip() if key in ("tell", "checkpoint") else _strip_comment(value)
            if not sep or not key:
                raise ValueError(f"line {n}: expected `key: value`, got {s!r}")
            if key == "by":
                continue
            if key == "read":
                if value.lower() not in ("latest", "all") and not value.lstrip("#").isdigit():
                    raise ValueError(f"line {n}: read: expects an event id or `latest`, got {value!r}")
            elif key == "clear":
                if value not in self.cfg.repos:
                    raise ValueError(f"line {n}: clear: {value!r} is not a repo")
            elif key in ("approve", "cancel", "reviewed"):
                if not value or len(value.split()) != 1:
                    raise ValueError(f"line {n}: {key}: expects one item, got {value!r}")
            elif key == "reorder":
                parts = value.split()
                if len(parts) < 2 or (parts[0] != "proposed" and parts[0] not in self.cfg.repos):
                    raise ValueError(f"line {n}: reorder: expects `<repo|proposed> <item> <item> ...`, got {value!r}")
            elif key == "tell":
                if len(value.split(None, 1)) != 2:
                    raise ValueError(f"line {n}: tell: expects `<item> <message>`, got {value!r}")
            elif key == "run":
                parts = value.split()
                if not 1 <= len(parts) <= 2 or (len(parts) == 2 and parts[1].lower() != "stop"):
                    raise ValueError(f"line {n}: run: expects `<target>` or `<target> stop`, got {value!r}")
            elif key == "checkpoint":
                pass                                    # any label, or none
            elif key == "stop":
                if value.lower() != "daemon":
                    raise ValueError(f"line {n}: stop: only `stop: daemon` is known, got {value!r}")
            else:
                raise ValueError(f"line {n}: unknown signal key {key!r}")
            out.append((key, value))
        if not out:
            raise ValueError("empty signal")
        return out

    def _apply_signal(self, key: str, value: str, rel: str) -> None:
        """One verb. Each checks everything it needs before it changes anything."""
        if key == "read":
            top = self.log.max_id()
            through = top if value.lower() in ("latest", "all") else int(value.lstrip("#"))
            through = max(min(through, top), self.log.read_mark())
            self.log.append("read", paths=[rel], through=through)
        elif key == "clear":
            self.log.append("clear", repo=value, paths=[rel])
        elif key == "approve":
            self._approve(value, rel)
        elif key == "cancel":
            self._cancel(value, rel)
        elif key == "reviewed":
            self._reviewed(value, rel)
        elif key == "tell":
            self._tell(value, rel)
        elif key == "checkpoint":
            self._checkpoint(value, rel)
        elif key == "run":
            name, *stop = value.split()
            (self.runner.stop if stop else self.runner.request)(name, self._by)
        elif key == "reorder":
            where, *refs = value.split()
            self._reorder(where, refs, rel)
        elif key == "stop":
            self.stop_requested = {"paths": [rel], **({"by": self._by} if self._by else {})}

    def _one(self, found: list[Path], verb: str, ref: str, where: str) -> Path:
        if not found:
            raise ValueError(f"{verb}: no item {ref!r} in {where}")
        if len(found) > 1:
            raise ValueError(f"{verb}: {ref!r} is ambiguous in {where}: {[p.stem for p in found]}")
        return found[0]

    def _running_file(self, repo: str, ref: str) -> Path | None:
        """`running/<repo>/NNN_name.md` named by `ref` while its run record exists."""
        for p in items.find(self.cfg.path("running", repo), ref):
            if p.with_name(p.stem + ".run.json").exists():
                return p
        return None

    def _approve(self, ref: str, rel: str) -> None:
        src = self._one(items.find(self.cfg.path("proposed"), ref), "approve", ref, "proposed/")
        repo = items.front_repo(src)
        if repo not in self.cfg.repos:
            raise ValueError(f"approve: {src.name}: `repo: {repo}` is not a configured repo")
        item = items.parse(src, repo)
        if item.errors:
            raise ValueError(f"approve: {src.name}: {'; '.join(item.errors)}")
        dest = self.cfg.path("queue", repo, src.name)
        if dest.exists() or item.id in state.scan(self.cfg, self.log).known():
            raise ValueError(f"approve: {item.id} already exists (queued, running or done); "
                             "give the proposal a new number")
        dest.parent.mkdir(parents=True, exist_ok=True)
        retry_io(os.rename, src, dest)     # refuses to overwrite on Windows; checked above anyway
        self._forget(item.id)
        self.log.append("approve", item=item.id, repo=repo, paths=[rel, self.cfg.rel(dest)],
                        **{"from": f"proposed/{src.name}"})

    def _cancel(self, ref: str, rel: str) -> None:
        prefix, slash, name_ref = ref.rpartition("/")
        if not slash or prefix == "proposed":
            found = items.find(self.cfg.path("proposed"), name_ref)
            if found or prefix == "proposed":
                src = self._one(found, "cancel", name_ref, "proposed/")
                repo = items.front_repo(src)
                self._cancel_file(src, "proposed", f"proposed/{src.stem}",
                                  repo if repo in self.cfg.repos else "proposed", rel)
                return
        if slash and prefix not in self.cfg.repos:
            raise ValueError(f"cancel: {prefix!r} is not a repo")
        repos = [prefix] if slash else list(self.cfg.repos)
        hits = [(r, p) for r in repos for p in items.find(self.cfg.path("queue", r), name_ref)]
        if not hits:
            for r in repos:
                if p := self._running_file(r, name_ref):
                    raise ValueError(f"cancel: {r}/{p.stem} is running; cancel never kills an executor")
            raise ValueError(f"cancel: no queued or proposed item {ref!r}")
        if len(hits) > 1:
            raise ValueError(f"cancel: {ref!r} is ambiguous: {[f'{r}/{p.stem}' for r, p in hits]}")
        repo, src = hits[0]
        item_id = f"{repo}/{src.stem}"
        self._drop_stage(repo, src.stem, "the item was cancelled")
        self._cancel_file(src, "queue", item_id, repo, rel)
        self._forget(item_id)

    def _reviewed(self, ref: str, rel: str) -> None:
        """Mark one finished item's report reviewed. Reviewed state is the event log's:
        an item is reviewed once a `reviewed` event names it."""
        prefix, slash, name_ref = ref.rpartition("/")
        if slash and prefix not in self.cfg.repos:
            raise ValueError(f"reviewed: {prefix!r} is not a repo")
        view = state.scan(self.cfg, self.log)
        repos = [prefix] if slash else list(self.cfg.repos)
        def named(ids) -> list[str]:
            return [i for i in ids if i.split("/", 1)[0] in repos and items.number_matches(i.split("/", 1)[1], name_ref)]

        hits = named(view.finishes)
        if not hits:
            if live := named([it.id for q in view.queued.values() for it in q] + list(view.running)):
                raise ValueError(f"reviewed: {live[0]} has not finished; only a finished item's report "
                                 "can be reviewed")
            raise ValueError(f"reviewed: no finished item {ref!r}")
        if len(hits) > 1:
            raise ValueError(f"reviewed: {ref!r} is ambiguous: {sorted(hits)}")
        item_id = hits[0]
        if prior := view.reviews.get(item_id):
            self.log.append("warning", item=item_id, paths=[rel],
                            note=f"{item_id} was already reviewed (#{prior.id}, {clock(prior.ts)}); nothing changed")
            return
        fin = view.finishes[item_id]
        self.log.append("reviewed", item=item_id, paths=[rel, *fin.paths[:1]], finish=fin.id,
                        status=fin.detail.get("status"), by=self._by or "unknown")

    def _checkpoint(self, label: str, rel: str) -> None:
        """A session boundary: its event id is its number. Every report still unreviewed
        gets a `warning` naming it, so the new session raises it first."""
        cp = self.log.append("checkpoint", paths=[rel], label=label, by=self._by or "unknown")
        for fin in state.scan(self.cfg, self.log).unreviewed():
            self.log.append("warning", item=fin.item, paths=fin.paths[:1], checkpoint=cp.id, finish=fin.id,
                            note=f"{fin.item} is still unreviewed at checkpoint #{cp.id} (finished "
                                 f"{fin.detail.get('status')} {clock(fin.ts)}); it carries forward: raise it first")

    def _tell(self, value: str, rel: str) -> None:
        """A message for a running executor: logged `tell`, then left in its mailbox for
        the PostToolUse hook (or its next `progress` call) to deliver."""
        ref, text = value.split(None, 1)
        prefix, slash, name_ref = ref.rpartition("/")
        if slash and prefix not in self.cfg.repos:
            raise ValueError(f"tell: {prefix!r} is not a repo")
        repos = [prefix] if slash else list(self.cfg.repos)
        hits = [f"{r}/{p.stem}" for r in repos if (p := self._running_file(r, name_ref))]
        if len(hits) > 1:
            raise ValueError(f"tell: {ref!r} is ambiguous: {hits}")
        run = self.runs.get(hits[0]) if hits else None
        if run is None or run.rec.get("phase", "exec") != "exec" or run.exited():
            view = state.scan(self.cfg, self.log)
            ids = [i for i in [*view.finishes, *view.running, *(it.id for q in view.queued.values() for it in q)]
                   if i.split("/", 1)[0] in repos and items.number_matches(i.split("/", 1)[1], name_ref)]
            if not ids:
                raise ValueError(f"tell: no running item {ref!r}")
            started = ids[0] in view.finishes or ids[0] in view.running
            raise ValueError(f"tell: {ids[0]} " + ("has exited; a message reaches only a running executor"
                                                   if started else "has not started; a message reaches only a running executor"))
        item_id = hits[0]
        ev = self.log.append("tell", item=item_id, paths=[rel], text=text, by=self._by or "unknown")
        talk.post(self.cfg.control, item_id, ev.id, text, self._by)

    def _cancel_file(self, src: Path, origin: str, item_id: str, repo: str, rel: str) -> None:
        cdir = self.cfg.state_dir / "cancelled"
        cdir.mkdir(parents=True, exist_ok=True)
        base = f"{time.strftime('%Y%m%d-%H%M%S')}__{repo if origin == 'queue' else 'proposed'}__{src.stem}"
        dest, n = cdir / f"{base}.md", 2
        while dest.exists():
            dest, n = cdir / f"{base}-{n}.md", n + 1
        retry_io(os.replace, src, dest)
        kept = self.cfg.rel(dest)
        self.log.append("cancel", item=item_id, repo=repo, paths=[rel, kept], kept=kept, **{"from": origin})

    def _drop_stage(self, repo: str, name: str, reason: str) -> None:
        """Discard the stage of queued `repo/name`, if it has one (its process tree is killed)."""
        item_id = f"{repo}/{name}"
        run = self.stages.get(item_id)
        if run is None:
            spath = self.cfg.path("running", repo, f"{name}.stage.json")
            if not spath.is_file():
                return
            rec = state.read_run(spath)
            run = self._stage_run(rec)
            if procs.alive(rec.get("pid"), rec.get("create_time")):
                procs.kill_tree(rec["pid"])
        self._discard_stage(run, reason)

    def _reorder(self, where: str, refs: list[str], rel: str) -> None:
        if self._reorder_journal.is_file():
            raise ValueError("reorder: an earlier reorder is still being finished (.conductor/reorder.json); "
                             "send this one again once it is logged")
        folder = self.cfg.path("proposed") if where == "proposed" else self.cfg.path("queue", where)
        label = "proposed/" if where == "proposed" else f"queue/{where}/"
        chosen: list[Path] = []
        for ref in refs:
            found = items.find(folder, ref)
            if not found and where != "proposed" and (p := self._running_file(where, ref)):
                raise ValueError(f"reorder: {where}/{p.stem} is running")
            src = self._one(found, "reorder", ref, label)
            if src in chosen:
                raise ValueError(f"reorder: {src.stem} is listed twice")
            if not items.NAME_RE.match(src.stem):
                raise ValueError(f"reorder: {src.name} is not named NNN_name.md")
            chosen.append(src)
        listed = [p.stem for p in chosen]
        new = items.renumber(listed)
        pairs = [(o, n) for o, n in zip(listed, new) if o != n]
        unlisted = {p.stem for p in folder.glob("*.md")} - set(listed)
        if clash := sorted(n for _, n in pairs if n in unlisted):
            raise ValueError(f"reorder: the new name(s) {clash} are taken by unlisted items")
        if where != "proposed":
            # A queue id is never reused: not one that ran, nor one running now.
            known = set(state.scan(self.cfg, self.log).known())
            if used := sorted(n for _, n in pairs if f"{where}/{n}" in known and n not in listed):
                raise ValueError(f"reorder: the new name(s) {used} are ids of items that ran or are running "
                                 f"in {where}; a re-run needs a new item number")
            # A renumbered item must still come after what it depends on in its own queue:
            # items start strictly in NNN order, so a dependency behind it would never run.
            stays = [f"{where}/{n}" for n in sorted(unlisted | {o for o in listed if o not in dict(pairs)})]
            for o, n in pairs:
                for ref in items.parse(folder / f"{o}.md", where).depends_on:
                    target = items.resolve_ref(ref, where, stays)
                    if target and int(target.split("/", 1)[1].split("_", 1)[0]) >= int(n.split("_", 1)[0]):
                        raise ValueError(f"reorder: {o} has depends_on {ref!r} ({target}); as {n} it would come "
                                         "before it in its own queue and hold forever")
        # A renumbered item is a new id: any depends_on naming the old one would dangle.
        def item_id(name: str) -> str | None:
            repo = where if where != "proposed" else items.front_repo(folder / f"{name}.md")
            return f"{repo}/{name}" if repo else None
        renamed = [i for o, _ in pairs if (i := item_id(o))]
        dependents = [items.parse(p, r) for r in self.cfg.repos for p in sorted(self.cfg.path("queue", r).glob("*.md"))]
        for p in sorted(self.cfg.path("proposed").glob("*.md")):
            if (r := items.front_repo(p)) is not None:
                dependents.append(items.parse(p, r))
        for d in dependents:
            for ref in d.depends_on:
                if target := items.resolve_ref(ref, d.repo, renamed):
                    raise ValueError(f"reorder: {d.path.stem} has depends_on {ref!r}, which names {target}; "
                                     "renumbering it would break the dependency")
        if where != "proposed":
            for o, _ in pairs:
                self._drop_stage(where, o, "the item was renumbered by a reorder")
        before = sorted(listed, key=lambda n: (int(n.split("_", 1)[0]), n))
        after = sorted(new, key=lambda n: (int(n.split("_", 1)[0]), n))
        journal = {"where": where, "pairs": pairs, "before": before, "after": after, "rel": rel,
                   **({"by": self._by} if self._by else {})}
        write_text(self._reorder_journal, json.dumps(journal, indent=1))
        try:
            self._roll_reorder(journal)
        except OSError as exc:
            # A rename failed part-way (a file held open). The journal stays: every tick
            # rolls it forward until it completes, and `where` launches nothing meanwhile.
            self._reorder_stuck = repr(exc)
            self.log.append("warning", repo=where, paths=[rel],
                            note=f"reorder of {where} interrupted: {exc!r}; it is finished on a later tick "
                                 f"(nothing in {where} launches until then)")

    @property
    def _reorder_journal(self) -> Path:
        return self.cfg.state_dir / "reorder.json"

    def _reorder_folder(self, where: str) -> Path:
        return self.cfg.path("proposed") if where == "proposed" else self.cfg.path("queue", where)

    def _retry_reorder(self) -> None:
        """Roll forward a reorder an earlier tick could not finish (see `_reorder`)."""
        if not self._reorder_journal.is_file():
            return
        try:
            j = json.loads(self._reorder_journal.read_text(encoding="utf-8"))
            self._roll_reorder(j, recovered=True, note=f"completed on a later tick (first try: {self._reorder_stuck})")
            self._reorder_stuck = None
        except OSError:
            pass                                    # still held open: next tick
        except (ValueError, KeyError, TypeError) as exc:
            bad = self._reorder_journal.with_name("reorder.json.bad")
            os.replace(self._reorder_journal, bad)
            self.log.append("warning", note=f"unreadable reorder journal set aside as {self.cfg.rel(bad)}: {exc!r}; "
                                            "any `*.reorder~*` temp names are restored at the next start")

    def _reorder_pending(self) -> str | None:
        """The queue (repo or `proposed`) of a reorder not finished yet."""
        if not self._reorder_journal.is_file():
            return None
        try:
            return json.loads(self._reorder_journal.read_text(encoding="utf-8")).get("where")
        except (OSError, ValueError, AttributeError):
            return None

    def _roll_reorder(self, j: dict, recovered: bool = False, note: str = "completed after a restart") -> None:
        """Carry out a journalled reorder: every `old.md` → temp, then every temp →
        `new.md`; then log it and drop the journal. Repeatable after a crash at any point."""
        folder = self._reorder_folder(j["where"])
        moved, stuck = False, []
        for o, n in j["pairs"]:
            src, tmp = folder / f"{o}.md", folder / f"{o}{items.REORDER_TEMP}{n}"
            if src.exists() and not tmp.exists():
                retry_io(os.rename, src, tmp)
                moved = True
        for o, n in j["pairs"]:
            tmp, dst = folder / f"{o}{items.REORDER_TEMP}{n}", folder / f"{n}.md"
            if tmp.exists():
                if dst.exists():
                    stuck.append(tmp.name)
                    continue
                retry_io(os.rename, tmp, dst)
                moved = True
        if j["where"] != "proposed":
            for o, _ in j["pairs"]:
                self._forget(f"{j['where']}/{o}")
        last = self.log.last("reorder", repo=j["where"])
        repeat = (recovered and not moved and last is not None
                  and last.detail.get("before") == j["before"] and last.detail.get("after") == j["after"])
        if not repeat:
            extra = {"by": j["by"]} if j.get("by") else {}
            if recovered:
                extra["note"] = note
            self.log.append("reorder", repo=j["where"], paths=[j["rel"]], before=j["before"], after=j["after"],
                            **extra)
        if stuck:
            self.log.append("warning", repo=j["where"],
                            note=f"reorder left {stuck} in {self.cfg.rel(folder)}: the target name is taken; "
                                 "rename them by hand")
        self._reorder_journal.unlink(missing_ok=True)

    def _recover_reorder(self) -> None:
        """Finish a reorder a crash interrupted; temp names without a journal go to the
        name they carry (or back to their old name if that is taken)."""
        if self._reorder_journal.is_file():
            # Its signal file, if it never finished, is set aside by _recover_signal.
            try:
                j = json.loads(self._reorder_journal.read_text(encoding="utf-8"))
                self._roll_reorder(j, recovered=True)
            except (ValueError, KeyError, TypeError, OSError) as exc:
                self.log.append("warning", note=f"could not finish the interrupted reorder in "
                                                f"{self.cfg.rel(self._reorder_journal)}: {exc!r}; retried every tick")
                self._reorder_stuck = repr(exc)
                return                  # its temp names belong to the journal
        folders = [self.cfg.path("proposed")] + [self.cfg.path("queue", r) for r in self.cfg.repos]
        for folder in folders:
            for tmp in sorted(folder.glob(f"*{items.REORDER_TEMP}*")) if folder.is_dir() else []:
                old, _, new = tmp.name.partition(items.REORDER_TEMP)
                for name in (new, old):
                    if not (folder / f"{name}.md").exists():
                        retry_io(os.rename, tmp, folder / f"{name}.md")
                        self.log.append("warning", note=f"reorder temp {self.cfg.rel(tmp)} restored as {name}.md")
                        break

    # ---------------------------------------------------------------- requests
    def process_requests(self) -> None:
        for d in request.pending(self.cfg):
            if not request.settled(d):
                continue
            rel = f"requests/{d.name}"
            try:
                req = request.parse(d)
            except request.RequestError as exc:
                self.log.append("request_failed", paths=[rel], request=d.name, error=str(exc))
                os.replace(d, d.with_name(d.name + request.FAILED_SUFFIX))
                continue
            self._guard_check(f"before request {d.name}")
            try:
                sha = request.apply(self.cfg, req)
            except (git.GitError, OSError) as exc:
                self.log.append("request_failed", paths=[rel], request=d.name, error=str(exc))
                os.replace(d, d.with_name(d.name + request.FAILED_SUFFIX))
                if self.snapshot:
                    guard.restore(self.cfg.control, self.cfg.state_dir, self.snapshot)
                continue
            self.log.append("request", paths=req.targets, request=d.name, message=req.message,
                            targets=req.targets, no_change=sha is None)
            if sha:
                self.log.append("commit", paths=req.targets, scope="control", sha=sha,
                                subject=req.message.splitlines()[0])
            shutil.rmtree(d)
            self.snapshot = guard.take(self.cfg.control, self.cfg.state_dir, stamp())
            if "conductor.toml" in req.targets:
                try:
                    new = config_mod.load(self.cfg.control)
                    busy = {r.rec["repo"] for r in [*self.runs.values(), *self.stages.values()]}
                    busy |= {p.parent.name for p in self.cfg.path("running").glob("*/*.json")}
                    if gone := sorted(busy - set(new.repos)):
                        raise config_mod.ConfigError(f"repos {gone} still have items in running/")
                    self.cfg = self.runner.cfg = new
                    self.log.append("config_reload", paths=["conductor.toml"])
                except config_mod.ConfigError as exc:
                    self.log.append("invalid", paths=["conductor.toml"], path="conductor.toml",
                                    error=f"{exc}; still using the previous config")

    # ---------------------------------------------------------------- questions
    def process_questions(self) -> None:
        """Log what the ask servers and the planner wrote in questions/. The server
        resolves questions; the daemon only observes, and moves deadlines for blocked time."""
        blocked = set()
        for q in questions.scan(self.cfg.control):
            rel = self.cfg.rel(q.path)
            if ("ask", q.id) not in self.q_seen:
                self._q_log("ask", q, [rel], text=q.summary(), block=q.block, default=q.default,
                            deadline=q.deadline, deadline_clock=clock(q.deadline) if q.deadline else None)
            if q.is_open:
                if q.block:
                    blocked.add(q.item)
                continue
            outcome = q.result.get("outcome")
            kind = OUTCOME_KINDS.get(outcome, "question_closed")
            if (kind, q.id) not in self.q_seen:
                waited = (parse_stamp(q.result["at"]) - parse_stamp(q.asked)).total_seconds()
                extra = {}
                if q.block and (run := self.runs.get(q.item)):
                    extra["deadline"] = self._extend_deadline(run, waited)
                paths = [self.cfg.rel(q.answer_path)] if outcome == "answered" else [rel]
                if outcome == "answered" and (by := questions.answer_by(q)):
                    extra["by"] = by
                self._q_log(kind, q, paths, outcome=outcome, text=q.result.get("text"),
                            reason=q.result.get("reason"), waited_seconds=round(waited), **extra)
            if outcome != "answered" and ("late", q.id) not in self.q_seen and q.answer_path.exists():
                text = questions.read_answer(q)
                if text is not None:
                    by = questions.answer_by(q)
                    self._q_log("late", q, [self.cfg.rel(q.answer_path)], text=text, outcome=outcome,
                                **({"by": by} if by else {}),
                                note="answered after the question resolved; the executor never saw it")
        self.blocked = blocked

    def process_talk(self) -> None:
        """Log what the executors' progress tool and message hook wrote: one `progress`
        per new progress line, one `delivered` per delivered message."""
        control = self.cfg.control
        for item_id in talk.progress_items(control):
            path = talk.progress_file(control, item_id)
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if self._progress_sizes.get(item_id) == size:
                continue
            self._progress_sizes[item_id] = size
            for line in talk.read_progress(control, item_id):
                if (item_id, line["seq"]) in self.progress_seen:
                    continue
                self.progress_seen.add((item_id, line["seq"]))
                self.log.append("progress", item=item_id, paths=[self.cfg.rel(path)],
                                **{k: v for k, v in line.items() if k in ("seq", "at", "note", "eta", "done", "remaining")})
        for msg in talk.delivered(control):
            if msg.get("id") in self.delivered_seen:
                continue
            self.delivered_seen.add(msg.get("id"))
            self.log.append("delivered", item=msg.get("item"), tell=msg.get("id"), via=msg.get("via"),
                            text=msg.get("text"), waited_seconds=talk.waited(msg))

    def _q_log(self, kind: str, q: questions.Question, paths: list[str], **detail) -> None:
        self.q_seen.add((kind, q.id))
        self.log.append(kind, item=q.item, paths=paths, question=q.id, **detail)

    def _extend_deadline(self, run: Run, seconds: float) -> str:
        """Time blocked on a question does not count toward max_hours."""
        rec = run.rec
        rec["deadline"] = stamp(parse_stamp(rec["deadline"]) + timedelta(seconds=seconds))
        rec["blocked_seconds"] = rec.get("blocked_seconds", 0) + round(seconds)
        self._save(run)
        return rec["deadline"]

    def _close_questions(self, item_id: str) -> None:
        for q in questions.scan(self.cfg.control):
            if q.item == item_id and q.is_open:
                questions.resolve(q, "closed", None, "daemon", reason="the item finished before an answer")
        self.process_questions()

    # ---------------------------------------------------------------- scheduling
    def schedule(self) -> None:
        view = state.scan(self.cfg, self.log)
        for d in view.stray:
            key = f"queue/{d.name}"
            if self.invalid_seen.get(key) != "stray":
                self.invalid_seen[key] = "stray"
                self.log.append("invalid", paths=[key], path=key,
                                error="queue folder is not a repo in conductor.toml")
        queued: dict[str, items.Item] = {}
        for repo, queue in view.queued.items():
            for item in queue:
                queued[item.id] = item
                if item.id not in self.enqueued:
                    self.enqueued.add(item.id)
                    meta = item.meta()
                    meta["paths_declared"] = meta.pop("paths")      # `paths` is the event's own column
                    self.log.append("enqueue", item=item.id, paths=[self.cfg.rel(item.path)], **meta)
                if item.errors:
                    key = f"{item.id}:{item.path.stat().st_mtime}"
                    if self.invalid_seen.get(item.id) != key:
                        self.invalid_seen[item.id] = key
                        self.log.append("invalid", item=item.id, paths=[self.cfg.rel(item.path)],
                                        error="; ".join(item.errors))
        # A stage belongs to the item file it read; a changed or removed item drops it.
        for item_id, srec in list(view.staged.items()):
            item = queued.get(item_id)
            if item_id in view.running:
                # A resume launch stopped between moving the item and dropping the record.
                self._stage_run(srec).rec_path.unlink(missing_ok=True)
                view.staged.pop(item_id)
            elif item is None or _file_hash(item.path) != srec.get("item_hash"):
                run = self.stages.get(item_id) or self._stage_run(srec)
                self._discard_stage(run, "the item file changed or left the queue after staging")
                view.staged.pop(item_id)
        reordering = self._reorder_pending()      # its items are under temp names: wait
        for repo, d in state.decide(self.cfg, view).items():
            if repo == reordering:
                continue
            if d.action == "hold" and d.item and self.holds.get(d.item.id) != d.reason:
                self.holds[d.item.id] = d.reason
                self.log.append("hold", item=d.item.id, paths=[self.cfg.rel(d.item.path)], reason=d.reason)
            elif d.action == "run":
                self.launch(d.item)
            elif d.action == "resume":
                self.resume(d.item, view.staged[d.item.id])
            elif d.action == "stage" and d.item.id not in self.no_stage and d.item.id not in self.stages:
                self.stage(d.item)

    def _stage_run(self, srec: dict) -> Run:
        return Run(srec, self.cfg.path("running", srec["repo"], f"{srec['name']}.stage.json"),
                   self.cfg.path("queue", srec["repo"], f"{srec['name']}.md"))

    def _save(self, run: Run) -> None:
        write_text(run.rec_path, json.dumps(run.rec, indent=1))

    def _worktree_event(self, rec: dict, action: str, note: str = "") -> None:
        self.log.append("worktree", item=rec["item"], paths=[Path(rec["worktree"]).as_posix()],
                        action=action, branch=rec.get("branch"), base=rec.get("base"), note=note)

    def _discard_worktree(self, rec: dict, note: str, action: str = "discarded") -> None:
        worktrees.discard(self.cfg, rec["repo"], Path(rec["worktree"]), rec.get("branch"))
        self._worktree_event(rec, action, note)

    def _make_worktree(self, item: items.Item, rec: dict, setup_log: Path) -> str | None:
        """Create the item's worktree into `rec`; the error, if it failed."""
        try:
            made = worktrees.create(self.cfg, item.repo, item.name, setup_log)
        except (worktrees.WorktreeError, git.GitError, OSError) as exc:
            return f"worktree: {exc}"
        rec.update(worktree=str(made.path), branch=made.branch, base=made.base, shared=made.linked)
        self._worktree_event(rec, "created", "; ".join(made.warnings))
        for w in made.warnings:
            self.log.append("warning", item=item.id, note=w)
        return None

    def launch(self, item: items.Item, stage: dict | None = None) -> None:
        """Run an item: in a new worktree, in the main checkout (`worktrees = false`),
        or, with `stage`, by resuming its staged session in the stage's worktree."""
        cfg = self.cfg
        # Idle: a difference is drift (accepted, re-snapshotted). Busy: it is tamper.
        self._guard_check(f"before launching {item.id}")
        repo_path = cfg.repos[item.repo]
        max_hours = item.max_hours or cfg.max_hours
        started = now()
        deadline = started + timedelta(hours=max_hours)
        paths = executor.RunPaths.for_item(cfg, item)
        rdir = cfg.path("running", item.repo)
        rdir.mkdir(parents=True, exist_ok=True)
        rec_path = rdir / f"{item.name}.run.json"
        md_path = rdir / f"{item.name}.md"
        rec = {
            "item": item.id, "repo": item.repo, "name": item.name,
            "session_id": stage["session_id"] if stage else str(uuid.uuid4()),
            "started": stamp(started), "deadline": stamp(deadline),
            "max_hours": max_hours, **item.meta(), "phase": "exec",
            "report": str(paths.report), "log": str(paths.log), "stderr": str(paths.stderr),
            "prompt": str(paths.prompt),
            "worktree": None, "branch": None, "base": None, "shared": [], "staged": bool(stage),
            "pid": None, "create_time": None,
        }
        run = Run(rec, rec_path, md_path)
        # Durable order: record, then worktree, then move, then launch, then the pid. A
        # restart that finds no pid and no live process with this session id requeues it.
        self._save(run)
        error, resume = None, None
        if stage:
            rec.update(worktree=stage["worktree"], branch=stage["branch"], shared=stage.get("shared", []))
            wt = Path(stage["worktree"])
            try:
                rec["base"] = worktrees.move_to(cfg, item.repo, wt)
                error = worktrees.run_setup(cfg, item.repo, wt, paths.setup)
            except (git.GitError, OSError) as exc:
                error = f"moving the staged worktree to {cfg.opts(item.repo).main}: {exc}"
            landed = git.commits_between(repo_path, stage["base"], rec["base"]) if not error else []
            resume = {"old": stage["base"], "new": rec["base"] or stage["base"], "landed": len(landed)}
            self._worktree_event(rec, "moved", f"{stage['base'][:10]} → {(rec['base'] or '?')[:10]}")
        elif cfg.opts(item.repo).worktrees:
            error = self._make_worktree(item, rec, paths.setup)
        cwd = Path(rec["worktree"]) if rec["worktree"] else repo_path
        rec["repo_head"] = rec["base"] or git.head(repo_path)
        rec["repo_status"] = git.tracked_status(cwd) if item.readonly and not error else None
        self._save(run)
        retry_io(os.replace, item.path, md_path)
        if stage:
            retry_io(Path(self._stage_run(stage).rec_path).unlink, True)
        paths.report.parent.mkdir(parents=True, exist_ok=True)
        for stale in (paths.report, paths.log, paths.stderr):
            if stale.exists():
                stale.unlink()
        self.runs[item.id] = run
        if error:
            self.log.append("start", item=item.id, paths=[cfg.rel(md_path)], session_id=rec["session_id"],
                            pid=None, error=error)
            self.finish(run, launch_error=error)
            return
        write_text(paths.prompt, executor.build_prompt(cfg, item, paths, max_hours, deadline, rec, resume))
        write_text(paths.mcp, executor.mcp_config(cfg, item, rec["session_id"]))
        write_text(paths.settings, executor.hook_settings(cfg, item))
        try:
            run.popen = procs.spawn(executor.command(cfg, rec["session_id"], paths.mcp, resume=bool(stage),
                                                     settings=paths.settings),
                                    cwd, paths.prompt, paths.log, paths.stderr)
        except OSError as exc:
            self.log.append("start", item=item.id, paths=[cfg.rel(md_path)], session_id=rec["session_id"],
                            pid=None, error=str(exc))
            self.finish(run, launch_error=str(exc))
            return
        rec["pid"] = run.popen.pid
        rec["create_time"] = procs.create_time(run.popen.pid)
        self._save(run)
        extra = {"worktree": rec["worktree"], "branch": rec["branch"]} if rec["worktree"] else {}
        if resume:
            extra |= {"resumed": True, "landed": resume["landed"]}
        self.log.append("start", item=item.id,
                        paths=[cfg.rel(md_path), cfg.rel(paths.report), cfg.rel(paths.log)],
                        session_id=rec["session_id"], pid=run.popen.pid, deadline=rec["deadline"], **extra)

    # ---------------------------------------------------------------- staging
    def stage(self, item: items.Item) -> None:
        """Launch the queue head early, read-only, in its own worktree: it reads and
        plans, then stops; `resume` continues the same session once it can run."""
        cfg = self.cfg
        self._guard_check(f"before staging {item.id}")
        paths = executor.RunPaths.for_item(cfg, item)
        started = now()
        rec = {
            "item": item.id, "repo": item.repo, "name": item.name, "phase": "staging",
            "session_id": str(uuid.uuid4()), "started": stamp(started),
            "deadline": stamp(started + timedelta(minutes=cfg.stage_minutes)),
            "item_hash": _file_hash(item.path),
            "log": str(paths.stage_log), "stderr": str(paths.stage_stderr), "prompt": str(paths.stage_prompt),
            "worktree": None, "branch": None, "base": None, "shared": [], "pid": None, "create_time": None,
        }
        run = self._stage_run(rec)
        self._save(run)
        error = self._make_worktree(item, rec, paths.setup)
        if error:
            run.rec_path.unlink(missing_ok=True)
            self.no_stage.add(item.id)
            self.log.append("warning", item=item.id, note=f"could not stage: {error}; it will launch fresh")
            return
        rec["untracked"] = worktrees.untracked(Path(rec["worktree"]))      # setup's output is allowed
        self._save(run)
        paths.report.parent.mkdir(parents=True, exist_ok=True)
        for stale in (paths.stage_log, paths.stage_stderr):
            stale.unlink(missing_ok=True)
        write_text(paths.stage_prompt, executor.build_stage_prompt(cfg, item, rec))
        try:
            run.popen = procs.spawn(executor.command(cfg, rec["session_id"], None), Path(rec["worktree"]),
                                    paths.stage_prompt, paths.stage_log, paths.stage_stderr)
        except OSError as exc:
            self._discard_stage(run, f"stage launch failed: {exc}")
            return
        rec["pid"] = run.popen.pid
        rec["create_time"] = procs.create_time(run.popen.pid)
        self._save(run)
        self.stages[item.id] = run
        self.log.append("stage", item=item.id, paths=[cfg.rel(paths.stage_log)], session_id=rec["session_id"],
                        pid=run.popen.pid, worktree=rec["worktree"], deadline=rec["deadline"])

    def check_stages(self) -> None:
        for run in list(self.stages.values()):
            if run.exited():
                self._stage_exited(run)
            elif now() > parse_stamp(run.rec["deadline"]):
                procs.kill_tree(run.pid())
                self._discard_stage(run, f"the stage ran past stage_minutes ({self.cfg.stage_minutes:g})")

    def _stage_exited(self, run: Run) -> None:
        self._guard_check(f"after staging {run.item_id}")
        rec = run.rec
        result = executor.read_result(Path(rec["log"]))
        wt = Path(rec["worktree"])
        problem = None
        if run.tamper:
            problem = "the stage changed the control folder (reverted)"
        elif result is None or result.get("is_error"):
            problem = "the stage left no result" if result is None else f"the stage errored: {result.get('subtype')}"
        elif self._stage_dirty(rec):
            problem = "the stage changed its worktree"
        if problem:
            self._discard_stage(run, problem)
            return
        rec["phase"] = "staged"
        rec["staged"] = stamp()
        self._save(run)
        self.stages.pop(run.item_id, None)
        plan = str(result.get("result") or "").strip()
        self.log.append("staged", item=run.item_id, paths=[self.cfg.rel(Path(rec["log"]))],
                        session_id=rec["session_id"], plan=plan[:500], cost_usd=result.get("total_cost_usd"),
                        worktree=str(wt))

    def _stage_dirty(self, rec: dict) -> bool:
        wt = Path(rec["worktree"])
        try:
            return (not wt.is_dir() or git.head(wt) != rec["base"] or bool(worktrees.changed(wt))
                    or worktrees.untracked(wt) != rec.get("untracked", []))
        except git.GitError:
            return True

    def _discard_stage(self, run: Run, reason: str) -> None:
        rec = run.rec
        if run.pid() and not run.exited():
            procs.kill_tree(run.pid())
        if rec["item"] in self.stages:
            # Its window closes here: what it did to the control folder is its own tamper.
            self._guard_check(f"discarding the stage of {rec['item']}")
        self.stages.pop(rec["item"], None)
        self.no_stage.add(rec["item"])
        if rec.get("worktree"):
            # A failure (a file still locked just after the kill) is a warning: the stage
            # record must go regardless, or the head waits on a stage nobody tracks.
            self._worktree_op(rec, "discarded", "stage discarded")
        retry_io(run.rec_path.unlink, True)
        self.log.append("warning", item=rec["item"], note=f"stage discarded: {reason}; the item launches fresh")

    def resume(self, item: items.Item, srec: dict) -> None:
        if self._stage_dirty(srec):
            self._discard_stage(self._stage_run(srec), "the staged worktree has changes")
            self.launch(item)
            return
        self.launch(item, stage=srec)

    # ---------------------------------------------------------------- runs
    def check_runs(self) -> None:
        for run in list(self.runs.values()):
            if run.rec.get("phase") == "merge":
                continue
            if run.exited():
                self.finish(run)
            elif now() > parse_stamp(run.rec["deadline"]) and run.item_id not in self.blocked:
                pid = run.pid()
                procs.kill_tree(pid)
                self.log.append("kill", item=run.item_id, pid=pid,
                                reason=f"max_hours {run.rec['max_hours']:g} reached")
                self.finish(run, killed=True)

    def finish(self, run: Run, killed: bool = False, interrupted: bool = False,
               launch_error: str | None = None) -> None:
        """Judge an exited executor. A worktree item that is not red waits to merge;
        everything else concludes now."""
        cfg, rec = self.cfg, run.rec
        item_id, repo = rec["item"], rec["repo"]
        self._guard_check(f"after {item_id} exited")
        reasons: list[str] = []
        if run.tamper:
            reasons.append("tamper: changed the control folder (reverted)")
        exit_code = run.popen.returncode if run.popen else None
        result = executor.read_result(Path(rec["log"]))
        if launch_error:
            reasons.append(f"launch failed: {launch_error}")
        elif killed:
            reasons.append(f"killed at max_hours {rec['max_hours']:g}")
        elif interrupted:
            reasons.append("interrupted: executor gone after a daemon restart and left no result")
        elif result is None:
            reasons.append(f"executor left no result (exit code {exit_code})")
        elif result.get("is_error"):
            reasons.append(f"executor error: {result.get('subtype')} {str(result.get('result', ''))[:200]}".strip())
        header, problem = executor.read_header(Path(rec["report"]))
        if problem:
            reasons.append(problem)
        repo_path = cfg.repos.get(repo)
        wt = Path(rec["worktree"]) if rec.get("worktree") else None
        cwd = wt if wt and wt.is_dir() else repo_path
        after_head = git.head(cwd) if cwd and git.is_repo(cwd) else None
        if not launch_error and cwd and after_head:
            if rec.get("readonly"):
                if after_head != rec.get("repo_head") or git.tracked_status(cwd) != rec.get("repo_status"):
                    reasons.append("readonly item changed the repo")
            elif wt and git.symbolic_head(wt) != f"refs/heads/{rec['branch']}":
                reasons.append(f"the worktree left {rec['branch']} (HEAD {git.symbolic_head(wt) or 'detached'}); "
                               "nothing merged, worktree kept")
            elif wt and worktrees.changed(wt):
                reasons.append("uncommitted changes left in the worktree (not merged)")
            branch = {"branch": rec["branch"]} if wt else {}
            for sha, subject in reversed(git.commits_between(cwd, rec.get("repo_head"), after_head)):
                self.log.append("commit", item=item_id, scope=repo, sha=sha, subject=subject, **branch)
            if rec.get("paths") and rec.get("repo_head") and after_head != rec["repo_head"]:
                outside = globs.outside(git.changed_files(cwd, rec["repo_head"], after_head), rec["paths"])
                if outside:
                    reasons.append(f"paths violation: changed {len(outside)} file(s) outside `paths`")
                    self.log.append("paths_violation", item=item_id, files=outside[:50],
                                    paths_declared=rec["paths"], branch=rec.get("branch"))
        report_status = header.get("status") if header and not problem else None
        if report_status == "red" and not reasons:
            reasons.append("report says red")
        status = "red" if reasons or report_status is None else report_status
        rec["outcome"] = {
            "status": status, "report_status": report_status, "reasons": reasons,
            "blocks": (header or {}).get("blocks"), "carry": (header or {}).get("carry"),
            "exit_code": exit_code, "cost_usd": (result or {}).get("total_cost_usd"),
            "turns": (result or {}).get("num_turns"), "launch_failed": bool(launch_error),
        }
        rec["exited"] = stamp()
        # The judgement is saved before anything else happens, so a restart from here on
        # resumes it (`merge` or `concluding`) instead of judging the item again.
        rec["phase"] = "merge" if wt and status != "red" and not launch_error and not rec.get("readonly") \
            else "concluding"
        self._save(run)
        if rec["phase"] == "concluding":
            self._settle(run)

    def _settle(self, run: Run) -> None:
        """An item that will not merge: discard or keep its worktree, then conclude.
        Safe to repeat after a restart."""
        rec = run.rec
        if rec.get("worktree"):
            if rec["outcome"].get("merged"):
                self._worktree_op(rec, "removed", "merged")
            elif rec["outcome"].get("launch_failed") or rec.get("readonly"):
                self._worktree_op(rec, "discarded", "readonly: never merged" if rec.get("readonly") else "launch failed")
            else:
                self._worktree_op(rec, "kept", "red: branch and worktree kept for inspection")
        self._conclude(run)

    def _worktree_op(self, rec: dict, action: str, note: str) -> None:
        """Discard (`discarded`/`removed`) or keep a worktree; a failure is a warning,
        never a crash: the item concludes either way."""
        try:
            if action == "kept":
                worktrees.keep(self.cfg, rec["repo"], Path(rec["worktree"]))
                self._worktree_event(rec, action, note)
            else:
                self._discard_worktree(rec, note, action=action)
        except (OSError, git.GitError, KeyError) as exc:
            self.log.append("warning", item=rec["item"],
                            note=f"worktree {rec['worktree']} not {action}: {exc!r}; remove it by hand")

    def process_merges(self) -> None:
        """Merge finished items into main, per repo serially in finish order."""
        pending: dict[str, list[Run]] = {}
        for run in self.runs.values():
            if run.rec.get("phase") == "merge":
                pending.setdefault(run.rec["repo"], []).append(run)
        for repo, runs in pending.items():
            for run in sorted(runs, key=lambda r: (r.rec["exited"], r.item_id)):
                if not self._merge(run):
                    break

    def _merge(self, run: Run) -> bool:
        """One merge attempt; False when it holds (later merges in the repo wait too)."""
        rec = run.rec
        wt = Path(rec["worktree"])
        try:
            m = worktrees.merge(self.cfg, rec["repo"], wt, rec["branch"])
        except git.GitError as exc:
            m = worktrees.Merge("hold", reason=f"git failed: {str(exc)[:300]}")
        if m.outcome == "hold":
            reason = f"merge waits: {m.reason}"
            if self.holds.get(rec["item"]) != reason:
                self.holds[rec["item"]] = reason
                self.log.append("hold", item=rec["item"], reason=reason, merge=True)
            return False
        out = rec["outcome"]
        if m.outcome == "conflict":
            out["status"] = "red"
            out["reasons"] = out["reasons"] + [f"rebase onto {self.cfg.opts(rec['repo']).main} conflicted"]
            self.log.append("conflict", item=rec["item"], branch=rec["branch"], files=m.files, error=m.reason,
                            note="branch kept; main untouched")
            rec["phase"] = "concluding"
            self._save(run)
            self._worktree_op(rec, "kept", "conflict: branch and worktree kept for inspection")
        else:
            commits = git.commits_between(self.cfg.repos[rec["repo"]], rec["base"], m.sha) if m.sha else []
            out["merged"] = m.sha
            self.log.append("merge", item=rec["item"], branch=rec["branch"], sha=m.sha, commits=len(commits),
                            note=m.reason)
            rec["phase"] = "concluding"
            self._save(run)
            self._worktree_op(rec, "removed", "merged")
        self._conclude(run)
        return True

    def _conclude(self, run: Run) -> None:
        cfg, rec = self.cfg, run.rec
        item_id, repo, out = rec["item"], rec["repo"], rec["outcome"]
        done_md = f"done/{repo}/{rec['name']}.md"
        extra = {k: rec[k] for k in ("branch",) if rec.get(k)}
        if out.get("merged"):
            extra["merged"] = out["merged"]
        # `red` before `finish`: a restart that finds the finish logged only moves the
        # item, so the red hold must already be on record.
        self.process_talk()
        for msg in talk.set_aside(cfg.control, item_id):
            self.log.append("warning", item=item_id, tell=msg.get("id"),
                            note=f"message #{msg.get('id')} was never delivered: the executor exited first")
        if out["status"] == "red":
            self.log.append("red", item=item_id, paths=[cfg.rel(Path(rec["report"]))],
                            reasons=out["reasons"], after_red=rec.get("after_red", "stop"))
        self.log.append(
            "finish", item=item_id, paths=[cfg.rel(Path(rec["report"])), cfg.rel(Path(rec["log"])), done_md],
            status=out["status"], report_status=out["report_status"], reasons=out["reasons"],
            blocks=out["blocks"], carry=out["carry"],
            session_id=rec["session_id"], exit_code=out["exit_code"],
            cost_usd=out["cost_usd"], turns=out["turns"],
            needs_planner=rec.get("needs_planner", False), after_red=rec.get("after_red", "stop"), **extra,
        )
        rec["phase"] = "done"
        if run.rec_path.exists():
            self._save(run)
        self._move_to_done(run)
        self.runs.pop(item_id, None)
        self._close_questions(item_id)
        qdir = cfg.path("queue", repo)
        still_running = any(r.rec["repo"] == repo for r in self.runs.values())
        if not still_running and not (qdir.is_dir() and any(qdir.glob("*.md"))):
            self.log.append("queue_empty", item=item_id, repo=repo, last=item_id)

    def _move_to_done(self, run: Run) -> None:
        ddir = self.cfg.path("done", run.rec["repo"])
        ddir.mkdir(parents=True, exist_ok=True)
        if run.rec_path.exists():
            retry_io(os.replace, run.rec_path, ddir / run.rec_path.name)
        if run.md_path.exists():
            retry_io(os.replace, run.md_path, ddir / run.md_path.name)

    # ---------------------------------------------------------------- inbox
    def write_inbox(self, force: bool = False) -> None:
        key = (self.log.max_id(), self.log.read_mark())
        if not force and key == self._inbox_key and time.time() - self._inbox_at < INBOX_REFRESH_SECONDS:
            return
        view = state.scan(self.cfg, self.log)
        write_text(self.cfg.path("inbox.md"), inbox.render(self.cfg, self.log, view))
        self._inbox_key, self._inbox_at = key, time.time()
