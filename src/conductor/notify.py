"""Desktop notifications. Windows toasts through WinRT (`windows-toasts`); elsewhere none.

Only what Matt acts on toasts (115, `docs/inbox.md`, *Toasts*): an item finishing (its queue
state merged in), something that waits on him, and errors. Everything else is panel only.
Toasts for the project within about a minute become one. A toast is best effort: the daemon
logs a failure as a `warning` and carries on.
"""

from __future__ import annotations

import sys
import time
from collections import Counter
from dataclasses import dataclass

APP_TEXT = "Conductor"
SETTLE_SECONDS = 10     # a finish waits this long for its repo's next launch, to name it
WINDOW_SECONDS = 60     # toasts within this of the last one shown become one
BODY_CHARS = 250


class Toaster:
    def __init__(self):
        self._toaster = None

    def __call__(self, title: str, body: str, tag: str) -> None:
        if sys.platform != "win32":
            raise RuntimeError("desktop toasts are implemented for Windows only")
        from windows_toasts import InteractableWindowsToaster, Toast
        if self._toaster is None:
            # The default AUMID (Command Prompt's) is registered on every Windows box, so the
            # toast shows without installing a shortcut; "Conductor" is the attribution line.
            self._toaster = InteractableWindowsToaster(APP_TEXT)
        # The tag (`<project>#<event id>`) is what the Action Center history records.
        toast = Toast([title, body], group="conductor")
        toast.tag = tag[:64]
        self._toaster.show_toast(toast)


@dataclass
class Note:
    """One event worth a toast: `label` counts it in a coalesced title ("2 done, 1 question")."""
    ev_id: int
    label: str          # done | partial | red | question | picker | error | message
    item: str | None
    repo: str | None
    line: str           # what it says, without the project
    body: str = ""
    finish: bool = False
    held: bool = False  # a red finish that holds its queue


def _name(item: str | None, repo: str | None, short: bool) -> str:
    """`051_x` in a one-repo project, `repo/051_x` otherwise."""
    if item and short and "/" in item:
        return item.split("/", 1)[1]
    return item or repo or ""


def note(kind: str, item: str | None, repo: str | None, detail: dict, ev_id: int = 0,
         short: bool = False) -> Note | None:
    """The toast an event raises, or None: panel only (report warnings, questions with a
    default, progress, ETA overruns, load and memory warnings, retros queued, parked edits,
    watch reports, an empty queue, conductor's own maintenance)."""
    who = _name(item, repo, short)

    def err(line: str, body: str = "") -> Note:
        return Note(ev_id, "error", item, repo, line, body[:BODY_CHARS])

    match kind:
        case "finish":
            status = detail.get("status") or "done"
            if detail.get("template") == "watch" and status != "red":
                return None                                   # a watch's report: panel only
            body = "; ".join(detail.get("reasons") or [])
            picks_named = detail.get("picks") or ([detail["pick"]] if detail.get("pick") else [])
            if picks_named:
                body = f"picker ready: {', '.join(picks_named)}" + (f". {body}" if body else "")
            held = status == "red" and detail.get("after_red", "stop") == "stop"
            return Note(ev_id, status if status in ("partial", "red") else "done", item, repo,
                        f"{who} {status}", body[:BODY_CHARS], finish=True, held=held)
        case "ask" if detail.get("block") or detail.get("default") in (None, ""):
            # Held for Matt with no default; a question with a default goes on without him.
            return Note(ev_id, "question", item, repo, f"question from {who}", str(detail.get("text", ""))[:BODY_CHARS])
        case "pick_open" if detail.get("by") not in ("app", "cli"):     # Matt opened it himself
            what = detail.get("name") or detail.get("manifest") or "a picker"
            return Note(ev_id, "picker", item, repo, f"picker `{what}` is open", str(detail.get("url") or ""))
        case "notify":
            return Note(ev_id, "message", item, repo, f"from the {detail.get('by') or 'planner'}",
                        str(detail.get("text", ""))[:BODY_CHARS])
        case "tamper":
            who = who or ", ".join(detail.get("concurrent", [])) or "an executor"
            return err(f"{who} changed the control folder", "Reverted. " + "; ".join(detail.get("changes", [])))
        case "hold":
            return err(f"{who} held", str(detail.get("reason", "")))
        case "checkout_dirty":
            return err(f"{who}'s merge left the main checkout dirty",
                       f"Not the branch's: {', '.join(detail.get('files', [])[:5])}. {detail.get('error', '')}")
        case "run_failed":
            tail = " ⏎ ".join(str(detail.get("log_tail", "")).splitlines()[-3:])
            return err(f"`{detail.get('target')}` did not come up",
                       str(detail.get("error", "")) + (f" — log tail: {tail}" if tail else ""))
        case "pick_failed":
            what = f"`{detail['name']}`" if detail.get("name") else str(detail.get("manifest", ""))
            return err(f"picker {what} did not open", str(detail.get("error", "")))
        case "amend_refused":
            return err(f"amendment to {who} refused", str(detail.get("reason", "")))
        case "request_failed":
            return err(f"request `{detail.get('request')}` refused", str(detail.get("error", "")))
        case "invalid":
            return err(f"{who or 'a file'} is invalid", str(detail.get("error", "")))
        case "push_failed":
            return err(f"push of `{detail.get('target')}` failed", str(detail.get("error", "")))
        case "memory_critical":
            return err("memory CRITICAL",
                       f"{detail.get('free')} GiB free, {detail.get('percent')}% committed. {detail.get('action', '')}")
        case "daemon_died":
            return err("the daemon had died",
                       f"Last heartbeat {detail.get('last_beat_clock', '?')}; nothing ran for {detail.get('gap')}.")
        case "daemon_stop" if detail.get("reason") == "error":
            return err("the daemon stopped on an error", str(detail.get("error", "")))
        case "watch_flag" if detail.get("flag") == "quiet":
            return err(f"{who} looks stuck", str(detail.get("text", "")))   # the watch's stuck-item finding
    return None


