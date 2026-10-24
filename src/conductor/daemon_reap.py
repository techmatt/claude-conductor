"""Leftover processes and worktrees: the reaper, and retries of what it could not remove."""

from __future__ import annotations

import json
import time
from pathlib import Path

import psutil

from . import git, procs, state, targets, worktrees
from .util import clock, parse_stamp, stamp, write_text


class ReapMixin:
    def _reap(self, item_id: str, worktree: str | None, since: str | None, context: str,
              until: str | None = None, job: tuple[int | None, float | None] | None = None) -> None:
        """Kill what an item left running in its worktree (a Vite server, a browser, a
        waiter loop): processes started while it ran (`procs.strays`), and what is left
        in its executor's job (`job`: the executor's pid and creation time). Never a run
        target, another item's or a stage's processes, or the daemon's own. Logged
        `reaped`. Found live: 030's Vite preview and twenty waiter loops held its
        worktree for a day, so its removal kept failing."""
        try:
            self._reap_now(item_id, worktree, since, context, until, job or (None, None))
        finally:
            procs.release_job(*(job or (None, None)))

    def _reap_now(self, item_id: str, worktree: str | None, since: str | None, context: str,
                  until: str | None, job: tuple[int | None, float | None]) -> None:
        if not worktree or not since or not Path(worktree).is_dir():
            return
        spare = set()
        for name in self.cfg.targets:
            if t := targets.process(self.cfg, name):
                spare |= procs.family(t["pid"])
        for r in [*self.runs.values(), *self.stages.values()]:
            if r.item_id != item_id:
                spare |= procs.family(r.pid())
                if r.resolver is not None:
                    spare |= procs.family(r.resolver.pid)
        start = parse_stamp(since).timestamp() - 5
        end = parse_stamp(until).timestamp() + 60 if until else time.time()
        roots = [p.pid for p in procs.strays(Path(worktree), start, end, spare)] + list(procs.job_members(*job))
        if not roots:
            return
        pids = set().union(*(procs.family(p) for p in roots)) - spare
        found = []
        for pid in sorted(pids):
            try:
                found.append(psutil.Process(pid))
            except psutil.Error:
                continue
        rows, total = [], 0
        for p in found:
            try:
                private, _ = procs.usage(p)
                rows.append({"pid": p.pid, "name": p.name(), "kind": procs.kind(p.name(), p.cmdline()),
                             "private_mb": round(private / 2 ** 20)})
                total += private
            except psutil.Error:
                continue
        for p in found:
            try:
                p.kill()
            except psutil.Error:
                pass
        psutil.wait_procs(found, timeout=10)
        self.log.append("reaped", item=item_id, paths=[Path(worktree).as_posix()], context=context,
                        processes=rows, private_mb=round(total / 2 ** 20))

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
            if action == "kept":
                self.log.append("warning", item=rec["item"],
                                note=f"worktree {rec['worktree']} not {action}: {exc!r}; remove it by hand")
                return
            # Usually a file another process still holds (a dev server, an editor).
            rows = [r for r in self._leftovers() if r["worktree"] != rec["worktree"]]
            rows.append({"item": rec["item"], "repo": rec["repo"], "worktree": rec["worktree"],
                         "branch": rec.get("branch"), "base": rec.get("base"), "action": action, "note": note,
                         "failed": stamp(), "error": repr(exc)})
            self._save_leftovers(rows)
            self.log.append("warning", item=rec["item"], paths=[Path(rec["worktree"]).as_posix()],
                            note=f"worktree {rec['worktree']} not {action}: {exc!r}; retried when the "
                                 f"{rec['repo']} queue next empties and at the next daemon start")

    # ---------------------------------------------------------------- leftover worktrees
    @property
    def _leftovers_file(self) -> Path:
        return self.cfg.state_dir / "leftovers.json"

    def _leftovers(self) -> list[dict]:
        try:
            rows = json.loads(self._leftovers_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [r for r in rows if isinstance(r, dict) and r.get("worktree")] if isinstance(rows, list) else []

    def _save_leftovers(self, rows: list[dict]) -> None:
        if rows:
            write_text(self._leftovers_file, json.dumps(rows, indent=1))
        else:
            self._leftovers_file.unlink(missing_ok=True)

    def _retry_leftovers(self, context: str, repo: str | None = None, skip: str | None = None) -> None:
        """Remove worktrees an earlier removal left behind (`leftovers.json`): at daemon
        start and when a repo's queue empties. A `warning` only while one still fails."""
        rows = self._leftovers()
        if not rows:
            return
        in_use = {str(Path(r.rec["worktree"])) for r in [*self.runs.values(), *self.stages.values()]
                  if r.rec.get("worktree")}
        for rec_path in self.cfg.path("running").glob("*/*.json"):
            try:
                if wt := state.read_run(rec_path).get("worktree"):
                    in_use.add(str(Path(wt)))
            except (OSError, ValueError):
                continue
        keep = []
        for row in rows:
            if (repo and row["repo"] != repo) or row["item"] == skip or row["repo"] not in self.cfg.repos:
                keep.append(row)
                continue
            if str(Path(row["worktree"])) in in_use:
                continue                # a later launch of the same name took the path over
            # What holds it is usually the item's own leftover: a server, a waiter loop.
            started, finished = self.log.last("start", item=row["item"]), self.log.last("finish", item=row["item"])
            if started and finished:
                self._reap(row["item"], row["worktree"], started.ts, f"leftover worktree retry ({context})",
                           until=finished.ts)
            try:
                worktrees.discard(self.cfg, row["repo"], Path(row["worktree"]), row.get("branch"))
            except (OSError, git.GitError) as exc:
                row["tries"], row["error"] = row.get("tries", 1) + 1, repr(exc)
                keep.append(row)
                self.log.append("warning", item=row["item"], paths=[Path(row["worktree"]).as_posix()],
                                note=f"leftover worktree {row['worktree']} still not removed ({context}): {exc!r}; "
                                     "retried again later")
                continue
            self._worktree_event(row, row["action"],
                                 f"leftover removed on retry ({context}); first failed {clock(row['failed'])}")
        self._save_leftovers(keep)
