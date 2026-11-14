"""Workflow retros queue themselves (docs/retros.md): once a finish makes one due
(`session.retros`), conductor writes the self-audit item the planner's **post-audit X**
would, `resume: <repo>/<X>` with `template: self_audit`, numbered like any item. No planner
signal and no night-shift gate; `[defaults] auto_retros = false` turns it off."""

from __future__ import annotations

import os

from . import items, nightshift, session, state
from .util import write_text

RETRO_FRONT = "---\nrepo: {repo}\nresume: {orig}\ntemplate: self_audit\n---\n"


class RetrosMixin:
    _retro_mark: tuple | None = None
    _renumbered: dict | None = None

    def renumber_taken(self, view) -> bool:
        """A new queue file whose number an item of its repo already has (queued, running,
        done) is renamed to the repo's next number before it is enqueued: a new item never
        shares one (115: aicomplete's re-queued retro and a planner item both got 222).
        Its `enqueue` carries `renumbered_from`, and its receipt stays at the name written."""
        if self._renumbered is None:
            self._renumbered = {}
        taken = [i for i in view.known() if i in self.enqueued or i in view.running or i in view.done]
        moved = False
        for repo, queue in view.queued.items():
            for item in queue:
                if item.id in self.enqueued or not item.num:
                    continue
                if not (others := [i for i in items.ref_matches(str(item.num), repo, taken) if i != item.id]):
                    taken.append(item.id)      # a later new file with its number is the one renamed
                    continue
                name = nightshift.queue_name(self.cfg, view, repo, item.name)
                try:
                    os.replace(item.path, item.path.with_name(f"{name}.md"))
                except OSError:
                    continue                   # still being written: the next tick
                self._renumbered[f"{repo}/{name}"] = {"renumbered_from": item.name, "number_taken_by": others}
                view.queued[repo] = [*view.queued[repo], items.parse(item.path.with_name(f"{name}.md"), repo)]
                taken.append(f"{repo}/{name}")
                moved = True
        return moved

    def queue_retros(self) -> None:
        """Queue each repo's due retro, one at a time per repo, never while it is paused.
        Looked at again only after a finish, pause, resume or retro event."""
        if not self.cfg.auto_retros:
            return
        mark = tuple((e.id if (e := self.log.last(k)) else 0) for k in ("finish", "pause", "resume", "retro"))
        if mark == self._retro_mark:
            return
        self._retro_mark = mark
        view = state.scan(self.cfg, self.log)
        for repo in self.cfg.repos:
            if repo in view.paused or self._reorder_pending() == repo:
                continue
            r = session.retros(self.log, view, repo)
            if r.pending or r.due is None:
                continue
            orig = r.due.item
            name = nightshift.queue_name(self.cfg, view, repo, f"post_audit_{orig.split('/', 1)[1].split('_', 1)[0]}")
            path = self.cfg.path("queue", repo, f"{name}.md")
            if path.exists():
                continue
            write_text(path, RETRO_FRONT.format(repo=repo, orig=orig))
            rel = self.cfg.rel(path)
            self.log.append("retro", item=f"{repo}/{name}", paths=[rel], file=rel, of=orig, why=r.why,
                            by="conductor")
