"""Receipts: what conductor did with each planner write, at a path the planner can name
before it exists (`docs/receipts.md`), so it stages one small file instead of the inbox.

The receipt of `<folder>/<name>.md` or `<folder>/<name>.signal` is `receipts/<folder>/<name>.md`;
a request folder's is `receipts/requests/<stamp>.md`. A signal file and a request are one
receipt each, holding every event their handling logged; queue items, amendments, answers
and proposals get theirs from the event that took them (or, for `proposed/`, a parse), and a
delivery adds its line to the receipt of the tell or amendment it delivered. Receipts are
runtime state: the folder ignores itself (`receipts/.gitignore`), and files older than
`KEEP_SECONDS` are pruned.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import PurePosixPath

from . import daemon_signals, inbox, items
from .events import Event
from .util import FrontMatterError, clock, now, read_text, split_front_matter, write_text

ROOT = "receipts"
KEEP_SECONDS = 3 * 86400        # a receipt is read within minutes of its write; three days is ample
PRUNE_EVERY = 3600
LINES = 8                       # event lines in a receipt's body; the rest are only ids
# Events that mean the write (or a line of it) was not carried out.
REFUSALS = ("invalid", "request_failed", "amend_refused", "late", "open_refused", "pick_failed")


def rel_for(write_rel: str) -> str:
    """`signals/planner-X.signal` → `receipts/signals/planner-X.md`; `queue/app/012_x.md` →
    `receipts/queue/app/012_x.md`; `requests/<stamp>` → `receipts/requests/<stamp>.md`."""
    p = PurePosixPath(write_rel.rstrip("/").removesuffix(".bad"))
    name = next((p.name.removesuffix(s) for s in (".signal", ".md") if p.name.endswith(s)), p.name)
    return (PurePosixPath(ROOT) / p.parent / f"{name}.md").as_posix()


def _q(value) -> str:
    return json.dumps(value, ensure_ascii=False)     # a JSON scalar or list is valid YAML


class ReceiptsMixin:
    def _init_receipts(self) -> None:
        self._capturing: list[Event] | None = None  # events of the signal file or request being handled
        self._pruned_at = 0.0
        self.proposed_seen: dict[str, int] = {}     # proposed file → the mtime its receipt covers
        self._pending: dict[str, str] = {}          # waiting request → what its pending receipt says

    def _prepare_receipts(self) -> None:
        root = self.cfg.path(ROOT)
        root.mkdir(parents=True, exist_ok=True)
        if not (root / ".gitignore").is_file():     # ignored without a request to the tracked .gitignore
            write_text(root / ".gitignore", "*\n")

    @contextmanager
    def _receipt(self, rel: str, always: bool = False):
        """Every event appended inside goes to `rel`'s receipt, written on the way out; with
        nothing logged, only if `always` (a signal), never for a request still waiting."""
        got: list[Event] = []
        prev, self._capturing = self._capturing, got
        try:
            yield got
        finally:
            self._capturing = prev
        if got or always:
            refusal = next((e for e in got if e.kind in REFUSALS and (e.kind != "invalid" or rel in e.paths)), None)
            if refusal:
                self._write_receipt(rel, got, reason=self._reason(refusal), applied=refusal.detail.get("applied"))
            else:
                commit = next((e for e in got if e.kind == "commit"), None)
                request = next((e for e in got if e.kind == "request"), None)
                result = (f"committed {commit.detail.get('sha', '')[:8]}" if commit else
                          "applied; nothing changed" if request else "applied")
                self._write_receipt(rel, got, result=result)

    def _receipt_event(self, ev: Event) -> None:
        """Every appended event: into the receipt being gathered, or the receipt of the
        write it took or refused, or a delivery onto its message's receipt."""
        if self._capturing is not None:
            self._capturing.append(ev)
            return
        rel, d = (ev.paths[0] if ev.paths else ""), ev.detail
        match ev.kind:
            case "enqueue":
                shared = d.get("shares_number")
                self._write_receipt(rel, [ev], result=f"queued as {ev.item}" + (
                    f"; its number is also {', '.join(shared)}'s: name it in full, never by number"
                    if shared else ""))
            case "edited":
                how = "the launch it raced was undone; " if d.get("launching") else ""
                how += "its stage is redone" if d.get("staged") else "it runs the new text"
                self._write_receipt(rel, [ev], result=f"rewritten: {how}")
            case "invalid" if ev.item and rel.startswith("queue/"):
                self._write_receipt(rel, [ev], reason=self._reason(ev))
            case "amended":
                self._write_receipt(rel, [ev], result=f"amendment #{ev.id}, {d.get('state')}: {d.get('delivery')}")
            case "answer":
                self._write_receipt(rel, [ev], result="answered: the executor has it")
            case "amend_refused" | "late":
                self._write_receipt(rel, [ev], reason=self._reason(ev))
            case "delivered":
                self._receipt_delivered(ev)

    def _reason(self, ev: Event) -> str:
        d = ev.detail
        if ev.kind == "late":
            return f"the question had already resolved ({d.get('outcome')}); the executor never saw this answer"
        return str(d.get("error") or d.get("reason") or inbox.describe(ev))

    def _receipt_delivered(self, ev: Event) -> None:
        mid = ev.detail.get("tell") or ev.detail.get("amend")
        row = self.log.conn.execute("SELECT * FROM events WHERE id = ?", (mid,)).fetchone() if mid else None
        orig = Event.from_row(row) if row else None
        if orig is None or not orig.paths:
            return
        path = self.cfg.path(*rel_for(orig.paths[0]).split("/"))
        try:
            meta, body = split_front_matter(read_text(path))
        except (OSError, FrontMatterError, UnicodeDecodeError):
            return                          # pruned or never written: nothing to add to
        meta = meta or {}
        meta["result"] = f"delivered #{mid} ({ev.detail.get('via')}, after {ev.detail.get('waited_seconds')}s)"
        meta["events"] = [*meta.get("events", []), ev.id]
        lines = [ln for ln in body.strip().splitlines() if ln.strip()] + [self._line(ev)]
        write_text(path, self._render(meta, lines))

    def process_proposed(self) -> None:
        """A receipt for each new or rewritten `proposed/` file: waiting for Matt's approval,
        or refused for what `approve:` would refuse it for. No event: a proposal changes nothing."""
        seen: dict[str, int] = {}
        for p in items.item_files(self.cfg.path("proposed")):
            rel = self.cfg.rel(p)
            try:
                mtime = p.stat().st_mtime_ns
            except OSError:
                continue
            if self.proposed_seen.get(rel) == mtime:
                seen[rel] = mtime
                continue
            if time.time() - mtime / 1e9 < daemon_signals.SIGNAL_SETTLE_SECONDS:
                continue                    # still being written: a later tick
            seen[rel] = mtime
            if rel not in self.proposed_seen and self._receipt_meta(rel).get("written_ms") == mtime // 1_000_000:
                continue                    # its receipt was written before a restart
            repo = items.front_repo(p)
            if repo not in self.cfg.repos:
                self._write_receipt(rel, [], reason=f"`repo: {repo}` is not a configured repo")
            elif errors := items.parse(p, repo).errors:
                self._write_receipt(rel, [], reason="; ".join(errors))
            else:
                self._write_receipt(rel, [], result=f"proposed for {repo}: waits for Matt (`approve: {p.stem}`)")
        self.proposed_seen = seen

    def _pending_receipt(self, rel: str, waits: str) -> None:
        """`outcome: pending` for a request still waiting (so the planner can tell waiting from
        lost), written once per thing waited for; the final receipt replaces it."""
        if self._pending.get(rel) != waits:
            self._pending[rel] = waits
            self._write_receipt(rel, [], pending=f"waiting for {waits}")

    def _receipt_meta(self, rel: str) -> dict:
        try:
            return split_front_matter(read_text(self.cfg.path(*rel_for(rel).split("/"))))[0] or {}
        except (OSError, FrontMatterError, UnicodeDecodeError):
            return {}

    def _line(self, ev: Event) -> str:
        return f"- #{ev.id} {clock(ev.ts)} {inbox.describe(ev)}"

    def _render(self, meta: dict, lines: list[str]) -> str:
        head = "\n".join(f"{k}: {_q(v)}" for k, v in meta.items() if v is not None)
        return f"---\n{head}\n---\n" + "".join(f"{ln}\n" for ln in lines)

    def _write_receipt(self, rel: str, evs: list[Event], result: str | None = None,
                       reason: str | None = None, applied: list | None = None, pending: str | None = None) -> None:
        """`outcome: accepted` with what happened (`result`), `refused` with `reason` (and,
        for a signal cut short, the lines `applied` before it), or `pending` with what it
        waits for (`result`); then one line per event."""
        if not pending:
            self._pending.pop(rel, None)
        try:
            mtime = self.cfg.path(*rel.split("/")).stat().st_mtime_ns // 1_000_000
        except OSError:
            mtime = None                    # a signal or request is gone by now; its name is unique anyway
        outcome = "refused" if reason else "pending" if pending else "accepted"
        meta = {"write": rel, "outcome": outcome, "result": pending or result,
                "reason": reason, "applied": applied, "at": clock(now()), "written_ms": mtime,
                "events": [e.id for e in evs]}
        lines = [self._line(e) for e in evs[:LINES]]
        if len(evs) > LINES:                # a `session:` warns once per unreviewed report
            lines.append(f"- … and {len(evs) - LINES} more (every id is in `events`)")
        write_text(self.cfg.path(*rel_for(rel).split("/")), self._render(meta, lines))
        self._prune_receipts()

    def _prune_receipts(self) -> None:
        if time.time() - self._pruned_at < PRUNE_EVERY:
            return
        self._pruned_at = time.time()
        cutoff = time.time() - KEEP_SECONDS
        for p in self.cfg.path(ROOT).rglob("*.md"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass
