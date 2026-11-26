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
import os
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from . import daemon_signals, inbox, items, memory_notes, models, parked, request, session, talk
from .events import Event
from .util import FrontMatterError, clock, now, read_text, split_front_matter, write_text

ROOT = "receipts"
KEEP_SECONDS = 3 * 86400        # a receipt is read within minutes of its write; three days is ample
PRUNE_EVERY = 3600
WRITE_WAKE_SECONDS = 0.25       # how often a poll's sleep looks for a new planner write (`_nap`)
LINES = 8                       # event lines in a receipt's body; the rest are only ids
# Events that mean the write (or a line of it) was not carried out.
REFUSALS = ("invalid", "request_failed", "amend_refused", "late", "open_refused", "pick_failed", "memory_refused")
# The planner's MCP server marks each write before it lands: `receipts/.mcp/<write rel, `/` as
# `~`>`. Every event the write logs carries `via: mcp`, its receipt too, and the receipt
# takes the mark away; a bridge write is one without it (docs/mcp.md, *Attribution*).
MARKS = ".mcp"
VIA = "mcp"


def mark_path(control: Path, write_rel: str) -> Path:
    return Path(control) / ROOT / MARKS / write_rel.strip("/").replace("/", "~")


def mark(control: Path, write_rel: str) -> None:
    """Say that `write_rel` (a signal, request folder, queue / proposed / backlog item,
    amendment, answer or parked file) is about to be written by the planner's server."""
    write_text(mark_path(control, write_rel), f"{now().isoformat(timespec='seconds')}\n")


def rel_for(write_rel: str) -> str:
    """`signals/planner-X.signal` → `receipts/signals/planner-X.md`; `queue/app/012_x.md` →
    `receipts/queue/app/012_x.md`; `requests/<stamp>` → `receipts/requests/<stamp>.md`."""
    p = PurePosixPath(write_rel.rstrip("/").removesuffix(".bad"))
    name = next((p.name.removesuffix(s) for s in (".signal", ".md") if p.name.endswith(s)), p.name)
    return (PurePosixPath(ROOT) / p.parent / f"{name}.md").as_posix()


def _settles(path: str) -> bool:
    """A write taken only once it has stopped changing: a signal or an amendment."""
    name = os.path.basename(path)
    if os.path.basename(os.path.dirname(path)) == "signals":
        return not name.endswith((".bad", ".tmp"))
    return ".amend" in name


def tell_result(ev: Event) -> str:
    """Where a tell went: never a bare `applied` (`delivered #N` or `not delivered` follows)."""
    if q := ev.detail.get("answers"):         # before 58 a tell could answer a question
        return f"delivered as the answer to {q}"
    if q := ev.detail.get("waiting_on"):
        return (f"message #{ev.id} to {ev.item}: it waits on question {q}, whose call hands back for "
                f"the message; {q} stays open (only an answer file answers it)")
    return f"message #{ev.id} to {ev.item}: delivered after its next tool call"


def model_result(ev: Event) -> str:
    """A `model:` signal's receipt: what the setting is now, and what it reaches."""
    d = ev.detail
    return (f"model is now {models.describe(d.get('model'))} (was {models.LABELS.get(d.get('was'), d.get('was'))}): "
            "every project's next new session runs on it; running items, and staged ones when they "
            "resume, keep theirs")


def _q(value) -> str:
    return json.dumps(value, ensure_ascii=False)     # a JSON scalar or list is valid YAML


