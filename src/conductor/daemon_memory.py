"""Memory sampling: the box's level, each live item's process tree, and what running items are told."""

from __future__ import annotations

import time
from pathlib import Path

from . import memory, procs, talk
from .util import parse_stamp

# What a running item is told when the box's memory level changes (`sample_memory`).
MEMORY_TELLS = {
    "pressure": ("Conductor: the machine is low on memory ({free} GiB free, {percent}% of commit used). Keep "
                 "working, but cut parallelism now: one browser and one test worker at a time, close dev servers "
                 "and browsers you are not using, and start no new parallel subagents or workflows until conductor "
                 "says memory is back to normal."),
    "critical": ("Conductor: memory is CRITICAL ({free} GiB free, {percent}% of commit used). Finish or stop "
                 "whatever runs in parallel now: one process at a time, no browsers you are not using this "
                 "minute, no subagents or workflows. Commit your work so far. Conductor will not kill you."),
    "ok": ("Conductor: memory is back to normal ({free} GiB free, {percent}% of commit used); you may go back to "
           "your usual parallelism."),
}


class MemoryMixin:
    def sample_memory(self, force: bool = False) -> None:
        """Every `memory_sample_seconds`: the box's commit and free RAM, each live item's
        process tree by kind (stored in `.conductor/memory.jsonl`), the heartbeat, and a
        `memory` event (`memory_critical` at the top level) when the level changes.
        Between samples, every tick reads the box alone (microseconds): a level that
        rises samples at once. Found live 2026-10-24 (wordwarriors 142): commit went
        from 84% to 95% inside one 30s interval, so ok jumped straight to critical."""
        every = self.cfg.memory_sample_seconds
        if not every:
            return
        try:
            status = self.memory_probe()
        except OSError as exc:
            if force or self._mem_at is None or time.monotonic() - self._mem_at >= every:
                self._mem_at = time.monotonic()
                self.log.append("warning", note=f"memory sample failed: {exc!r}")
            return
        if not force and self._mem_at is not None and time.monotonic() - self._mem_at < every:
            rising = memory.level(status, self.cfg, self.mem_level)
            if memory.LEVELS.index(rising) <= memory.LEVELS.index(self.mem_level):
                return
        self._mem_at = time.monotonic()
        trees = {}
        live = [*self.runs.items(), *self.stages.items()]
        claimed = set().union(set(), *(procs.family(r.pid()) for _, r in live))
        for item_id, run in live:
            phase = run.rec.get("phase", "exec")
            if phase == "merge" and run.resolver is not None and run.resolver.poll() is None:
                trees[item_id] = memory.tree(run.resolver.pid)      # its merge executor
            elif phase in ("exec", "staging", "setup") and not run.exited():     # setup: the runner's tree
                # Plus what it detached in its worktree (`( cmd & )`, a server): an orphan
                # is outside the tree. Found live: the prompt-20 proof's hog was.
                left = [p.pid for p in procs.strays(Path(run.rec["worktree"]), parse_stamp(run.rec["started"]).timestamp() - 5,
                                                    time.time(), claimed)] if run.rec.get("worktree") else []
                trees[item_id] = memory.tree(run.pid(), *left)
        was = self.mem_level
        now_level = memory.level(status, self.cfg, was)
        if memory.LEVELS.index(now_level) >= memory.LEVELS.index(was):
            self._mem_clear_since = None
            self.mem_level = now_level                  # a rise counts at once
        else:
            # A fall counts once the box has stayed clear for memory_calm_seconds: a
            # leaking service on this box swings free RAM by gigabytes every few minutes.
            self._mem_clear_since = self._mem_clear_since or time.monotonic()
            if time.monotonic() - self._mem_clear_since >= self.cfg.memory_calm_seconds:
                self.mem_level, self._mem_clear_since = now_level, None
        rec = memory.sample(status, self.mem_level, trees)
        try:
            memory.store(self.cfg.state_dir, rec, self.cfg.memory_keep_hours)
        except OSError as exc:
            self.log.append("warning", note=f"memory sample not stored: {exc!r}")
        self._beat()
        if self.mem_level != was:
            self._memory_changed(was, rec, trees)

    def _memory_changed(self, was: str, rec: dict, trees: dict) -> None:
        lv = self.mem_level
        action = {
            "ok": "launches and stages resume",
            "pressure": "no new launches or stages until it clears; running items told to cut parallelism",
            "critical": ("CRITICAL: no new launches or stages; running items told to drop to one process at a time. "
                         "Close what you can on the box (browsers, editors, other sessions); conductor kills nothing"),
        }[lv]
        skip = set().union(set(), *(procs.family(r.pid()) for r in [*self.runs.values(), *self.stages.values()]))
        pct = round(100 * rec["commit"] / rec["limit"]) if rec["limit"] else 0
        self.log.append("memory_critical" if lv == "critical" else "memory", level=lv, was=was,
                        commit=rec["commit"], limit=rec["limit"], percent=pct, free=rec["free"],
                        top=memory.top(rec), others=memory.others(skip), action=action)
        for item_id, run in self.runs.items():
            if run.rec.get("phase", "exec") != "exec" or run.exited():
                continue
            if lv == self.mem_told.get(item_id, "ok"):
                continue                # told already (or, back at ok, never told)
            text = MEMORY_TELLS[lv].format(free=rec["free"], percent=pct)
            ev = self.log.append("tell", item=item_id, text=text, by="conductor")
            talk.post(self.cfg.control, item_id, ev.id, text, "conductor")
            self.mem_told[item_id] = lv
