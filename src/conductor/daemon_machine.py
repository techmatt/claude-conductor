"""The machine under the daemon: Windows Update restarts, and items they cut off.

- `check_update_restart` (each tick, every `UPDATE_CHECK_SECONDS`): while an update waits
  for a restart and work is running or queued, one `warning` per boot and day naming the
  active hours outside which Windows may restart by itself (workbench 009 lost ~2 h).
- `_continue_interrupted` (restart rule 5, `daemon_setup._recover_spawned`): an executor
  gone with no result is resumed in place, once: the same session (`--resume`) in its
  kept worktree, still in `running/`, its deadline moved by the time lost, told what
  happened. Scheduling never sees it, so nothing can run beside it that could not before.
  Anything else (a second cut, a watch, no transcript, the worktree off its branch) is
  red as before, its reason naming a machine restart when there was one.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from . import executor, git, items, procs, talk, winupdate
from . import resume as resume_items
from .util import clock, now, parse_stamp, stamp, write_text

if TYPE_CHECKING:
    from .daemon import Run

UPDATE_CHECK_SECONDS = 600
CONTINUE_LIMIT = 1                   # resumes per item: a second cut is red

CONTINUE = """\
Conductor: this session was cut off at about {cut} and is resumed now, at {now}: {why}.
Everything you had started is gone: background commands, servers, builds, test runs and
browsers. Your worktree, branch and commits are as the cut left them, and a file being
written then may be partial. Check the state first (`git status`, `git log`, the output a
cut step was writing), redo the step that was cut, then carry on with the prompt from where
you were. Your deadline is now {deadline}; everything else you were told stands. Post a
progress line saying where you resume.{outside}

Write your report to exactly this path:
    {report}
"""
AMENDS = """
---

Amendments sent while you were cut off: each amends the prompt and wins where they conflict.
"""
OUTSIDE = (" This item changes data outside git: check that data against the step you were in "
           "before you redo anything, and commit before you change it again.")


class MachineMixin:
    _update_seen: tuple | None = None
    _update_due: float = 0.0

    def check_update_restart(self) -> None:
        if time.monotonic() < self._update_due:
            return
        self._update_due = time.monotonic() + UPDATE_CHECK_SECONDS
        info = winupdate.pending()
        if info is None or not (self.runs or any(self._queued_any())):
            return
        key = (winupdate.boot_time(), now().date())
        if key == self._update_seen:
            return
        self._update_seen = key
        hours = winupdate.hours_text(info["active"])
        when = f"outside its active hours ({hours})" if hours else "outside its active hours"
        self.log.append("warning", update_restart=True, active=info["active"], note=(
            f"Windows has an update waiting for a restart: {when} it may restart by itself and cut "
            "off running items. Restart at a quiet moment, or set active hours to cover overnight "
            "work (Settings, Windows Update). An item cut off resumes its session once the daemon "
            "is back (restart.md, *Machine restarts*)."))

    def _queued_any(self):
        for repo in self.cfg.repos:
            qdir = self.cfg.path("queue", repo)
            yield qdir.is_dir() and any(p for p in qdir.glob("*.md") if items.NAME_RE.match(p.stem))

    def _cut_why(self, rec: dict) -> str:
        """Why the executor is gone: a machine restart since it started (with Windows'
        logged reason when there is one), else an exit while conductor was down."""
        boot = winupdate.boot_time()
        if boot and boot > parse_stamp(rec["started"]):
            reason = winupdate.restart_reason()
            return f"the machine restarted{f' ({reason})' if reason else ''}, back at {clock(boot)}"
        return "its executor exited while conductor was down"

    def _continue_interrupted(self, run: Run) -> bool:
        """Resume a cut-off run's session in place; False when it can't be (red as before)."""
        cfg, rec = self.cfg, run.rec
        rec["interrupted"] = why = self._cut_why(rec)
        n = rec.get("continued", 0) + 1
        wt = Path(rec["worktree"]) if rec.get("worktree") else None
        if (n > CONTINUE_LIMIT or rec.get("phase", "exec") != "exec" or rec.get("template") == "watch"
                or not run.md_path.exists() or resume_items.session_file(rec["session_id"]) is None
                or (wt and (not wt.is_dir() or git.symbolic_head(wt) != f"refs/heads/{rec['branch']}"))):
            return False
        item = items.parse(run.md_path, rec["repo"])
        paths = executor.RunPaths.for_item(cfg, item)
        try:
            cut = datetime.fromtimestamp(Path(rec["log"]).stat().st_mtime).astimezone()
        except OSError:
            cut = parse_stamp(rec["started"])
        deadline = parse_stamp(rec["deadline"]) + max(now() - cut, timedelta(0))
        prompt = paths.prompt.with_name(paths.prompt.name.replace(".prompt.md", f".continue{n}.md"))
        text = CONTINUE.format(cut=clock(cut), now=clock(now()), why=why, deadline=clock(deadline),
                               outside=OUTSIDE if rec.get("external_data") else "", report=paths.report.resolve())
        amends = [m for m in talk.take(cfg.control, item.id, "resume") if talk.is_amend(m)]
        text += (AMENDS + "".join(executor._amend(executor.RESUME_AMEND, m) for m in amends) if amends else "")
        write_text(prompt, text)
        write_text(paths.mcp, executor.mcp_config(cfg, item, rec["session_id"], rec.get("worktree") or cfg.repos[item.repo]))
        write_text(paths.settings, executor.hook_settings(cfg, item))
        env = {executor.PORT_VAR: str(rec.get("port", ""))}
        if rec.get("remote"):
            from . import remote
            env |= remote.item_launch(cfg.control, prompt, item.id)
        try:
            run.popen = procs.spawn(executor.command(cfg, rec["session_id"], paths.mcp, resume=True,
                                                     settings=paths.settings),
                                    wt or cfg.repos[item.repo], prompt, paths.log, paths.stderr, env=env)
        except OSError as exc:
            rec["interrupted"] = f"{why}; resuming its session failed: {exc}"
            return False
        run.proc = None
        rec.update(pid=run.popen.pid, create_time=procs.create_time(run.popen.pid), continued=n,
                   deadline=stamp(deadline))
        self._save(run)
        self.log.append("recover", item=item.id, paths=[cfg.rel(prompt)], pid=run.popen.pid, continued=n,
                        session_id=rec["session_id"], deadline=rec["deadline"], cut=stamp(cut), why=why,
                        note=f"cut off at {clock(cut)} ({why}); its session resumed in place, "
                             f"deadline now {clock(deadline)}")
        return True