class ReceiptsMixin:
    def _init_receipts(self) -> None:
        self._capturing: list[Event] | None = None  # events of the signal file or request being handled
        self._capturing_rel: str | None = None      # that write's path
        self._noted: list[str] | None = None        # its no-op lines' notes (`_noop`): receipt only, no event
        self._pruned_at = 0.0
        self.proposed_seen: dict[str, int] = {}     # proposed file → the mtime its receipt covers
        self.parked_seen: dict[str, int] = {}       # parked file → the same
        self._pending: dict[str, str] = {}          # waiting request → what its pending receipt says
        self._woke_for: set[tuple[str, int]] = set()    # settled files a nap already cut short for (`_nap`)

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
        notes: list[str] = []
        prev, self._capturing = self._capturing, got
        prev_notes, self._noted = self._noted, notes
        prev_rel, self._capturing_rel = self._capturing_rel, rel
        try:
            yield got
        finally:
            self._capturing, self._noted, self._capturing_rel = prev, prev_notes, prev_rel
        if got or always:
            refusal = next((e for e in got if e.kind in REFUSALS and (e.kind != "invalid" or rel in e.paths)), None)
            if refusal:
                self._write_receipt(rel, got, reason=self._reason(refusal), applied=refusal.detail.get("applied"))
            else:
                commit = next((e for e in got if e.kind == "commit"), None)
                request = next((e for e in got if e.kind == "request"), None)
                tells = [e for e in got if e.kind == "tell"]
                model = next((e for e in got if e.kind == "model"), None)
                parks = [e for e in got if e.kind == "parked_edit"]
                result = (f"committed {commit.detail.get('sha', '')[:8]}" if commit else
                          model_result(model) if model else
                          "; ".join(map(self._parked_result, parks)) if parks else
                          "nothing to commit: every target is identical to HEAD (was the edited copy sent? "
                          "resend it from a fresh workspace path)"
                          if request and request.detail.get("identical") else
                          "applied; nothing changed" if request else
                          "; ".join(map(tell_result, tells)) if tells else "applied")
                if notes:                       # 129: a tidy-up already true is a no-op, said here only
                    result = "; ".join(([result] if got else []) + notes)
                # A cancel with queued dependents says so: each now holds by itself.
                warning = "; ".join(e.detail["warning"] for e in got if e.kind == "cancel" and e.detail.get("warning"))
                if request and (fold := session.state_warning(request.detail)):     # 106: fold before it grows
                    warning = "; ".join(filter(None, [warning, fold]))
                self._write_receipt(rel, got, result=result, warning=warning or None)

    def _marked(self) -> set[str]:
        """The writes the planner's server has marked and whose receipt hasn't taken the mark."""
        try:
            return {n.replace("~", "/") for n in os.listdir(self.cfg.path(ROOT, MARKS))}
        except OSError:
            return set()

    def _mark_via(self, kind: str, item: str | None, paths: list[str], detail: dict) -> None:
        """`EventLog.before_append`: an event of a marked write (its own path, the signal file
        or request being handled, or the name a renumbered item was written under) carries
        `via: mcp`."""
        if "via" in detail or not (marks := self._marked()):
            return
        rels = [*paths, *([self._capturing_rel] if self._capturing_rel else [])]
        if (old := detail.get("renumbered_from")) and item:
            rels.append(f"queue/{item.split('/', 1)[0]}/{old}.md")
        if any(r.strip("/") in marks for r in rels):
            detail["via"] = VIA

    def _planner_write(self, rel: str, what: str, repo: str | None = None) -> list[Event]:
        """A `planner_write` event for a marked proposed, parked or backlog write, the kinds
        that otherwise log none, so the server's writes of them show in the log too."""
        if rel not in self._marked():
            return []
        return [self.log.append("planner_write", repo=repo, paths=[rel], what=what)]

    def _noop(self, note: str) -> None:
        """A signal line whose wish already holds (`ack:` of a clear id, `reviewed:` of a reviewed
        item, ...): accepted, nothing logged, so no notice; its receipt's `result` says so (129)."""
        if self._noted is not None:
            self._noted.append(note)

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
                starts = f"; {d['starts']}" if d.get("starts") else ""
                starts += f"; {d['parked']}" if d.get("parked") else ""
                if old := d.get("renumbered_from"):     # the receipt stays at the name written (115)
                    rel = f"queue/{ev.repo}/{old}.md"
                    starts += (f"; renumbered from {old}: its number was already "
                               f"{', '.join(d.get('number_taken_by') or [])}'s")
                self._write_receipt(rel, [ev], result=f"queued as {ev.item}{starts}" + (
                    f"; its number is also {', '.join(shared)}'s: name it in full, never by number"
                    if shared else ""), warning=d.get("warning"))
            case "edited" if d.get("identical"):        # 128: the same bytes again
                self._write_receipt(rel, [ev], result=(
                    "rewritten with identical bytes: nothing changed, and it runs the text it had"), warning=(
                    "the write changed nothing (was the edited copy sent? resend it from a fresh workspace "
                    "path, or amend instead)"))
            case "edited":
                how = "the launch it raced was undone; " if d.get("launching") else ""
                how += "its stage is redone" if d.get("staged") else "it runs the new text"
                how += f" ({'; '.join(d['changes'])})" if d.get("changes") else ""     # 128: what conductor now holds
                if names := d.get("choices"):          # one name before 150, a list since
                    names = [names] if isinstance(names, str) else names
                    how += f"; it carries picker{'s' if len(names) > 1 else ''} {', '.join(names)}'s saved choices"
                how += f"; now {d['starts']}" if d.get("starts") else ""     # only when the start moved
                how += f"; {talk.amends_line(d['amendments'])}" if d.get("staged") and d.get("amendments") else ""
                self._write_receipt(rel, [ev], result=f"rewritten: {how}", warning=d.get("warning"))
            case "invalid" if ev.item and rel.startswith("queue/"):
                self._write_receipt(rel, [ev], reason=self._reason(ev))
            case "amended":
                self._write_receipt(rel, [ev], result=f"amendment #{ev.id}, {d.get('state')}: {d.get('delivery')}")
            case "answer":
                flagged = f"; its default overrode the prompt: {o}" if (o := ev.detail.get("overrides_prompt")) else ""
                self._write_receipt(rel, [ev], result=f"answered: the executor has it{flagged}")
            case "amend_refused" | "late":
                self._write_receipt(rel, [ev], reason=self._reason(ev))
            case "delivered":
                self._receipt_delivered(ev)
            case "warning" if d.get("tell"):
                self._receipt_delivered(ev, f"not delivered: message #{d['tell']}: the executor exited first")

    def _reason(self, ev: Event) -> str:
        d = ev.detail
        if ev.kind == "late":
            return f"the question had already resolved ({d.get('outcome')}); the executor never saw this answer"
        return str(d.get("error") or d.get("reason") or inbox.describe(ev))

    def _receipt_delivered(self, ev: Event, result: str | None = None) -> None:
        """A delivery (or, with `result`, a failure to deliver) onto its message's receipt."""
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
        meta["result"] = result or f"delivered #{mid} ({ev.detail.get('via')}, after {ev.detail.get('waited_seconds')}s)"
        meta["events"] = [*meta.get("events", []), ev.id]
        lines = [ln for ln in body.strip().splitlines() if ln.strip()] + [self._line(ev)]
        write_text(path, self._render(meta, lines))

    def _nap(self) -> None:
        """One poll's sleep, cut short by any planner write (a request, signal, queue item or
        amendment, proposal, answer or backlog file, new or rewritten), and by a file still
        settling (`daemon_signals.signal_settle`) coming of age: every kind's receipt is there
        about a second after the write, not a poll or two later (Word Warriors session 11).
        A settling file that came of age during the tick (a signal written with a request,
        too young when the tick began) ends it at once: Word Warriors session 12 saw such a
        signal's receipt a whole poll after its request's."""
        before, pending = self._planner_writes(), request.pending(self.cfg)
        settling = {p: m for p, m in before.items() if _settles(p)}
        ripe = {(p, m) for p, m in settling.items() if m / 1e9 + daemon_signals.signal_settle(os.path.basename(p)) <= time.time()}
        if ripe - self._woke_for:           # once per file version: one a later tick can't take yet won't spin
            self._woke_for = ripe
            return
        self._woke_for = ripe
        end = time.monotonic() + self.cfg.poll_seconds
        ages = [m / 1e9 + daemon_signals.signal_settle(os.path.basename(p)) for p, m in before.items()]
        if young := [t for t in ages if t > time.time()]:
            end = min(end, time.monotonic() + min(young) - time.time() + 0.05)
        while (left := end - time.monotonic()) > 0:
            time.sleep(min(left, WRITE_WAKE_SECONDS))
            if request.pending(self.cfg) != pending or self._planner_writes() != before:
                return

    def _planner_writes(self) -> dict[str, int]:
        """Each entry of the folders the planner writes into, by path → mtime (ns). A request
        folder's mtime moves as its files are added."""
        dirs = [self.cfg.path(d) for d in ("requests", "signals", "proposed", "questions", parked.ROOT,
                                           memory_notes.ROOT)]
        dirs += [self.cfg.path(top, repo) for top in ("queue", "backlog") for repo in self.cfg.repos]
        out = {}
        for d in dirs:
            try:
                with os.scandir(d) as it:
                    for e in it:
                        out[e.path] = e.stat().st_mtime_ns
            except OSError:
                continue
        return out

    def process_proposed(self) -> None:
        """A receipt for each new or rewritten `proposed/` file: waiting for Matt's approval,
        or refused for what `approve:` would refuse it for. No event (a proposal changes
        nothing) but the MCP server's `planner_write`."""
        fresh, self.proposed_seen = self._new_versions(items.item_files(self.cfg.path("proposed")), self.proposed_seen)
        for p, rel in fresh:
            repo = items.front_repo(p)
            evs = self._planner_write(rel, "proposed", repo if repo in self.cfg.repos else None)
            if repo not in self.cfg.repos:
                self._write_receipt(rel, evs, reason=f"`repo: {repo}` is not a configured repo")
            elif errors := items.parse(p, repo).errors:
                self._write_receipt(rel, evs, reason="; ".join(errors))
            else:
                self._write_receipt(rel, evs, result=f"proposed for {repo}: waits for Matt (`approve: {p.stem}`)")

    def process_parked(self) -> None:
        """A receipt for each new or rewritten `parked/<repo>.md`: how many edits wait there
        (`parked.md`). No event but the MCP server's `planner_write`; conductor's own take or
        put-back rewrites it too."""
        files = [p for p in sorted(self.cfg.path(parked.ROOT).glob("*.md")) if not p.name.endswith(".done.md")]
        fresh, self.parked_seen = self._new_versions(files, self.parked_seen)
        for p, rel in fresh:
            if p.stem not in self.cfg.repos:
                self._write_receipt(rel, self._planner_write(rel, "parked"), reason=f"`{p.stem}` is not a configured repo")
                continue
            evs = self._planner_write(rel, "parked", p.stem)
            n, rules = parked.counts(self.cfg.control, p.stem)
            tagged = f" ({rules} `[rules]`)" if rules else ""
            self._write_receipt(rel, evs, result=f"{n} parked edit(s) waiting for {p.stem}{tagged}: "
                                                 f"{self._parked_carrier(p.stem)}; waiting: "
                                                 f"{parked.listing(parked.waiting(self.cfg.control, p.stem))}")

    def _parked_result(self, ev: Event) -> str:
        """A `park:` line's receipt (144): what changed, then every line that waits after."""
        d = ev.detail
        did = {"add": f"parked `{d.get('line')}`", "remove": f"removed `{d.get('was')}`",
               "replace": f"replaced `{d.get('was')}` with `{d.get('line')}`"}.get(d.get("op"), d.get("op"))
        waits = [parked.Edit(t, t.lower().startswith(parked.TAG)) for t in d.get("waiting") or []]
        return (f"{did}; {len(waits)} parked edit(s) now wait for {ev.repo}: {parked.listing(waits)}; "
                f"{self._parked_carrier(ev.repo)}")

    def process_memory_edits(self) -> None:
        """Each `memory-edits/<stamp>.md` the planner's `memory_note` wrote: applied to the repo's
        executor memory under `~/.claude` (`memory_notes`, 143; no executor may write there),
        logged `memory_note` or `memory_refused`, its receipt written, the request deleted."""
        for p in memory_notes.take(self.cfg.control):
            rel = self.cfg.rel(p)
            try:
                text = read_text(p)
            except (OSError, UnicodeDecodeError):
                continue                        # still being written: a later tick
            try:
                meta, body = memory_notes.parse(text)
                detail = memory_notes.apply(self.cfg.repos, meta, body)
            except memory_notes.NoteRefused as exc:
                ev = self.log.append("memory_refused", paths=[rel], reason=str(exc))
                self._write_receipt(rel, [ev], reason=str(exc))
            else:
                ev = self.log.append("memory_note", repo=detail.pop("repo"), paths=[rel], **detail)
                self._write_receipt(rel, [ev], result=f"{ev.repo} memory note {detail['note']}: {detail['result']}")
            p.unlink(missing_ok=True)

    def _parked_carrier(self, repo: str) -> str:
        """Who will carry the parked edits: the first queued item that would take any, by name,
        never a template (142: fractals was told "the next serial item" with a template next)."""
        edits = parked.waiting(self.cfg.control, repo)
        for f in items.item_files(self.cfg.path("queue", repo)):
            item = items.parse(f, repo)
            if not item.errors and parked.eligible(item, edits):
                return f"{item.id} carries them at its launch"
        return "no queued item carries them yet: the next serial one queued will (not a template, readonly or alongside one)"

    def _new_versions(self, paths: list[Path], seen: dict[str, int]) -> tuple[list[tuple[Path, str]], dict[str, int]]:
        """The readable files among `paths` that are new or rewritten since `seen` (rel → the
        mtime its receipt covers), as (path, rel), and the new `seen`. No settle delay, as for
        a queue item: a file its writer still holds (unreadable on Windows) waits for a later
        tick, and a rewrite gets a new receipt. One whose receipt predates a restart is not new."""
        fresh, now_seen = [], {}
        for p in paths:
            rel = self.cfg.rel(p)
            try:
                mtime = p.stat().st_mtime_ns
            except OSError:
                continue
            if seen.get(rel) == mtime:
                now_seen[rel] = mtime
                continue
            try:
                p.read_bytes()
            except OSError:
                continue
            now_seen[rel] = mtime
            if rel not in seen and self._receipt_meta(rel).get("written_ms") == mtime // 1_000_000:
                continue
            fresh.append((p, rel))
        return fresh, now_seen

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
                       reason: str | None = None, applied: list | None = None, pending: str | None = None,
                       warning: str | None = None) -> None:
        """`outcome: accepted` with what happened (`result`) and any `warning` (a queue item
        whose `depends_on` will hold it), `refused` with `reason` (and, for a signal cut short,
        the lines `applied` before it), or `pending` with what it waits for (`result`); then
        one line per event."""
        via = None
        if not pending:
            self._pending.pop(rel, None)
            mark = mark_path(self.cfg.control, rel)
            if mark.exists():               # the server's write: said, and the mark taken away
                via = VIA
                mark.unlink(missing_ok=True)
        try:
            mtime = self.cfg.path(*rel.split("/")).stat().st_mtime_ns // 1_000_000
        except OSError:
            mtime = None                    # a signal or request is gone by now; its name is unique anyway
        outcome = "refused" if reason else "pending" if pending else "accepted"
        meta = {"write": rel, "outcome": outcome, "result": pending or result, "warning": warning,
                "reason": reason, "applied": applied, "at": clock(now()), "written_ms": mtime, "via": via,
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
        marks = self.cfg.path(ROOT, MARKS)      # a mark no receipt took (a write that never landed)
        for p in [*self.cfg.path(ROOT).rglob("*.md"), *(marks.iterdir() if marks.is_dir() else [])]:
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass
