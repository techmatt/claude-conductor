"""Workflow retros queue themselves (docs/retros.md): once a finish makes one due
(`session.retros`), conductor writes the self-audit item the planner's **post-audit X**
would, `resume: <repo>/<X>` with `template: self_audit`, numbered like any item. No planner
signal and no night-shift gate; `[defaults] auto_retros = false` turns it off."""

from __future__ import annotations

from . import nightshift, session, state
from .util import write_text

RETRO_FRONT = "---\nrepo: {repo}\nresume: {orig}\ntemplate: self_audit\n---\n"


class RetrosMixin:
    _retro_mark: tuple | None = None

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
