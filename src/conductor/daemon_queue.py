"""The queue as each tick finds it (`docs/items.md`): new files are enqueued, a rewrite of
one not yet launched is `edited` (128: naming what changed, or that the write changed
nothing), and a write to an item that already launched is set aside as `.bad`.

A rewrite's receipt says what conductor now holds, so a planner whose bridge sent a stale
copy sees it at once (Word Warriors 184: the rewrite adding `needs_pick` never reached the
file, and the receipt said only "it runs the new text").
"""

from __future__ import annotations

import difflib
import json
import os
import time

from . import items, picks, state
from .daemon_signals import SIGNAL_SETTLE_SECONDS
from .util import FrontMatterError, file_hash, read_text, retry_io, split_front_matter

VALUE_CHARS = 40        # a front-matter value in a change line, at most


def _value(v) -> str:
    text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return text if len(text) <= VALUE_CHARS else text[:VALUE_CHARS - 1] + "…"


def changes(old: str, new: str) -> list[str]:
    """What a rewrite changed, for its receipt: each front-matter field added, removed or
    changed, then the body's lines added and removed; `whitespace only` when that is all."""
    try:
        (om, ob), (nm, nb) = split_front_matter(old), split_front_matter(new)
    except FrontMatterError:
        om, ob, nm, nb = {}, old, {}, new
    om, nm, out = om or {}, nm or {}, []
    for key in dict.fromkeys([*om, *nm]):
        if key not in om:
            out.append(f"{key}: {_value(nm[key])} added")
        elif key not in nm:
            out.append(f"{key} removed (was {_value(om[key])})")
        elif om[key] != nm[key]:
            out.append(f"{key}: {_value(om[key])} → {_value(nm[key])}")
    diff = [ln for ln in difflib.unified_diff(ob.strip().splitlines(), nb.strip().splitlines(), lineterm="", n=0)
            if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
    if diff:
        added, removed = sum(ln[0] == "+" for ln in diff), sum(ln[0] == "-" for ln in diff)
        out.append(f"body +{added} −{removed} line(s)")
    return out or ["whitespace only"]


class QueueMixin:
    def _scan_queue(self, view: state.View) -> tuple[dict[str, items.Item], set[str]]:
        """Enqueue new queue files and log each rewrite; the queued items by id, and those
        rewritten this tick (a stage of one is redone: `schedule`)."""
        self._refuse_late_writes(view)
        queued: dict[str, items.Item] = {}
        edited: set[str] = set()
        texts = self.queued_texts

        def dep_warning(item: items.Item) -> dict:
            warning = state.dependency_warning(view, item) if item.depends_on else None
            return {"warning": warning} if warning else {}      # its receipt says so (docs/receipts.md)

        for repo, queue in view.queued.items():
            for item in queue:
                queued[item.id] = item
                if item.id not in self.enqueued:
                    self.enqueued.add(item.id)
                    meta = item.meta()
                    meta["paths_declared"] = meta.pop("paths")      # `paths` is the event's own column
                    meta |= self._renumbered.pop(item.id, {})
                    if shared := [i for i in items.ref_matches(str(item.num), repo, view.known())
                                  if i != item.id and item.num]:
                        meta["shares_number"] = shared              # its receipt says: name it in full
                    self.starts[item.id] = starts = state.start_estimate(self.cfg, view, item)
                    carries = {"parked": p} if (p := state.parked_estimate(self.cfg, view, item)) else {}
                    self.log.append("enqueue", item=item.id, paths=[self.cfg.rel(item.path)],
                                    **meta, starts=starts, **carries, **dep_warning(item))
                # A queued item may be rewritten until it launches: the launch (and a
                # stage, which a change discards below) reads the file as it is then.
                digest = file_hash(item.path)
                try:
                    mtime, text = item.path.stat().st_mtime_ns, read_text(item.path)
                except (OSError, UnicodeDecodeError):
                    mtime, text = None, None
                seen_mtime, seen_text = texts.get(item.id, (mtime, text))
                if self.queued_hashes.get(item.id, digest) != digest:
                    edited.add(item.id)
                    starts = state.start_estimate(self.cfg, view, item)
                    moved = {"starts": starts} if self.starts.get(item.id) != starts else {}
                    self.starts[item.id] = starts
                    what = changes(seen_text, text) if seen_text is not None and text is not None else []
                    choices = ({"choices": item.needs_pick} if item.needs_pick and not item.errors
                               and any(c.startswith("needs_pick") for c in what)
                               and not picks.awaited(self.cfg, view, item.needs_pick) else {})
                    self.log.append("edited", item=item.id, paths=[self.cfg.rel(item.path)],
                                    staged=item.id in view.staged, changes=what, **choices, **moved,
                                    **dep_warning(item), **self._kept_amends(item.id))
                elif mtime is not None and seen_mtime is not None and mtime != seen_mtime:
                    # Written again with the same bytes: the planner meant a change that never came.
                    self.log.append("edited", item=item.id, paths=[self.cfg.rel(item.path)],
                                    staged=item.id in view.staged, identical=True)
                self.queued_hashes[item.id] = digest
                if text is not None:
                    texts[item.id] = (mtime, text)
                if item.errors:
                    # Per file version and reason: a `resume:` item's reason moves with the queue.
                    key = f"{item.id}:{item.path.stat().st_mtime}:{'; '.join(item.errors)}"
                    if self.invalid_seen.get(item.id) != key:
                        self.invalid_seen[item.id] = key
                        self.log.append("invalid", item=item.id, paths=[self.cfg.rel(item.path)],
                                        error="; ".join(item.errors))
        self.queued_hashes = {i: h for i, h in self.queued_hashes.items() if i in queued}
        self.queued_texts = {i: t for i, t in texts.items() if i in queued}
        self.starts = {i: s for i, s in self.starts.items() if i in queued}
        return queued, edited

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
