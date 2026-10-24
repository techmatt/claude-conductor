"""The daemon: one polling loop per control folder.

Each tick: signals → requests → questions → stages → running executors and merges →
schedule → inbox. Single-threaded; executors are separate processes the loop polls,
so several repos (and disjoint items within one) run at once. The one exception is the
picker server (`picker`), whose handler threads touch only files and a queue that the
loop drains into the event log.

`Daemon` is this module plus eleven mixins, one per concern: `daemon_signals` (signal
files and their verbs), `daemon_messages` (questions, progress, deliveries, amendments),
`daemon_receipts` (a receipt per planner write), `daemon_reap` (leftover processes and
worktrees), `daemon_merge` (merges and concluding an item), `daemon_setup` (a repo's
setup commands, off the loop), `daemon_restart` (restarting itself when its code changes),
`daemon_templates` (what a template item is handed), `daemon_watch` (the night shift),
`daemon_push` (pushing main to a remote) and `daemon_memory` (memory sampling and its tells).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import psutil

from . import (audits, desktop, executor, git, globs, guard, inbox, items, memory, notify, picker, picks, procs, request, state,
               talk, targets, worktrees)
from . import resume as resume_items
from . import config as config_mod
from .config import RUNTIME, Config
from .daemon_memory import MemoryMixin
from .daemon_merge import MergeMixin
from .daemon_messages import MessagesMixin
from .daemon_push import PushMixin
from .daemon_receipts import ReceiptsMixin
from .daemon_reap import ReapMixin
from .daemon_restart import RestartMixin
from .daemon_setup import SetupMixin
from .daemon_signals import SIGNAL_SETTLE_SECONDS, SignalsMixin
from .daemon_templates import TemplatesMixin
from .daemon_watch import WatchMixin
from .events import QUESTION_KINDS, Event, EventLog
from .util import clock, duration, file_hash, now, parse_stamp, retry_io, stamp, write_text

INBOX_REFRESH_SECONDS = 30
TICK_FAILURES_BEFORE_EXIT = 3     # consecutive ticks failing on an OSError

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
    resolver: subprocess.Popen | None = None    # a merge executor resolving a prose conflict
    setup: subprocess.Popen | None = None       # the setup runner (`setup_run`), before the executor

    @property
    def item_id(self) -> str:
        return self.rec["item"]

    @property
    def setting_up(self) -> bool:
        """Setup still runs: no executor has spawned, so it has not exited either."""
        return (self.rec.get("setup") or {}).get("state") == "running"

    def exited(self) -> bool:
        if self.setting_up:
            return False
        if self.popen is not None:
            return self.popen.poll() is not None
        if self.proc is not None:
            try:
                return not self.proc.is_running() or self.proc.status() == psutil.STATUS_ZOMBIE
            except psutil.Error:
                return True
        return True

    def pid(self) -> int | None:
        """The executor's, or while setup runs the setup runner's."""
        if self.setting_up:
            return self.setup.pid if self.setup else self.rec["setup"].get("pid")
        return self.popen.pid if self.popen else (self.proc.pid if self.proc else None)


def _pick_ref(repo: str, value: str | None) -> str | None:
    """A report's `pick:` (a manifest path in its repo) as the ref the `pick:` signal takes."""
    value = (value or "").strip().replace("\\", "/").lstrip("/")
    return f"{repo}/{picks.normal(value)}" if value and value.lower() != "none" else None


