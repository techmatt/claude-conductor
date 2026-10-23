"""The `ask` MCP server: one stdio server per executor, launched by Claude Code from the
`--mcp-config` conductor writes for the item.

    python -m conductor.ask_server --control <folder> --item <repo/NNN_name> --default-minutes 10

Three tools. `ask(question, options?, default?, block?)` writes the question file, then
holds the tool call open until the question resolves: an answer file appears, or (with
a default and `block` false) the default deadline passes. `progress(note, eta?, done?,
remaining?, outline?)` appends a progress line (`talk.record_progress`) and returns at once; it
hands out no waiting message (the PostToolUse hook after it does, on the lead's call only).
`wait(minutes?)` is how a paused item idles: it returns once a message waits for the item
(the hook after it delivers it) or the minutes pass. JSON-RPC 2.0 over
newline-delimited stdin/stdout; calls run on their own threads so `ping` and
`notifications/cancelled` are still served while one waits.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from . import questions, talk

POLL_SECONDS = 1.0
# Claude Code aborts a tool call that sends no response or progress for 30 minutes
# (its MCP idle timeout), so a waiting ask reports progress well inside that.
PROGRESS_SECONDS = 60.0
SERVER_NAME = "conductor"
TOOL_NAME = "ask"
PROGRESS_NAME = "progress"
WAIT_NAME = "wait"
OUTLINE_ROWS = 12               # the preamble asks for ~8; a few more are taken, not refused
WAIT_MINUTES = 10               # `wait`'s default; at most WAIT_MAX_MINUTES, inside Claude Code's MCP idle timeout
WAIT_MAX_MINUTES = 20

TOOL = {
    "name": TOOL_NAME,
    "description": (
        "Ask Matt (via the project's planner) a question and wait for the answer. Ask only "
        "when a wrong guess would waste real work or be hard to undo. Give `default` "
        "whenever a reasonable one exists: if nobody answers within the default window "
        "(minutes, set by conductor) the call returns the default and you carry on with it. "
        "Set `block` true only when every default would be worse than waiting; a blocking "
        "call waits until answered, however long, and that time does not count toward your "
        "time limit. With no default the call blocks."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The question, self-contained: what you need decided and why."},
            "options": {"type": "array", "items": {"type": "string"}, "description": "Candidate answers, if any."},
            "default": {"type": "string", "description": "What you will do if nobody answers in time."},
            "block": {"type": "boolean", "description": "Wait until answered instead of falling back to the default.", "default": False},
        },
        "required": ["question"],
    },
}

PROGRESS_TOOL = {
    "name": PROGRESS_NAME,
    "description": (
        "Tell Matt how this item is going, in one short line; returns at once. Call it at "
        "the start (with a first `eta` as soon as you can estimate one), at real milestones, "
        "and whenever your estimate changes materially; never as chatter. Also the way to "
        "answer a message Matt sent you while you work. With `outline` it also posts your "
        "remaining sections with finish times (see the preamble's Outline rule)."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "note": {"type": "string", "description": "What is happening, one line."},
            "eta": {"type": "string", "description": "When you expect to finish: a clock time (5:40pm) or a duration (25m)."},
            "done": {"type": "number", "description": "Units done so far, when the work has countable units."},
            "remaining": {"type": "number", "description": "Units still to do."},
            "outline": {
                "type": "array", "maxItems": OUTLINE_ROWS,
                "description": ("Your remaining plan, coarse: every section of this item in order, the "
                                "report last, at most 8 rows. Titles a few words, a rough finish time "
                                "each, finished ones `done`. No descriptions; `note` may be left out."),
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "A few words."},
                        "eta": {"type": "string", "description": "Local clock time this section should finish (11:05am)."},
                        "done": {"type": "boolean", "default": False},
                    },
                    "required": ["title", "eta"],
                },
            },
        },
    },
}


WAIT_TOOL = {
    "name": WAIT_NAME,
    "description": (
        "Wait, doing nothing, until a message from conductor arrives (or `minutes` pass); it is "
        "delivered as soon as this call returns. Use it only when conductor tells you to wait "
        "(a pause); never to wait for anything else."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "minutes": {"type": "number", "description": f"At most this long (default {WAIT_MINUTES}, "
                                                         f"at most {WAIT_MAX_MINUTES}); then call it again."},
        },
    },
}


def parse_outline(value) -> tuple[list[dict], str | None]:
    """The `outline` argument as stored rows ({title, eta, done}), or an error."""
    if not isinstance(value, list) or not value:
        return [], "`outline` must be a non-empty list of {title, eta, done} rows"
    if len(value) > OUTLINE_ROWS:
        return [], f"`outline` has {len(value)} rows; keep it coarse, at most 8"
    rows = []
    for n, row in enumerate(value, 1):
        if not isinstance(row, dict) or not isinstance(row.get("title"), str) or not row["title"].strip():
            return [], f"`outline` row {n} needs a `title`"
        done = row.get("done", False)
        if not isinstance(done, bool):
            return [], f"`outline` row {n}: `done` must be true or false"
        rows.append({"title": " ".join(row["title"].split())[:80],
                     "eta": " ".join(str(row.get("eta") or "").split())[:20], "done": done})
    return rows, None


class Server:
    def __init__(self, control: Path, item: str, default_minutes: float, session_id: str | None,
                 out=sys.stdout):
        self.control, self.item = control, item
        self.default_minutes, self.session_id = default_minutes, session_id
        self.out = out
        self._lock = threading.Lock()
        self._cancel: dict = {}

    def send(self, obj: dict) -> None:
        with self._lock:
            self.out.write(json.dumps(obj) + "\n")
            self.out.flush()

    def reply(self, mid, result: dict) -> None:
        self.send({"jsonrpc": "2.0", "id": mid, "result": result})

    def error(self, mid, code: int, message: str) -> None:
        self.send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})

    # ------------------------------------------------------------- dispatch
    def handle(self, msg: dict) -> None:
        method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method == "initialize":
            self.reply(mid, {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": "1"},
            })
        elif method == "tools/list":
            self.reply(mid, {"tools": [TOOL, PROGRESS_TOOL, WAIT_TOOL]})
        elif method == "tools/call" and params.get("name") == PROGRESS_NAME:
            try:
                text, is_error = self.progress(params.get("arguments") or {})
            except Exception as exc:      # never leave a call hanging on a bug
                text, is_error = f"progress failed: {exc}", True
            self.reply(mid, {"content": [{"type": "text", "text": text}], "isError": is_error})
        elif method == "tools/call":
            if params.get("name") not in (TOOL_NAME, WAIT_NAME):
                self.error(mid, -32602, f"unknown tool {params.get('name')!r}")
                return
            cancel = threading.Event()
            self._cancel[mid] = cancel
            token = (params.get("_meta") or {}).get("progressToken")
            fn = self.ask if params["name"] == TOOL_NAME else self.wait
            threading.Thread(target=self._call, args=(mid, params.get("arguments") or {}, cancel, token, fn),
                             daemon=True).start()
        elif method == "notifications/cancelled":
            if ev := self._cancel.get(params.get("requestId")):
                ev.set()
        elif method == "ping":
            self.reply(mid, {})
        elif mid is not None:
            self.error(mid, -32601, f"method not found: {method}")
        # other notifications need no reply

    def _call(self, mid, args: dict, cancel: threading.Event, token=None, fn=None) -> None:
        fn = fn or self.ask
        try:
            text, is_error = fn(args, cancel, token)
        except Exception as exc:          # never leave a call hanging on a bug
            text, is_error = f"{fn.__name__} failed: {exc}", True
        self._cancel.pop(mid, None)
        if text is not None:
            self.reply(mid, {"content": [{"type": "text", "text": text}], "isError": is_error})

    # ------------------------------------------------------------- the tools
    def progress(self, args: dict) -> tuple[str, bool]:
        note, outline = args.get("note"), args.get("outline")
        entry = {}
        if outline is not None:
            rows, why = parse_outline(outline)
            if why:
                return why, True
            entry["outline"] = rows
        if note not in (None, "") or not outline:
            if not isinstance(note, str) or not note.strip():
                return "`note` must be a non-empty string (it may be left out only with an `outline`)", True
            entry["note"] = " ".join(note.split())[:500]
        if args.get("eta") not in (None, ""):
            entry["eta"] = str(args["eta"]).strip()[:80]
        for key in ("done", "remaining"):
            if args.get(key) is not None:
                if isinstance(args[key], bool) or not isinstance(args[key], (int, float)):
                    return f"`{key}` must be a number", True
                entry[key] = args[key]
        talk.record_progress(self.control, self.item, entry)
        # Waiting mail is never handed out here: the server can't tell a helper's call from
        # the lead's, and a helper ignores it (Word Warriors 059). The PostToolUse hook that
        # runs right after this call can (`talk.subagent_call`), so it delivers.
        return "Logged; carry on.", False

    def ask(self, args: dict, cancel: threading.Event, token=None) -> tuple[str | None, bool]:
        question = args.get("question")
        if not isinstance(question, str) or not question.strip():
            return "`question` must be a non-empty string", True
        options = args.get("options") or []
        if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
            return "`options` must be a list of strings", True
        default = args.get("default")
        if default is not None and not isinstance(default, str):
            return "`default` must be a string", True
        default = default.strip() if default and default.strip() else None
        block = args.get("block", False)
        if not isinstance(block, bool):
            return "`block` must be true or false", True
        q = questions.create(self.control, self.item, question, options, default, block,
                             self.default_minutes, self.session_id)
        deadline = datetime.fromisoformat(q.deadline).timestamp() if q.deadline else None
        started = last_progress = time.time()
        while True:
            if token is not None and time.time() - last_progress >= PROGRESS_SECONDS:
                last_progress = time.time()
                self.send({"jsonrpc": "2.0", "method": "notifications/progress", "params": {
                    "progressToken": token, "progress": round(last_progress - started),
                    "message": f"waiting for an answer to {q.id}"}})
            if cancel.is_set():
                questions.resolve(q, "cancelled", None, "server", reason="the tool call was cancelled")
                return None, False
            q = questions.load(q.path) or q
            if q.result:
                return self._text(q), q.result.get("outcome") not in ("answered", "default")
            answer = questions.read_answer(q)
            if answer is not None:
                questions.resolve(q, "answered", answer, "server")
                continue
            if deadline is not None and time.time() >= deadline:
                questions.resolve(q, "default", q.default, "server")
                continue
            time.sleep(POLL_SECONDS)

    def wait(self, args: dict, cancel: threading.Event, token=None) -> tuple[str | None, bool]:
        """Return once a message waits in the item's mailbox (the PostToolUse hook right
        after this call delivers it), or after `minutes`."""
        minutes = args.get("minutes", WAIT_MINUTES)
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or minutes <= 0:
            return "`minutes` must be a positive number", True
        started = last_progress = time.time()
        end = started + min(minutes, WAIT_MAX_MINUTES) * 60
        while not cancel.is_set():
            if talk.pending(self.control, self.item):
                return "A message from conductor is waiting: it reaches you as this call returns. Do what it says.", False
            if time.time() >= end:
                return "No message yet. If conductor told you to wait, call `wait` again; do nothing else.", False
            if token is not None and time.time() - last_progress >= PROGRESS_SECONDS:
                last_progress = time.time()
                self.send({"jsonrpc": "2.0", "method": "notifications/progress", "params": {
                    "progressToken": token, "progress": round(last_progress - started), "message": "waiting"}})
            time.sleep(POLL_SECONDS)
        return None, False

    def _text(self, q: questions.Question) -> str:
        r = q.result
        match r.get("outcome"):
            case "answered":
                return f"Answer to {q.id}:\n{r.get('text')}"
            case "default":
                return (f"Nobody answered {q.id} within {self.default_minutes:g} minutes, so the "
                        f"default was taken: {q.default}\nProceed with the default. A later answer "
                        "will not reach you; say in your report that the default was used.")
            case outcome:
                return f"{q.id} was {outcome} without an answer: {r.get('reason', '')}".strip()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="conductor.ask_server")
    p.add_argument("--control", required=True)
    p.add_argument("--item", required=True)
    p.add_argument("--default-minutes", type=float, default=10.0)
    p.add_argument("--session-id")
    args = p.parse_args(argv)
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    server = Server(Path(args.control), args.item, args.default_minutes, args.session_id)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            server.error(None, -32700, "parse error")
            continue
        if isinstance(msg, dict):
            server.handle(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
