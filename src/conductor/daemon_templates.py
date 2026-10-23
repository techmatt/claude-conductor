"""What conductor hands a template item at launch, since executors never see the control
folder: a cleanup's audit notes, a disk audit's measurements, a watch's view of the running
items (`{{notes}}` and `{{usage}}` in `templates/`)."""

from __future__ import annotations

from pathlib import Path

from . import audits, inbox, nightshift, questions, state
from .util import clock, duration, now, parse_stamp, read_text

PROMPT_CHARS = 6000     # of each running item's prompt, in a watch's view
PROGRESS_LINES = 12
EVENT_LINES = 15
QUIET_KINDS = ("progress", "worktree", "setup", "enqueue")   # already shown, or noise to a watch


def _quote(text: str) -> str:
    """Quoted, so nothing in an item's prompt reads as this prompt's own text."""
    return "\n".join(f"> {ln}" if ln else ">" for ln in text.rstrip().splitlines())


class TemplatesMixin:
    def _free_notes(self, repo: str) -> list[str]:
        """The ids of `repo`'s audit notes no cleanup has used or claimed, oldest first."""
        return [n.item for n in state.scan(self.cfg, self.log).unused_notes(self.cfg, repo)]

    def _notes_text(self, rec: dict) -> str:
        if not rec.get("notes"):
            return ""
        finishes = {i: ev for i in rec["notes"] if (ev := self.log.last("finish", item=i))}
        return audits.render_notes(self.cfg, rec["notes"], finishes)

    def _template_usage(self, rec: dict) -> str:
        """`{{usage}}`: a disk audit's measurements or a watch's view; nothing for the rest."""
        if rec.get("template") == "disk_audit":
            return self._disk_usage(rec)
        if nightshift.is_watch(rec):
            return self._watch_context()
        return ""

    def _disk_usage(self, rec: dict) -> str:
        """A disk audit's view of what only conductor sees: the control folder's sizes
        and what each of the repo's worktrees is. Leftover removals are retried first."""
        repo = rec["repo"]
        self._retry_leftovers("disk audit", repo=repo)
        live = {str(Path(r.rec["worktree"])): r.item_id for r in [*self.runs.values(), *self.stages.values()]
                if r.rec.get("worktree") and r.rec.get("repo") == repo}
        if rec.get("worktree"):
            live[str(Path(rec["worktree"]))] = "this item's own working directory"
        last: dict[str, str] = {}
        for ev in self.log.query(kinds=["worktree"]):
            if ev.repo == repo and ev.item:
                last[ev.item.split("/", 1)[1]] = ev.detail.get("action")
        kept = {name for name, action in last.items() if action == "kept"}
        shared = self.cfg.opts(repo).shared
        checkout = (f"Main checkout: `{self.cfg.repos[repo]}`. Its `shared` folders (linked into every "
                    "worktree; a Drive-homed one is a junction in the main checkout): "
                    + (", ".join(f"`{s}`" for s in shared) if shared else "none") + ".")
        return "\n\n".join([checkout, audits.control_usage(self.cfg, repo),
                            audits.worktree_listing(self.cfg, repo, live, self._leftovers(), kept)])

    def _watch_context(self) -> str:
        """A watch's view, frozen at its launch: each running item's prompt, outline,
        progress lines and recent events, then the open questions and recent flags."""
        at = now()
        out = [f"## What conductor sees at {clock(at)}"]
        live = sorted((i, r) for i, r in self.runs.items()
                      if not nightshift.is_watch(r.rec) and r.rec.get("phase", "exec") == "exec" and not r.exited())
        for item_id, run in live:
            rec = run.rec
            started = parse_stamp(rec["started"])
            head = (f"### {item_id}\n\nStarted {clock(started)} ({duration((at - started).total_seconds())} ago); "
                    f"max_hours {rec.get('max_hours', 0):g}, stopped at {clock(rec['deadline'])}"
                    + ("; **blocked on a question now**" if item_id in self.blocked else "")
                    + (f"; **paused** by Matt since {clock(rec['paused_at'])}: leave it be" if rec.get("paused_at") else "")
                    + ".")
            try:
                prompt = read_text(run.md_path)
            except (OSError, UnicodeDecodeError) as exc:
                prompt = f"(could not read its prompt: {exc})"
            if len(prompt) > PROMPT_CHARS:
                prompt = prompt[:PROMPT_CHARS] + f"\n… ({len(prompt) - PROMPT_CHARS} more characters)"
            parts = [head, "Its prompt:", _quote(prompt)]
            o = inbox.latest_outline(self.log, item_id)
            parts.append(f"Outline: {inbox.outline_line(o)}" if o else "Outline: none posted.")
            prog = self.log.query(item=item_id, kinds=["progress"])[-PROGRESS_LINES:]
            parts.append("Progress lines, oldest first:\n" + ("\n".join(f"- {clock(e.ts)} {inbox.progress_line(e)}"
                                                                      for e in prog) or "- none yet"))
            evs = [e for e in self.log.query(item=item_id) if e.kind not in QUIET_KINDS][-EVENT_LINES:]
            parts.append("Recent events:\n" + "\n".join(f"- {clock(e.ts)} #{e.id} {inbox.describe(e)}" for e in evs))
            out.append("\n\n".join(parts))
        if not live:
            out.append("No item is executing now.")
        qs = [q for q in questions.scan(self.cfg.control) if q.is_open]
        out.append("### Open questions\n\n" + ("\n".join(
            f"- `{q.id}` from {q.item}, asked {clock(q.asked)}, "
            + ("blocking" if q.block else f"default at {clock(q.deadline)}") + f": {q.summary()}" for q in qs)
            or "None."))
        flags = [e for e in self.log.query(kinds=["watch_flag"])
                 if (at - parse_stamp(e.ts)).total_seconds() < 6 * 3600][-20:]
        out.append("### Conductor's flags, the last six hours\n\n"
                   + ("\n".join(f"- {clock(e.ts)} {e.detail.get('text')}" for e in flags) or "None."))
        return "\n\n".join(out)
