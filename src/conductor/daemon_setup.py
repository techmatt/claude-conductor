"""A repo's setup commands, off the loop: spawned as a process of their own (`setup_run`)
and polled each tick, so signals, requests and other repos keep moving while they run.

A run record in setup has `phase: setup`; a stage record keeps `phase: staging`. Both
carry `setup` = {state (running | ok | failed), commands, spec, result, pid, create_time,
started, finished, error}. A restart never adopts a setup: it kills the runner, discards
the worktree and requeues the item (a stage is discarded), since nothing has run yet.
A run whose setup succeeded is saved as `phase: exec` (pid None) before its executor
spawns, so a crash after the spawn is adopted by session marker like any exec run.

Here too: the second halves of a launch and of a stage (`_start_exec`, `_start_stage`),
run once setup is done, or at once by `launch` / `stage` when the repo has none.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from . import executor, git, items, procs, talk, worktrees
from . import resume as resume_items           # `resume` here is a staged item's move record
from .util import file_hash, now, parse_stamp, retry_io, stamp, write_text

if TYPE_CHECKING:
    import psutil

    from .daemon import Run


class SetupMixin:
    def _setup_files(self, rec: dict, kind: str) -> tuple[Path, Path]:
        """(spec, result) for a run's or a stage's setup, in `.conductor/setup/`."""
        base = f"{rec['repo']}__{rec['name']}.{kind}"
        d = self.cfg.state_dir / "setup"
        return d / f"{base}.json", d / f"{base}.result.json"

    def _start_setup(self, run: Run, kind: str, log: Path) -> str | None:
        """Spawn the setup runner in the run's (or stage's) worktree; the error, if it could
        not start. The record names the spec before the spawn, so a restart that finds no
        pid still finds the runner by its command line."""
        rec = run.rec
        commands = list(self.cfg.opts(rec["repo"]).setup)
        spec, result = self._setup_files(rec, kind)
        result.unlink(missing_ok=True)
        write_text(spec, json.dumps({"commands": commands, "cwd": rec["worktree"], "log": str(log),
                                     "minutes": self.cfg.setup_minutes, "result": str(result)}, indent=1))
        rec["setup"] = {"state": "running", "commands": commands, "spec": str(spec), "result": str(result),
                        "log": str(log), "pid": None, "create_time": None, "started": stamp()}
        self._save(run)
        log.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Its cwd is the state folder, never the worktree: `-m` puts the cwd on sys.path.
            run.setup = procs.spawn([procs.console_python(), "-m", "conductor.setup_run", str(spec)],
                                    self.cfg.state_dir, spec, log, log)
        except OSError as exc:
            self._end_setup(run, f"setup could not start: {exc}")
            return rec["setup"]["error"]
        rec["setup"] |= {"pid": run.setup.pid, "create_time": procs.create_time(run.setup.pid)}
        self._save(run)
        self.log.append("setup", item=rec["item"], paths=[self.cfg.rel(log)], commands=commands,
                        pid=run.setup.pid, stage=kind == "stage")
        return None

    def _setup_outcome(self, run: Run) -> tuple[bool, str | None]:
        """(finished, error) of a running setup."""
        s = run.rec["setup"]
        result = Path(s["result"])
        gone = (run.setup.poll() is not None) if run.setup else not procs.alive(s.get("pid"), s.get("create_time"))
        if result.is_file():
            try:
                return True, json.loads(result.read_text(encoding="utf-8")).get("error")
            except (OSError, ValueError) as exc:
                return True, f"setup result unreadable: {exc}"
        if gone:
            code = run.setup.returncode if run.setup else None
            return True, f"setup runner exited without a result (exit code {code}; see {Path(s['log']).name})"
        return False, None

    def _end_setup(self, run: Run, error: str | None) -> None:
        """Setup is over (finished, failed or abandoned): the runner is gone, its files removed."""
        s = run.rec["setup"]
        if run.setup is None:
            self._kill_setup(run.rec)               # one read back from disk, if still alive
        elif run.setup.poll() is None:
            procs.kill_tree(run.setup.pid)
        s |= {"state": "failed" if error else "ok", "finished": stamp(), "error": error}
        run.setup = None
        for p in (s["spec"], s["result"]):
            Path(p).unlink(missing_ok=True)
        self._save(run)

    def _kill_setup(self, rec: dict) -> None:
        """Kill a setup runner this process did not start (or lost): by pid, else by its spec path."""
        s = rec.get("setup") or {}
        if procs.alive(s.get("pid"), s.get("create_time")):
            procs.kill_tree(s["pid"])
        elif s.get("spec") and (p := procs.find_by_marker(s["spec"])):
            procs.kill_tree(p.pid)
        if s:
            s["state"] = "killed"           # no longer setting up: its pid is nobody's to kill
        for p in (s.get("spec"), s.get("result")):
            if p:
                Path(p).unlink(missing_ok=True)

    def _setup_seconds(self, rec: dict) -> dict:
        s = rec.get("setup") or {}
        if not s.get("finished"):
            return {}
        return {"setup_seconds": round((parse_stamp(s["finished"]) - parse_stamp(s["started"])).total_seconds())}

    def _poll_run_setup(self, run: Run) -> None:
        """A run in setup: on success its executor starts (`_start_exec`); a failure, a lost
        runner or setup past max_hours is a launch error, as a blocking setup's was."""
        rec = run.rec
        done, error = self._setup_outcome(run)
        if not done:
            if now() <= parse_stamp(rec["deadline"]):
                return
            error = f"setup ran past max_hours {rec.get('max_hours') or self.cfg.max_hours:g}"
        if not error:
            # Saved as exec (pid None) before the spawn: a crash or a lost save after it is
            # an exec record, adopted by its session marker, never requeued to run twice.
            rec["phase"] = "exec"
        self._end_setup(run, error)
        if error:
            self.log.append("start", item=run.item_id, paths=[self.cfg.rel(run.md_path)], session_id=rec["session_id"],
                            pid=None, error=error)
            self.finish(run, launch_error=error)
            return
        self._start_exec(run, items.parse(run.md_path, rec["repo"]))

    def _poll_stage_setup(self, run: Run) -> None:
        """A stage in setup: on success the staging executor starts; anything else
        discards the stage (the item launches fresh)."""
        rec = run.rec
        done, error = self._setup_outcome(run)
        if not done:
            if now() <= parse_stamp(rec["deadline"]):
                return
            error = f"setup ran past stage_minutes ({self.cfg.stage_minutes:g})"
        self._end_setup(run, error)
        if error:
            self._discard_stage(run, f"could not stage: {error}")
        elif not run.md_path.is_file():
            self._discard_stage(run, "the item left the queue during setup")
        elif file_hash(run.md_path) != rec.get("item_hash"):
            self._discard_stage(run, "the item file changed during setup", then="it starts again from the new text")
            self.no_stage.discard(rec["item"])
        else:
            self._start_stage(run, items.parse(run.md_path, rec["repo"]))

    def _recover_setup_run(self, run: Run) -> None:
        """Restart rule for a run caught in setup: it never ran, so it is requeued. Unless its
        executor did start (a record from before the save-as-exec order, or one whose save was
        lost): that run is adopted or harvested as an exec run would be. A locked file in the
        worktree is a warning and a leftover, never a startup crash."""
        rec, repo = run.rec, run.rec["repo"]
        proc = procs.find_by_marker(rec["session_id"])
        if proc is not None or (rec.get("log") and Path(rec["log"]).exists()):
            rec["phase"] = "exec"
            self._save(run)
            self._recover_spawned(run, proc)
            return
        self._kill_setup(rec)
        if rec.get("worktree"):
            self._reap(rec["item"], rec["worktree"], rec.get("started"), "daemon stopped during setup")
            self._worktree_op(rec, "discarded", "daemon stopped during setup; requeued")
        retry_io(os.replace, run.md_path, self.cfg.path("queue", repo, run.md_path.name))
        run.rec_path.unlink()
        self.log.append("requeue", item=rec["item"], paths=[f"queue/{repo}/{run.md_path.name}"],
                        reason="daemon stopped during setup")

    def _recover_spawned(self, run: Run, proc: psutil.Process | None) -> None:
        """Restart rule for a run whose executor spawned: adopt it while it lives, else
        harvest its result, else finish it as interrupted. `recover` and setup's both."""
        rec, item_id = run.rec, run.rec["item"]
        self.runs[item_id] = run
        if proc is not None:
            run.proc = proc
            self.log.append("adopt", item=item_id, pid=proc.pid, session_id=rec["session_id"])
        elif executor.read_result(Path(rec["log"])) is not None:
            self.log.append("recover", item=item_id, note="executor finished while conductor was down")
            self.finish(run)
        else:
            self.finish(run, interrupted=True)

    # ---------------------------------------------------------------- second halves
    def _start_exec(self, run: Run, item: items.Item) -> None:
        """The second half of a launch, once the worktree and its setup are ready: the
        prompt (with any amendments written meanwhile), then the executor."""
        cfg, rec = self.cfg, run.rec
        paths = executor.RunPaths.for_item(cfg, item)
        stage, resume = rec.get("staged"), rec.get("resume")
        cwd = Path(rec["worktree"]) if rec["worktree"] else cfg.repos[item.repo]
        rec["repo_status"] = git.tracked_status(cwd) if item.readonly else None
        # Amendments written before the launch: into the prompt, or the resume message.
        amends = talk.take(cfg.control, item.id, "resume" if stage else "prompt")
        rec["beside"], warn = self._beside(item)
        rec["warned"] = [i for i, _ in warn]        # named in its paragraph: no tell about them later
        resumed = resume_items.paragraph(cfg, self.log, rec, paths.original) if rec.get("resume_of") else ""
        write_text(paths.prompt, executor.build_prompt(cfg, item, paths, item.max_hours or cfg.max_hours,
                                                       parse_stamp(rec["deadline"]), rec, resume,
                                                       notes=self._notes_text(rec), usage=self._disk_usage(rec),
                                                       amends=[m for m in amends if talk.is_amend(m)], beside=warn,
                                                       resumed=resumed))
        write_text(paths.mcp, executor.mcp_config(cfg, item, rec["session_id"]))
        write_text(paths.settings, executor.hook_settings(cfg, item))
        md_path = run.md_path
        try:
            run.popen = procs.spawn(executor.command(cfg, rec["session_id"], paths.mcp, resume=bool(stage),
                                                     settings=paths.settings, fork_of=rec.get("resume_session")),
                                    cwd, paths.prompt, paths.log, paths.stderr, env={executor.PORT_VAR: str(rec["port"])})
        except OSError as exc:
            self.log.append("start", item=item.id, paths=[cfg.rel(md_path)], session_id=rec["session_id"],
                            pid=None, error=str(exc))
            self.finish(run, launch_error=str(exc))
            return
        rec["pid"] = run.popen.pid
        rec["create_time"] = procs.create_time(run.popen.pid)
        self._save(run)
        extra = {"worktree": rec["worktree"], "branch": rec["branch"]} if rec["worktree"] else {}
        extra["port"] = rec["port"]
        if item.template:
            extra |= {"template": item.template, "template_version": rec["template_version"]}
        if rec.get("notes"):
            extra["notes"] = rec["notes"]
        if resume:
            extra |= {"resumed": True, "landed": resume["landed"]}
        if item.alongside:
            extra["alongside"] = item.alongside
        if rec.get("resume_of"):
            extra |= {"resume_of": rec["resume_of"], "resume_session": rec["resume_session"]}
        if rec["beside"]:
            extra["beside"] = rec["beside"]
        extra |= self._setup_seconds(rec)
        self.log.append("start", item=item.id,
                        paths=[cfg.rel(md_path), cfg.rel(paths.report), cfg.rel(paths.log)],
                        session_id=rec["session_id"], pid=run.popen.pid, deadline=rec["deadline"], **extra)
        # The other side: each executing item it starts beside now runs beside it too (one in
        # setup works its own list out), and each it was warned about hears of it, unless its
        # own Beside-you paragraph already named this one (it was in setup or staged then).
        for other in rec["beside"]:
            o = self.runs.get(other)
            if o and o.rec.get("phase", "exec") == "exec" and item.id not in o.rec.setdefault("beside", []):
                o.rec["beside"].append(item.id)
                self._save(o)
        for other, _ in warn:
            o = self.runs.get(other)
            if (o and o.rec.get("phase", "exec") == "exec" and not o.exited()
                    and item.id not in o.rec.get("warned", [])):
                text = executor.BESIDE_TELL.format(item=item.id, gist=item.gist())
                ev = self.log.append("tell", item=other, text=text, by="conductor", beside=item.id)
                talk.post(cfg.control, other, ev.id, text, "conductor")

    def _beside(self, item: items.Item) -> tuple[list[str], list[tuple[str, str]]]:
        """What `item` starts beside in its repo: the ids of the items executing or in
        setup there, and the (id, gist) of each one, staged ones too, it is warned about:
        those where either is `alongside`. A pair sharing only by readonly or `paths`
        hears nothing, as before `alongside`."""
        repo = item.repo
        others = {r.item_id: r.md_path for r in self.runs.values() if r.rec["repo"] == repo
                  and r.item_id != item.id and r.rec.get("phase", "exec") in ("setup", "exec")}
        beside = sorted(others)
        rdir = self.cfg.path("running", repo)
        for spath in sorted(rdir.glob("*.stage.json")) if rdir.is_dir() else []:
            name = spath.name.removesuffix(".stage.json")
            others.setdefault(f"{repo}/{name}", self.cfg.path("queue", repo, f"{name}.md"))
        others.pop(item.id, None)
        warn = []
        for other, md in sorted(others.items()):
            o = items.parse(md, repo) if md.is_file() else None
            if o is not None and (item.alongside or o.alongside):
                warn.append((other, o.gist()))
        return beside, warn

    def _start_stage(self, run: Run, item: items.Item) -> None:
        """The second half of staging, once the worktree and its setup are ready."""
        cfg, rec = self.cfg, run.rec
        paths = executor.RunPaths.for_item(cfg, item)
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
                        pid=run.popen.pid, worktree=rec["worktree"], deadline=rec["deadline"],
                        **self._setup_seconds(rec))
