"""The daemon's view of questions, progress lines, deliveries and amendments."""

from __future__ import annotations

import time
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from . import daemon_signals, executor, items, questions, state, talk
from .util import FrontMatterError, clock, file_hash, parse_stamp, read_text, retry_io, split_front_matter, stamp

if TYPE_CHECKING:
    from .daemon import Run

OUTCOME_KINDS = {"answered": "answer", "default": "default"}     # anything else: question_closed
AMEND_DELIVERY = {
    "queued": "appended to the prompt at launch",
    "staging": "added to the resume message; the stage is kept",
    "staged": "added to the resume message; the stage is kept",
    "running": "delivered after its next tool call",
}
REPLY_VIAS = ("hook", "progress")       # deliveries into a running session, which replies with `progress`


class MessagesMixin:
    def process_questions(self) -> None:
        """Log what the ask servers and the planner wrote in questions/. The server
        resolves questions; the daemon only observes, and moves deadlines for blocked time."""
        blocked = set()
        for q in questions.scan(self.cfg.control):
            rel = self.cfg.rel(q.path)
            if ("ask", q.id) not in self.q_seen:
                self._q_log("ask", q, [rel], text=q.summary(), block=q.block, default=q.default,
                            deadline=q.deadline, deadline_clock=clock(q.deadline) if q.deadline else None)
            if q.is_open:
                if q.block:
                    blocked.add(q.item)
                continue
            outcome = q.result.get("outcome")
            kind = OUTCOME_KINDS.get(outcome, "question_closed")
            if (kind, q.id) not in self.q_seen:
                waited = (parse_stamp(q.result["at"]) - parse_stamp(q.asked)).total_seconds()
                extra = {}
                if q.block and (run := self.runs.get(q.item)):
                    extra["deadline"] = self._extend_deadline(run, waited)
                paths = [self.cfg.rel(q.answer_path)] if outcome == "answered" else [rel]
                if outcome == "answered" and (by := questions.answer_by(q)):
                    extra["by"] = by
                self._q_log(kind, q, paths, outcome=outcome, text=q.result.get("text"),
                            reason=q.result.get("reason"), waited_seconds=round(waited), **extra)
            if outcome != "answered" and ("late", q.id) not in self.q_seen and q.answer_path.exists():
                text = questions.read_answer(q)
                if text is not None:
                    by = questions.answer_by(q)
                    self._q_log("late", q, [self.cfg.rel(q.answer_path)], text=text, outcome=outcome,
                                **({"by": by} if by else {}),
                                note="answered after the question resolved; the executor never saw it")
        self.blocked = blocked

    def process_talk(self) -> None:
        """Log what the executors' progress tool and message hook wrote: one `progress`
        per new progress line, one `delivered` per delivered message. Deliveries go
        first: the first progress line after a message reached a running executor is
        tagged as its reply (`reply_to`)."""
        control = self.cfg.control
        for msg in talk.delivered(control):
            if msg.get("id") in self.delivered_seen:
                continue
            self.delivered_seen.add(msg.get("id"))
            extra = {"tell": msg.get("id")}
            if talk.is_amend(msg):
                extra = {"amend": msg.get("id")}
                kept = self._keep_amendment(msg)
                extra["kept"] = self.cfg.rel(kept) if kept else None
            self.log.append("delivered", item=msg.get("item"), via=msg.get("via"),
                            text=msg.get("text"), waited_seconds=talk.waited(msg),
                            after_seq=msg.get("after_seq"), **extra)
            if msg.get("via") in REPLY_VIAS and isinstance(msg.get("after_seq"), int):
                self.awaiting_reply.setdefault(msg["item"], []).append((msg["id"], msg["after_seq"]))
        for item_id in talk.progress_items(control):
            path = talk.progress_file(control, item_id)
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if self._progress_sizes.get(item_id) == size:
                continue
            self._progress_sizes[item_id] = size
            for line in talk.read_progress(control, item_id):
                if (item_id, line["seq"]) in self.progress_seen:
                    continue
                self.progress_seen.add((item_id, line["seq"]))
                extra = {}
                waiting = self.awaiting_reply.get(item_id, [])
                if replied := [mid for mid, after in waiting if line["seq"] > after]:
                    extra["reply_to"] = replied[0] if len(replied) == 1 else replied
                    self.awaiting_reply[item_id] = [w for w in waiting if w[0] not in replied]
                self.log.append("progress", item=item_id, paths=[self.cfg.rel(path)],
                                **{k: v for k, v in line.items() if k in ("seq", "at", "note", "eta", "done", "remaining", "outline")},
                                **extra)

    def _awaiting_reply(self) -> dict[str, list[tuple[int, int]]]:
        """Messages delivered to a running executor with no reply tagged yet, per item:
        (message id, progress lines by its delivery). Rebuilt from the log at start."""
        replied = {i for e in self.log.query(kinds=["progress"]) for i in talk.reply_ids(e.detail)}
        out: dict[str, list[tuple[int, int]]] = {}
        for e in self.log.query(kinds=["delivered"]):
            mid = e.detail.get("tell") or e.detail.get("amend")
            after = e.detail.get("after_seq")
            if e.detail.get("via") in REPLY_VIAS and isinstance(after, int) and mid not in replied and e.item:
                out.setdefault(e.item, []).append((mid, after))
        return out

    def _keep_amendment(self, msg: dict) -> Path | None:
        repo, _, name = str(msg.get("item", "")).partition("/")
        if repo not in self.cfg.repos:
            return None
        path = executor.RunPaths.for_name(self.cfg, repo, name).amend
        talk.keep_amendment(path, msg)
        return path

    # ---------------------------------------------------------------- amendments
    def process_amends(self, view: state.View) -> set[str]:
        """Take every settled `queue/<repo>/NNN_name.amend[.N].md`, in order, and deliver
        it by the item's state: a queued or staged item gets it at launch (in its prompt,
        or in the resume message, the stage kept); a running one after its next tool call.
        A finished or merging item's is refused and left in place. Returns the items with
        an amend file still being written: they wait a tick before they launch."""
        fresh: set[str] = set()
        queued = {it.id for q in view.queued.values() for it in q}
        for repo in self.cfg.repos:
            qdir = self.cfg.path("queue", repo)
            found = sorted(((a, p) for p in qdir.glob("*.md") if (a := items.amend_of(p))) if qdir.is_dir() else [],
                           key=lambda t: (t[0], t[1].name))
            for (name, _), p in found:
                item_id, rel = f"{repo}/{name}", self.cfg.rel(p)
                try:
                    if time.time() - p.stat().st_mtime < daemon_signals.SIGNAL_SETTLE_SECONDS:
                        fresh.add(item_id)
                        continue
                except OSError:
                    continue
                digest = file_hash(p)
                if (rel, digest) in self.amend_refused:
                    continue
                where = self._amend_state(item_id, view, queued)
                if where is None:
                    if self.amend_waiting.get(rel) != digest:
                        self.amend_waiting[rel] = digest
                        self.log.append("warning", item=item_id, paths=[rel],
                                        note=f"{rel}: no item {item_id} is queued, staged or running; "
                                             "left in place until it appears")
                    continue
                self.amend_waiting.pop(rel, None)
                reason = None
                try:
                    meta, text = split_front_matter(read_text(p))
                except (FrontMatterError, UnicodeDecodeError) as exc:
                    meta, text, reason = {}, "", f"unreadable: {exc}"
                by = str((meta or {}).get("by") or "unknown")
                text = text.strip()
                if where in ("finished", "merging"):
                    reason = (f"{item_id} has finished; a correction to finished work is a new item"
                              if where == "finished" else
                              f"{item_id} has finished executing and is merging; a correction to finished work "
                              "is a new item")
                elif reason is None and not text:
                    reason = "the amendment is empty"
                if reason:
                    self.amend_refused.add((rel, digest))
                    self.log.append("amend_refused", item=item_id, paths=[rel], path=rel, hash=digest,
                                    state=where, reason=reason, by=by)
                    continue
                ev = self.log.append("amended", item=item_id, paths=[rel], text=text, by=by, state=where,
                                     delivery=AMEND_DELIVERY[where])
                talk.post(self.cfg.control, item_id, ev.id, text, by, kind="amend", state=where)
                retry_io(p.unlink)
        return fresh

    def _amend_state(self, item_id: str, view: state.View, queued: set[str]) -> str | None:
        """queued | staging | staged | running | merging | finished, or None (not seen)."""
        if run := self.runs.get(item_id):
            return "running" if run.rec.get("phase", "exec") == "exec" and not run.exited() else "merging"
        if item_id in view.running:
            return "running" if view.running[item_id].get("phase", "exec") == "exec" else "merging"
        if item_id in view.finishes or item_id in view.done:
            return "finished"
        if item_id in view.staged and item_id in queued:
            return view.staged[item_id].get("phase", "staged")
        return "queued" if item_id in queued else None

    def _q_log(self, kind: str, q: questions.Question, paths: list[str], **detail) -> None:
        self.q_seen.add((kind, q.id))
        self.log.append(kind, item=q.item, paths=paths, question=q.id, **detail)

    def _extend_deadline(self, run: Run, seconds: float) -> str:
        """Time blocked on a question does not count toward max_hours."""
        rec = run.rec
        rec["deadline"] = stamp(parse_stamp(rec["deadline"]) + timedelta(seconds=seconds))
        rec["blocked_seconds"] = rec.get("blocked_seconds", 0) + round(seconds)
        self._save(run)
        return rec["deadline"]

    def _close_questions(self, item_id: str) -> None:
        for q in questions.scan(self.cfg.control):
            if q.item == item_id and q.is_open:
                questions.resolve(q, "closed", None, "daemon", reason="the item finished before an answer")
        self.process_questions()