def _plural(n: int, label: str) -> str:
    if label in ("done", "partial", "red") or n == 1:
        return f"{n} {label}"
    return f"{n} {label}s"


class Toasts:
    """Collects notes and shows them: a finish after `SETTLE_SECONDS` (so its repo's next
    launch can be named), anything else at once, and nothing within `WINDOW_SECONDS` of the
    last toast shown: those wait and go out as one."""

    def __init__(self, project: str, send, queue_state, short: bool = False, clock=time.monotonic):
        """`send(title, body, tag)`; `queue_state(repo, since_id)` says where a repo's queue
        stands after the finish with event id `since_id` ("052_x started", "queue now empty")."""
        self.project, self.send, self.queue_state, self.short, self.clock = project, send, queue_state, short, clock
        self.pending: list[tuple[float, Note]] = []
        self.last_shown: float | None = None

    def add(self, kind: str, item, repo, detail: dict, ev_id: int) -> None:
        n = note(kind, item, repo, detail, ev_id, self.short)
        if n:
            self.pending.append((self.clock(), n))

    def due(self) -> bool:
        if not self.pending:
            return False
        t = self.clock()
        first = min(at for at, _ in self.pending)
        settle = SETTLE_SECONDS if any(n.finish for _, n in self.pending) else 0
        after = (self.last_shown + WINDOW_SECONDS) if self.last_shown is not None else 0
        return t >= max(first + settle, after)

    def flush(self, force: bool = False) -> None:
        """Show what is due (all of it with `force`). `send` may raise: the notes are gone
        either way, so a broken notifier never piles them up."""
        if not (self.pending and (force or self.due())):
            return
        notes = [n for _, n in self.pending]
        self.pending = []
        self.last_shown = self.clock()
        self.send(*self.render(notes))

    def render(self, notes: list[Note]) -> tuple[str, str, str]:
        lines = []
        last_finish = {n.repo: n.ev_id for n in notes if n.finish}
        for n in notes:
            line = n.line
            if n.finish and last_finish.get(n.repo) == n.ev_id:
                line += " · " + (f"queue `{n.repo}` HELD (`clear: {n.repo}`)" if n.held
                                 else self.queue_state(n.repo, n.ev_id))
            lines.append((line, n.body))
        tag = f"{self.project}#" + ",".join(str(n.ev_id) for n in notes)
        if len(notes) == 1:
            return f"{self.project}: {lines[0][0]}", lines[0][1], tag
        counts = Counter(n.label for n in notes)
        title = f"{self.project}: " + ", ".join(_plural(c, label) for label, c in counts.items())
        body = "\n".join(line + (f": {b}" if b else "") for line, b in lines)
        return title, body[:BODY_CHARS * 2], tag