class Daemon(SignalsMixin, MessagesMixin, ReapMixin, MergeMixin, SetupMixin, RestartMixin, ReceiptsMixin,
             TemplatesMixin, WatchMixin, PushMixin, MemoryMixin):
    def __init__(self, cfg: Config, echo=print, notifier=None):
        """`notifier(title, body, tag)` raises desktop toasts; default: Windows toasts."""
        self.cfg = cfg
        self.echo = echo
        self.notifier = notifier if notifier is not None else notify.Toaster()
        self._init_receipts()                     # before the log: every event may be a receipt's
        self.log = EventLog(cfg.events_db, on_append=self._on_event)
        self.runs: dict[str, Run] = {}              # executing, or finished and waiting to merge
        self.stages: dict[str, Run] = {}            # staging processes still running
        self.landings: dict[str, Run] = {}          # `land:` requests for kept red branches (daemon_merge)
        self.no_stage: set[str] = set()             # a stage failed or was discarded: launch fresh
        self.snapshot: guard.Snapshot | None = None
        self.enqueued = self._live_enqueued()
        self.holds = {e.item: e.detail.get("reason") for e in self.log.query(kinds=["hold"])}
        self.q_seen = {(e.kind, e.detail.get("question")) for e in self.log.query(kinds=QUESTION_KINDS)}
        self.progress_seen = {(e.item, e.detail.get("seq")) for e in self.log.query(kinds=["progress"])}
        self.delivered_seen = {e.detail.get("tell") or e.detail.get("amend") for e in self.log.query(kinds=["delivered"])}
        self.awaiting_reply = self._awaiting_reply()
        # An amend file refused stays where it is: refused once per content.
        self.amend_refused = {(e.detail.get("path"), e.detail.get("hash")) for e in self.log.query(kinds=["amend_refused"])}
        self.amend_waiting: dict[str, str | None] = {}  # amend file → hash, for an item not seen yet
        self._progress_sizes: dict[str, int] = {}
        self.runner = targets.Runner(cfg, self.log)
        self.picker = picker.Server(cfg, self.log)  # no socket until the first `pick:`
        self.opener = desktop.Opener(cfg, self.log)
        self.blocked: set[str] = set()          # items waiting on a blocking question
        self.invalid_seen: dict[str, str] = {}
        self.report_stats: dict[str, tuple] = {}    # reviewed item → its files' (size, mtime) last hashed
        self.queued_hashes: dict[str, str | None] = {}  # queued item → its file's hash: a change is `edited`
        self._inbox_key: tuple | None = None
        self._inbox_at = 0.0
        self._lock_file = cfg.state_dir / "daemon.json"
        self.stop_requested: dict | None = None     # set by a `stop: daemon` signal
        self._by: str | None = None                 # `by:` of the signal file being applied
        self._reorder_stuck: str | None = None      # why the journalled reorder is not finished
        self._lock: dict | None = None              # daemon.json while this process holds it
        self._died: dict | None = None              # the lock a dead daemon left behind
        self.memory_probe = procs.memory_status     # tests stand in a box at a given level
        self.mem_level = "ok"
        self._mem_at: float | None = None           # monotonic time of the last sample
        self._mem_clear_since: float | None = None  # below the level since (monotonic), not yet calm
        self.mem_told: dict[str, str] = {}          # running item → level it was last told about
        self._init_restart()
        self._init_watch()
        self._init_push()

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
        self._receipt_event(ev)
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
        """Take `.conductor/daemon.json`. A clean stop removes it, so one left by a dead
        process means that daemon died (killed, its tree reaped): `startup` logs it."""
        if self._lock_file.is_file():
            try:
                other = json.loads(self._lock_file.read_text(encoding="utf-8"))
            except ValueError:
                other = {}
            if other.get("pid") != os.getpid() and procs.alive(other.get("pid"), other.get("create_time")):
                raise DaemonError(f"conductor is already running for {self.cfg.control} (pid {other['pid']})")
            if isinstance(other, dict) and other.get("pid") != os.getpid():
                self._died = other
        self._lock = {"pid": os.getpid(), "create_time": procs.create_time(os.getpid()), "started": stamp()}
        self._beat()

    def _beat(self) -> None:
        """The heartbeat: `beat` in daemon.json, rewritten with each memory sample. The
        app reads a stale one as a daemon that stopped answering."""
        if self._lock is not None:
            write_text(self._lock_file, json.dumps({**self._lock, "beat": stamp()}))

    def _log_death(self) -> None:
        """The daemon before this one left its lock: say when it was last alive, how long
        nothing ran, what memory looked like then and what was running."""
        d, self._died = self._died, None
        last = d.get("beat") or d.get("started")
        gap = duration((now() - parse_stamp(last)).total_seconds()) if last else "unknown"
        mem = memory.latest(self.cfg.state_dir)
        running = sorted(f"{p.parent.name}/{p.name.removesuffix('.run.json')}"
                         for p in self.cfg.path("running").glob("*/*.run.json"))
        self.log.append("daemon_died", pid=d.get("pid"), started=d.get("started"), last_beat=last,
                        last_beat_clock=clock(last) if last else None, gap=gap, running=running,
                        memory={k: mem.get(k) for k in ("at", "level", "commit", "limit", "free")} if mem else None,
                        note="it stopped without logging daemon_stop: killed, or its process tree was")

    def release(self) -> None:
        self._lock = None
        try:
            self._lock_file.unlink()
        except OSError:
            pass
        self.log.close()

    def startup(self) -> None:
        for d in ("queue", "proposed", "running", "done", "reports", "questions", "picks", "requests", "signals"):
            self.cfg.path(d).mkdir(parents=True, exist_ok=True)
        for repo in self.cfg.repos:
            self.cfg.path("queue", repo).mkdir(exist_ok=True)
        self._prepare_receipts()
        self._prepare_backlog()
        self.log.append("daemon_start", pid=os.getpid(), control=str(self.cfg.control))
        self._read_code()
        if self._died:
            self._log_death()
        if (owner := procs.claude_ancestor()) is not None:
            self.log.append("warning", note=f"this daemon runs under a Claude Code session (pid {owner.pid}): when that "
                                            "session ends or reaps its background commands, the daemon and every "
                                            "executor it started die with it. Start it from the app or a terminal.")
        self._check_ignores()
        # Items left in running/ are judged against the snapshot they launched under.
        self.snapshot = guard.load(self.cfg.state_dir)
        self._recover_reorder()
        self._recover_signal()
        self.recover()
        self._recover_landings()
        self._retry_leftovers("daemon start")
        self.push_ahead()
        self._compact_logs(sorted(state.scan(self.cfg, self.log).done))
        if not self._active():
            if self.snapshot and (changes := guard.diff(self.cfg.control, self.snapshot)):
                self.log.append("drift", paths=guard.changed_paths(changes), changes=changes,
                                note="control folder changed while conductor was not running; accepted")
            self.snapshot = guard.take(self.cfg.control, self.cfg.state_dir, stamp())
        elif self.snapshot is None:
            self.snapshot = guard.take(self.cfg.control, self.cfg.state_dir, stamp())

    def _check_ignores(self) -> None:
        probes = [n if "." in n and n != ".conductor" else f"{n}/probe" for n in RUNTIME]
        # One call for every probe: it prints the ones that are ignored.
        out = subprocess.run(["git", "-C", str(self.cfg.control), "check-ignore", *probes],
                             capture_output=True, text=True, **git.NO_WINDOW).stdout
        ignored = set(out.splitlines())
        missing = [p.split("/")[0] for p in probes if p not in ignored]
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
                if self.restart_due():
                    break               # the new daemon is up and waits for this one to exit
                self._nap()
        except KeyboardInterrupt:
            self._log_stop("interrupt")
        finally:
            try:
                self.picker.drain()     # a save that arrived during the last tick
                self.picker.close()
            except Exception:           # noqa: BLE001 - never in the way of releasing the lock
                pass
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
        self.process_proposed()
        self.process_questions()
        self.process_talk()
        self.runner.poll()
        self.picker.drain()
        self.check_stages()
        self.check_runs()
        self.process_merges()
        self.process_pushes()
        self.sample_memory()
        self.night_shift()
        self.schedule()
        self.write_inbox()

    def _active(self) -> dict[str, Run]:
        """Executor processes that may be running: guard attribution covers these. Never
        a setup, which is no executor and writes only in its worktree."""
        active = {i: r for i, r in [*self.runs.items(), *self.landings.items()] if r.rec.get("phase", "exec") == "exec"
                  or (r.rec.get("resolve") or {}).get("state") == "running"}
        return {**active, **{i: r for i, r in self.stages.items() if not r.setting_up}}

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
                if rec.get("phase") == "setup":
                    self._recover_setup_run(run)
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
                alive = procs.alive(rec.get("pid"), rec.get("create_time"))
                self._recover_spawned(run, psutil.Process(rec["pid"]) if alive
                                      else procs.find_by_marker(rec["session_id"]))
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
        if run.setting_up:
            self._kill_setup(rec)
            self._discard_stage(run, "the daemon stopped during the stage's setup")
            return
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
        own = self.own_control_git(changes)     # conductor's own push or commit (daemon_push)
        if not active or own:
            self.log.append("drift", paths=guard.changed_paths(changes), changes=changes, **own,
                            note=f"changed while no executor ran ({context}); accepted" if not active else
                            f"conductor's own git work ({own['own']}) while {', '.join(sorted(active))} ran; accepted")
            self.snapshot = guard.take(self.cfg.control, self.cfg.state_dir, stamp())
            for run in active.values():         # a red of conductor's making: the item still lands
                run.rec.setdefault("own_changes", []).extend(changes)
                self._save(run)
            return
        taken = self.snapshot.taken
        guard.restore(self.cfg.control, self.cfg.state_dir, self.snapshot)
        running = sorted(active)
        # Pinned on the items whose own writing tool calls named a changed path since the
        # snapshot (`talk.note_touch`, `guard.named`): writing its report, or naming any other
        # file, is no evidence. None did (a change from outside, a script or a relative path
        # that never names the folder, a stage or merge executor, which runs no hook, a call
        # still running): every active item takes it, as before.
        made = [i for i in running if guard.named(talk.touched_since(self.cfg.control, i, taken), changes)]
        blamed = made or running
        self.log.append("tamper", item=blamed[0] if len(blamed) == 1 else None,
                        paths=guard.changed_paths(changes), changes=changes,
                        concurrent=running if len(running) > 1 else [], found=context,
                        attributed=made if made and len(running) > 1 else [])
        for item_id in blamed:
            active[item_id].tamper += changes

    # ---------------------------------------------------------------- requests
    def process_requests(self, between: bool = False) -> None:
        """`between`: called from `schedule` before a launch, so a request whose `request.md`
        landed after the tick began (the bridge writes it after the call's queue files) is
        not left behind a worktree's creation. A config change waits for the next tick."""
        waiting: set[str] = set()   # targets of earlier requests still waiting: stamp order holds for them
        for d in request.pending(self.cfg):
            if between and "conductor.toml" in request.listed_targets(d):
                return                      # it and those after it: the next tick, in order
            rel = f"requests/{d.name}"
            with self._receipt(rel):     # one receipt: the request, its commit, or why it failed
                if waiting and (ahead := request.listed_targets(d) & waiting):
                    waiting |= request.listed_targets(d)
                    self._pending_receipt(rel, f"an earlier request still waiting on {sorted(ahead)}")
                    continue
                try:
                    req = request.ready(d)
                except request.RequestError as exc:
                    self.log.append("request_failed", paths=[rel], request=d.name, error=str(exc))
                    os.replace(d, d.with_name(d.name + request.FAILED_SUFFIX))
                    continue
                if req is None:      # its files still arriving: no receipt yet (pending is a real wait)
                    waiting |= request.listed_targets(d)
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
                    self.control_committed()             # pushed with its burst (daemon_push)
                if request.is_closeout(req.message):     # the session ended: what it saw is read
                    self.log.append("read", paths=[rel], through=self.log.max_id(), closeout=d.name)
                    self.closeout_pushes(d.name)         # and its work goes to the remotes (daemon_push)
                shutil.rmtree(d)
                self.snapshot = guard.take(self.cfg.control, self.cfg.state_dir, stamp())
                if "conductor.toml" in req.targets:
                    try:
                        new = config_mod.load(self.cfg.control)
                        busy = {r.rec["repo"] for r in [*self.runs.values(), *self.stages.values()]}
                        busy |= {p.parent.name for p in self.cfg.path("running").glob("*/*.json")}
                        if gone := sorted(busy - set(new.repos)):
                            raise config_mod.ConfigError(f"repos {gone} still have items in running/")
                        self.cfg = self.runner.cfg = self.picker.cfg = self.opener.cfg = new
                        self.log.append("config_reload", paths=["conductor.toml"])
                    except config_mod.ConfigError as exc:
                        self.log.append("invalid", paths=["conductor.toml"], path="conductor.toml",
                                        error=f"{exc}; still using the previous config")

    # ---------------------------------------------------------------- scheduling
    def schedule(self) -> None:
        view = state.scan(self.cfg, self.log)
        self.check_reviewed_reports(view)
        for d in view.stray:
            key = f"queue/{d.name}"
            if self.invalid_seen.get(key) != "stray":
                self.invalid_seen[key] = "stray"
                self.log.append("invalid", paths=[key], path=key,
                                error="queue folder is not a repo in conductor.toml")
        self._refuse_late_writes(view)
        queued: dict[str, items.Item] = {}
        edited: set[str] = set()
        cancelled: set[str] | None = None

        def dep_warning(item: items.Item) -> dict:
            nonlocal cancelled
            if not item.depends_on:
                return {}
            if cancelled is None:
                cancelled = {e.item for e in self.log.query(kinds=["cancel"]) if e.item}
            warning = state.dependency_warning(view, item, cancelled)
            return {"warning": warning} if warning else {}      # its receipt says so (docs/receipts.md)

        for repo, queue in view.queued.items():
            for item in queue:
                queued[item.id] = item
                if item.id not in self.enqueued:
                    self.enqueued.add(item.id)
                    meta = item.meta()
                    meta["paths_declared"] = meta.pop("paths")      # `paths` is the event's own column
                    if shared := [i for i in items.ref_matches(str(item.num), repo, view.known())
                                  if i != item.id and item.num]:
                        meta["shares_number"] = shared              # its receipt says: name it in full
                    self.log.append("enqueue", item=item.id, paths=[self.cfg.rel(item.path)],
                                    **meta, **dep_warning(item))
                # A queued item may be rewritten until it launches: the launch (and a
                # stage, which a change discards below) reads the file as it is then.
                digest = file_hash(item.path)
                if self.queued_hashes.get(item.id, digest) != digest:
                    edited.add(item.id)
                    self.log.append("edited", item=item.id, paths=[self.cfg.rel(item.path)],
                                    staged=item.id in view.staged, **dep_warning(item))
                self.queued_hashes[item.id] = digest
                if item.errors:
                    # Per file version and reason: a `resume:` item's reason moves with the queue.
                    key = f"{item.id}:{item.path.stat().st_mtime}:{'; '.join(item.errors)}"
                    if self.invalid_seen.get(item.id) != key:
                        self.invalid_seen[item.id] = key
                        self.log.append("invalid", item=item.id, paths=[self.cfg.rel(item.path)],
                                        error="; ".join(item.errors))
        self.queued_hashes = {i: h for i, h in self.queued_hashes.items() if i in queued}
        # A stage belongs to the item file it read; a removed item drops it, and a rewritten
        # one (staging or staged) is staged again from its new text.
        for item_id, srec in list(view.staged.items()):
            item = queued.get(item_id)
            if item_id in view.running:
                # A resume launch stopped between moving the item and dropping the record.
                self._stage_run(srec).rec_path.unlink(missing_ok=True)
                view.staged.pop(item_id)
            elif item is None:
                self._discard_stage(self.stages.get(item_id) or self._stage_run(srec),
                                    "the item left the queue after staging")
                view.staged.pop(item_id)
            elif file_hash(item.path) != srec.get("item_hash"):
                if item_id not in edited:         # rewritten while the daemon was down
                    self.log.append("edited", item=item_id, paths=[self.cfg.rel(item.path)], staged=True)
                self._discard_stage(self.stages.get(item_id) or self._stage_run(srec),
                                    f"the item file changed while {srec.get('phase', 'staged')}",
                                    then="it starts again from the new text")
                self.no_stage.discard(item_id)
                view.staged.pop(item_id)
        # After the enqueues (an amend file written with its item finds it queued) and the
        # stage checks (an amendment never discards a stage; an item change still does).
        amending = self.process_amends(view)
        reordering = self._reorder_pending()      # its items are under temp names: wait
        decisions = state.decide(self.cfg, view)
        for repo, d in decisions.items():
            if repo == reordering:
                continue
            if self.mem_level != "ok" and d.action in ("run", "resume", "stage"):
                continue                            # memory pressure: nothing new starts (the `memory` event says so)
            if d.action in ("run", "resume") and d.item.id in amending:
                continue                            # an amend file still being written: next tick
            if d.action == "hold" and d.item and self.holds.get(d.item.id) != d.reason:
                self.holds[d.item.id] = d.reason
                self.log.append("hold", item=d.item.id, paths=[self.cfg.rel(d.item.path)], reason=d.reason)
            elif d.action == "run":
                self._between_requests(self.launch, d.item)
            elif d.action == "resume":
                self._between_requests(self.resume, d.item, view.staged[d.item.id])
            elif d.action == "stage" and d.item.id not in self.no_stage and d.item.id not in self.stages:
                self._between_requests(self.stage, d.item)
        # A readonly or alongside item behind its head starts now; its neighbours keep their order and stages.
        for item in state.early_starts(self.cfg, view, decisions) if self.mem_level == "ok" else []:
            if item.repo != reordering and item.id not in amending:
                self._between_requests(self.launch, item)

    def _between_requests(self, start, *args) -> None:
        """A launch, resume or stage takes seconds (a worktree's creation): a request that
        landed since the tick began is handled before it, and one landing during it after."""
        self.process_requests(between=True)
        start(*args)
        self.process_requests(between=True)

    def _refuse_late_writes(self, view: state.View) -> None:
        """A queue file named for an item that already launched (a rewrite that lost the race
        with its launch, or a reused name) can never reach it. It is set aside as `.bad`
        with an `invalid` event, never left to hold its repo's queue."""
        for repo, queue in view.queued.items():
            late = [i for i in queue if i.id in view.running or i.id in view.done]
            for item in late:
                queue.remove(item)
                try:
                    if time.time() - item.path.stat().st_mtime < SIGNAL_SETTLE_SECONDS:
                        continue                    # still being written: set aside once settled
                    bad = item.path.with_name(item.path.name + ".bad")
                    retry_io(os.replace, item.path, bad)
                except OSError:
                    continue
                where = "running" if item.id in view.running else "finished"
                self.log.append("invalid", item=item.id, paths=[self.cfg.rel(bad)], path=self.cfg.rel(bad),
                                error=f"{item.id} already launched ({where}), so this write to its queue file "
                                      f"can't reach it; set aside as {bad.name}. A running item takes tell or "
                                      "amend; a change to finished work is a new item")

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
        """Create the item's worktree into `rec`, without setup (`daemon_setup` runs it);
        the error, if it failed."""
        try:
            made = worktrees.create(self.cfg, item.repo, item.name, setup_log, setup=False)
        except (worktrees.WorktreeError, git.GitError, OSError) as exc:
            return f"worktree: {exc}"
        rec.update(worktree=str(made.path), branch=made.branch, base=made.base, shared=made.linked)
        self._worktree_event(rec, "created", "; ".join(made.notes + made.warnings))
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
            **item.meta(), "max_hours": max_hours, "phase": "exec",   # after meta: the item's may be None
            "report": str(paths.report), "log": str(paths.log), "stderr": str(paths.stderr),
            "prompt": str(paths.prompt),
            "worktree": None, "branch": None, "base": None, "shared": [], "staged": bool(stage),
            "pid": None, "create_time": None,
        }
        if item.template:
            rec["template_version"] = audits.template_version(item.template)
        if item.template == "cleanup":
            # Claimed in the run record from the first save, so no other cleanup takes them.
            rec["notes"] = self._free_notes(item.repo)        # a cleanup never stages
        run = Run(rec, rec_path, md_path)
        # Durable order: record, then worktree, then move, then launch, then the pid. A
        # restart that finds no pid and no live process with this session id requeues it.
        self._save(run)
        error = None
        if stage:
            rec.update(worktree=stage["worktree"], branch=stage["branch"], shared=stage.get("shared", []))
            wt = Path(stage["worktree"])
            try:
                rec["base"] = worktrees.move_to(cfg, item.repo, wt)
            except (git.GitError, OSError) as exc:
                error = f"moving the staged worktree to {cfg.opts(item.repo).main}: {exc}"
            landed = git.commits_between(repo_path, stage["base"], rec["base"]) if not error else []
            rec["resume"] = {"old": stage["base"], "new": rec["base"] or stage["base"], "landed": len(landed)}
            self._worktree_event(rec, "moved", f"{stage['base'][:10]} → {(rec['base'] or '?')[:10]}")
        elif cfg.opts(item.repo).worktrees or item.resume_of or item.template == "watch":   # these: always one
            error = self._make_worktree(item, rec, paths.setup)
        if item.template == "cleanup" and not rec["notes"] and not error:
            error = f"cleanup: no unused audit notes for {item.repo}"
        if item.resume_of and not error:
            error = resume_items.prepare(cfg, self.log, rec)
        rec["repo_head"] = rec["base"] or git.head(repo_path)
        rec["repo_status"] = None
        if not error:
            try:
                rec["port"] = self._free_port()
            except OSError as exc:
                error = f"port: {exc}"
        self._save(run)
        if (seen := self.queued_hashes.get(item.id)) and (now_hash := file_hash(item.path)) != seen:
            self._launch_raced(run, item, stage, now_hash)
            return
        retry_io(os.replace, item.path, md_path)
        if stage:
            retry_io(Path(self._stage_run(stage).rec_path).unlink, True)
        paths.report.parent.mkdir(parents=True, exist_ok=True)
        for stale in (paths.report, paths.log, paths.stderr):
            if stale.exists():
                stale.unlink()
        self.runs[item.id] = run
        if not error and rec["worktree"] and cfg.opts(item.repo).setup and item.template != "watch":
            # Setup runs off the loop; `check_runs` starts the executor once it is done. A watch
            # only reads: it skips setup (an `npm ci` would cost it minutes).
            rec["phase"] = "setup"
            error = self._start_setup(run, "run", paths.setup)
            if not error:
                return
            rec["phase"] = "exec"
        if error:
            self.log.append("start", item=item.id, paths=[cfg.rel(md_path)], session_id=rec["session_id"],
                            pid=None, error=error)
            self.finish(run, launch_error=error)
            return
        self._start_exec(run, item)

    def _launch_raced(self, run: Run, item: items.Item, stage: dict | None, digest: str | None) -> None:
        """A rewrite landed between this tick's scan and the move into `running/`: undo the
        launch (nothing has started) so the next tick launches the new text, its stage gone."""
        rec = run.rec
        if stage:
            self._discard_stage(self._stage_run(stage), "the item file changed while it was launching")
        elif rec.get("worktree"):
            self._worktree_op(rec, "discarded", "the item file changed while launching")
        retry_io(run.rec_path.unlink, True)
        self.queued_hashes[item.id] = digest
        self.log.append("edited", item=item.id, paths=[self.cfg.rel(item.path)], staged=bool(stage),
                        launching=True)

    def _free_port(self) -> int:
        """The port an executor's servers use (`CONDUCTOR_PORT`): free now, and never a
        run target's, another live item's or the picker's."""
        avoid = targets.ports(self.cfg) | {r.rec["port"] for r in self.runs.values() if r.rec.get("port")}
        if self.picker.port:
            avoid.add(self.picker.port)
        return procs.free_port(avoid)

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
            "item_hash": file_hash(item.path),
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
        if cfg.opts(item.repo).setup:
            # Setup runs off the loop; `check_stages` starts the stage once it is done.
            self.stages[item.id] = run
            if error := self._start_setup(run, "stage", paths.setup):
                self._discard_stage(run, f"could not stage: {error}")
            return
        self._start_stage(run, item)

    def check_stages(self) -> None:
        for run in list(self.stages.values()):
            if run.setting_up:
                self._poll_stage_setup(run)
            elif run.exited():
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
        procs.release_job(rec.get("pid"), rec.get("create_time"))
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

    def _discard_stage(self, run: Run, reason: str, then: str = "the item launches fresh") -> None:
        rec = run.rec
        if run.setting_up:
            self._end_setup(run, reason)         # kills the runner
        elif run.pid() and not run.exited():
            procs.kill_tree(run.pid())
        self._reap(rec["item"], rec.get("worktree"), rec.get("started"), "its stage was discarded",
                   job=(rec.get("pid"), rec.get("create_time")))
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
        self.log.append("warning", item=rec["item"], note=f"stage discarded: {reason}; {then}")

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
            if run.rec.get("phase") == "setup":
                self._poll_run_setup(run)
            elif run.exited():
                self.finish(run)
            elif now() > parse_stamp(run.rec["deadline"]) and not run.rec.get("paused_at") and (
                    run.item_id not in self.blocked or run.rec.get("template") == "watch"):
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
        self._reap(item_id, rec.get("worktree"), rec.get("started"), "its executor exited",
                   job=(rec.get("pid"), rec.get("create_time")))
        self.mem_told.pop(item_id, None)
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
                changed = [f for f in git.changed_files(cwd, rec["repo_head"], after_head)
                           if not cfg.opts(repo).is_prose(f)]          # prose is outside `paths`
                outside = globs.outside(changed, rec["paths"])
                if outside:
                    reasons.append(f"paths violation: changed {len(outside)} file(s) outside `paths`")
                    self.log.append("paths_violation", item=item_id, files=outside[:50],
                                    paths_declared=rec["paths"], branch=rec.get("branch"))
        report_status = header.get("status") if header and not problem else None
        if report_status == "red" and not reasons:
            reasons.append("report says red")
        status = "red" if reasons or report_status is None else report_status
        # Conductor's own doing (`_guard_check`) is no red of the executor's: the item still lands.
        own = [f"conductor's own git work changed the control folder ({'; '.join(rec['own_changes'][:3])})"
               ] if rec.get("own_changes") else []
        rec["outcome"] = {
            "status": status, "report_status": report_status, "reasons": reasons + own * (status == "red"),
            "conductor": own,
            "blocks": (header or {}).get("blocks"), "carry": (header or {}).get("carry"),
            "slow": (header or {}).get("slow"), "pick": _pick_ref(repo, (header or {}).get("pick")),
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

    def _compact_logs(self, item_ids) -> None:
        """Shrink finished items' run logs (`executor.compact_log`): after each finish,
        and at daemon start for every finished item, which also catches logs written
        before compaction existed."""
        for item_id in item_ids:
            if item_id in self.runs or item_id in self.stages:
                continue
            paths = executor.RunPaths.for_name(self.cfg, *item_id.split("/", 1))
            for log in (paths.log, paths.stage_log, paths.merge_log):
                try:
                    executor.compact_log(log)
                except OSError as exc:
                    self.log.append("warning", item=item_id, paths=[self.cfg.rel(log)],
                                    note=f"could not compact {log.name}: {exc!r}")

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
