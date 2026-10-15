"""The daemon: one polling loop per control folder.

Each tick: signals → requests → running executors → schedule → inbox. Single-threaded;
executors are separate processes the loop polls, so several repos run at once.
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

from . import config as config_mod
from . import executor, git, guard, inbox, items, procs, request, state
from .config import RUNTIME, Config
from .events import Event, EventLog
from .util import clock, now, parse_stamp, read_text, stamp, write_text

INBOX_REFRESH_SECONDS = 30
SIGNAL_SETTLE_SECONDS = 1.0


class DaemonError(RuntimeError):
    pass


@dataclass
class Run:
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


class Daemon:
    def __init__(self, cfg: Config, echo=print):
        self.cfg = cfg
        self.echo = echo
        self.log = EventLog(cfg.events_db, on_append=self._echo_event)
        self.runs: dict[str, Run] = {}
        self.snapshot: guard.Snapshot | None = None
        self.enqueued = {e.item for e in self.log.query(kinds=["enqueue"])}
        self.holds = {e.item: e.detail.get("reason") for e in self.log.query(kinds=["hold"])}
        self.invalid_seen: dict[str, str] = {}
        self._inbox_key: tuple | None = None
        self._inbox_at = 0.0
        self._lock_file = cfg.state_dir / "daemon.json"

    # ---------------------------------------------------------------- lifecycle
    def _echo_event(self, ev: Event) -> None:
        if self.echo:
            self.echo(f"{clock(ev.ts)} #{ev.id} {inbox.describe(ev)}")

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
        self.recover()
        if not self.runs:
            if self.snapshot and (changes := guard.diff(self.cfg.control, self.snapshot)):
                self.log.append("drift", paths=guard.changed_paths(changes), changes=changes,
                                note="control folder changed while conductor was not running; accepted")
            self.snapshot = guard.take(self.cfg.control, self.cfg.state_dir, stamp())
        elif self.snapshot is None:
            self.snapshot = guard.take(self.cfg.control, self.cfg.state_dir, stamp())

    def _check_ignores(self) -> None:
        probes = [n if "." in n and n != ".conductor" else f"{n}/probe" for n in RUNTIME]
        missing = [p.split("/")[0] for p in probes
                   if subprocess.run(["git", "-C", str(self.cfg.control), "check-ignore", "-q", p]).returncode]
        if missing:
            self.log.append("warning", note=f"not git-ignored in the control folder: {missing}")

    def run_forever(self) -> None:
        self.acquire()
        try:
            self.startup()
            while True:
                self.tick()
                time.sleep(self.cfg.poll_seconds)
        except KeyboardInterrupt:
            self.log.append("daemon_stop", running=sorted(self.runs),
                            note="executors keep running; the next start adopts them")
        finally:
            self.write_inbox(force=True)
            self.release()

    def tick(self) -> None:
        self.process_signals()
        self.process_requests()
        self.check_runs()
        self.schedule()
        self.write_inbox()

    # ---------------------------------------------------------------- recovery
    def recover(self) -> None:
        """Restart rule: never re-run. Adopt a live executor, harvest a finished one,
        requeue one that provably never launched, and mark anything else red."""
        for repo in self.cfg.repos:
            rdir = self.cfg.path("running", repo)
            if not rdir.is_dir():
                continue
            for rec_path in sorted(rdir.glob("*.run.json")):
                md = rec_path.with_name(rec_path.name.removesuffix(".run.json") + ".md")
                if not md.is_file():
                    rec_path.unlink()       # launch died before the item moved; it is still queued
                    continue
                rec = state.read_run(rec_path)
                run = Run(rec, rec_path, md)
                item_id = rec["item"]
                if self.log.last("finish", item=item_id):
                    self._move_to_done(run)       # finish was logged; only the move was lost
                    continue
                if rec.get("pid") is None and not procs.find_by_marker(rec["session_id"]):
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

    # ---------------------------------------------------------------- guard
    def _guard_check(self, context: str) -> None:
        """Compare the control folder to the snapshot while executors run; revert and
        pin the change on the running items."""
        if not self.snapshot:
            return
        changes = guard.diff(self.cfg.control, self.snapshot)
        if not changes:
            return
        if not self.runs:
            self.log.append("drift", paths=guard.changed_paths(changes), changes=changes,
                            note=f"changed while no executor ran ({context}); accepted")
            self.snapshot = guard.take(self.cfg.control, self.cfg.state_dir, stamp())
            return
        guard.restore(self.cfg.control, self.cfg.state_dir, self.snapshot)
        running = sorted(self.runs)
        self.log.append("tamper", item=running[0] if len(running) == 1 else None,
                        paths=guard.changed_paths(changes), changes=changes,
                        concurrent=running if len(running) > 1 else [], found=context)
        for run in self.runs.values():
            run.tamper += changes

    # ---------------------------------------------------------------- signals
    def process_signals(self) -> None:
        sdir = self.cfg.path("signals")
        if not sdir.is_dir():
            return
        for f in sorted(p for p in sdir.iterdir() if p.is_file()):
            if f.suffix in (".bad", ".tmp") or time.time() - f.stat().st_mtime < SIGNAL_SETTLE_SECONDS:
                continue
            try:
                lines = read_text(f).splitlines()
                pairs = [ln.split(":", 1) for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
                pairs = [(k.strip().lower(), v.strip()) for k, v in pairs]
                if not pairs:
                    raise ValueError("empty signal")
                for key, value in pairs:
                    if key == "read":
                        top = self.log.max_id()
                        through = top if value.lower() in ("latest", "all") else int(value.lstrip("#"))
                        through = max(min(through, top), self.log.read_mark())
                        self.log.append("read", paths=[f"signals/{f.name}"], through=through)
                    elif key == "clear":
                        if value not in self.cfg.repos:
                            raise ValueError(f"clear: {value!r} is not a repo")
                        self.log.append("clear", repo=value, paths=[f"signals/{f.name}"])
                    else:
                        raise ValueError(f"unknown signal key {key!r}")
                f.unlink()
            except (ValueError, UnicodeDecodeError) as exc:
                self.log.append("invalid", paths=[f"signals/{f.name}"], path=f"signals/{f.name}", error=str(exc))
                os.replace(f, f.with_name(f.name + ".bad"))

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
                    self.cfg = config_mod.load(self.cfg.control)
                    self.log.append("config_reload", paths=["conductor.toml"])
                except config_mod.ConfigError as exc:
                    self.log.append("invalid", paths=["conductor.toml"], path="conductor.toml",
                                    error=f"{exc}; still using the previous config")

    # ---------------------------------------------------------------- scheduling
    def schedule(self) -> None:
        view = state.scan(self.cfg, self.log)
        for d in view.stray:
            key = f"queue/{d.name}"
            if self.invalid_seen.get(key) != "stray":
                self.invalid_seen[key] = "stray"
                self.log.append("invalid", paths=[key], path=key,
                                error="queue folder is not a repo in conductor.toml")
        for repo, queue in view.queued.items():
            for item in queue:
                if item.id not in self.enqueued:
                    self.enqueued.add(item.id)
                    self.log.append("enqueue", item=item.id, paths=[self.cfg.rel(item.path)], **item.meta())
                if item.errors:
                    key = f"{item.id}:{item.path.stat().st_mtime}"
                    if self.invalid_seen.get(item.id) != key:
                        self.invalid_seen[item.id] = key
                        self.log.append("invalid", item=item.id, paths=[self.cfg.rel(item.path)],
                                        error="; ".join(item.errors))
        for repo, d in state.decide(self.cfg, view).items():
            if d.action == "hold" and d.item and self.holds.get(d.item.id) != d.reason:
                self.holds[d.item.id] = d.reason
                self.log.append("hold", item=d.item.id, paths=[self.cfg.rel(d.item.path)], reason=d.reason)
            elif d.action == "run":
                self.launch(d.item)

    def launch(self, item: items.Item) -> None:
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
            "session_id": str(uuid.uuid4()), "started": stamp(started), "deadline": stamp(deadline),
            "max_hours": max_hours, **item.meta(),
            "report": str(paths.report), "log": str(paths.log), "stderr": str(paths.stderr),
            "prompt": str(paths.prompt),
            "repo_head": git.head(repo_path),
            "repo_status": git.tracked_status(repo_path) if item.readonly else None,
            "pid": None, "create_time": None,
        }
        # Durable order: record, then move, then launch, then record the pid. A restart
        # that finds no pid and no live process with this session id requeues the item.
        write_text(rec_path, json.dumps(rec, indent=1))
        os.replace(item.path, md_path)
        paths.report.parent.mkdir(parents=True, exist_ok=True)
        for stale in (paths.report, paths.log, paths.stderr):
            if stale.exists():
                stale.unlink()
        write_text(paths.prompt, executor.build_prompt(cfg, item, paths, max_hours, deadline))
        run = Run(rec, rec_path, md_path)
        self.runs[item.id] = run
        try:
            run.popen = procs.spawn(executor.command(cfg, rec["session_id"]), repo_path,
                                    paths.prompt, paths.log, paths.stderr)
        except OSError as exc:
            self.log.append("start", item=item.id, paths=[cfg.rel(md_path)], session_id=rec["session_id"],
                            pid=None, error=str(exc))
            self.finish(run, launch_error=str(exc))
            return
        rec["pid"] = run.popen.pid
        rec["create_time"] = procs.create_time(run.popen.pid)
        write_text(rec_path, json.dumps(rec, indent=1))
        self.log.append("start", item=item.id,
                        paths=[cfg.rel(md_path), cfg.rel(paths.report), cfg.rel(paths.log)],
                        session_id=rec["session_id"], pid=run.popen.pid, deadline=rec["deadline"])

    # ---------------------------------------------------------------- runs
    def check_runs(self) -> None:
        for run in list(self.runs.values()):
            if run.exited():
                self.finish(run)
            elif now() > parse_stamp(run.rec["deadline"]):
                pid = run.pid()
                procs.kill_tree(pid)
                self.log.append("kill", item=run.item_id, pid=pid,
                                reason=f"max_hours {run.rec['max_hours']:g} reached")
                self.finish(run, killed=True)

    def finish(self, run: Run, killed: bool = False, interrupted: bool = False,
               launch_error: str | None = None) -> None:
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
        after_head = git.head(repo_path) if repo_path and git.is_repo(repo_path) else None
        if rec.get("readonly") and repo_path:
            if after_head != rec.get("repo_head") or git.tracked_status(repo_path) != rec.get("repo_status"):
                reasons.append("readonly item changed the repo")
        for sha, subject in reversed(git.commits_between(repo_path, rec.get("repo_head"), after_head)
                                     if repo_path else []):
            self.log.append("commit", item=item_id, scope=repo, sha=sha, subject=subject)
        report_status = header.get("status") if header and not problem else None
        if report_status == "red" and not reasons:
            reasons.append("report says red")
        status = "red" if reasons or report_status is None else report_status
        done_md = f"done/{repo}/{rec['name']}.md"
        self.log.append(
            "finish", item=item_id, paths=[cfg.rel(Path(rec["report"])), cfg.rel(Path(rec["log"])), done_md],
            status=status, report_status=report_status, reasons=reasons,
            blocks=(header or {}).get("blocks"), carry=(header or {}).get("carry"),
            session_id=rec["session_id"], exit_code=exit_code,
            cost_usd=(result or {}).get("total_cost_usd"), turns=(result or {}).get("num_turns"),
            needs_planner=rec.get("needs_planner", False), after_red=rec.get("after_red", "stop"),
        )
        if status == "red":
            self.log.append("red", item=item_id, paths=[cfg.rel(Path(rec["report"]))],
                            reasons=reasons, after_red=rec.get("after_red", "stop"))
        self._move_to_done(run)
        self.runs.pop(item_id, None)

    def _move_to_done(self, run: Run) -> None:
        ddir = self.cfg.path("done", run.rec["repo"])
        ddir.mkdir(parents=True, exist_ok=True)
        if run.rec_path.exists():
            os.replace(run.rec_path, ddir / run.rec_path.name)
        if run.md_path.exists():
            os.replace(run.md_path, ddir / run.md_path.name)

    # ---------------------------------------------------------------- inbox
    def write_inbox(self, force: bool = False) -> None:
        key = (self.log.max_id(), self.log.read_mark())
        if not force and key == self._inbox_key and time.time() - self._inbox_at < INBOX_REFRESH_SECONDS:
            return
        view = state.scan(self.cfg, self.log)
        write_text(self.cfg.path("inbox.md"), inbox.render(self.cfg, self.log, view))
        self._inbox_key, self._inbox_at = key, time.time()
