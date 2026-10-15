"""The `ask` MCP server: one stdio server per executor, launched by Claude Code from the
`--mcp-config` conductor writes for the item.

    python -m conductor.ask_server --control <folder> --item <repo/NNN_name> --default-minutes 10

One tool, `ask(question, options?, default?, block?)`. A call writes the question file,
then holds the tool call open until the question resolves: an answer file appears, or
(with a default and `block` false) the default deadline passes. JSON-RPC 2.0 over
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

from . import questions

POLL_SECONDS = 1.0
# Claude Code aborts a tool call that sends no response or progress for 30 minutes
# (its MCP idle timeout), so a waiting ask reports progress well inside that.
PROGRESS_SECONDS = 60.0
SERVER_NAME = "conductor"
TOOL_NAME = "ask"

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
            self.reply(mid, {"tools": [TOOL]})
        elif method == "tools/call":
            if params.get("name") != TOOL_NAME:
                self.error(mid, -32602, f"unknown tool {params.get('name')!r}")
                return
            cancel = threading.Event()
            self._cancel[mid] = cancel
            token = (params.get("_meta") or {}).get("progressToken")
            threading.Thread(target=self._call, args=(mid, params.get("arguments") or {}, cancel, token),
                             daemon=True).start()
        elif method == "notifications/cancelled":
            if ev := self._cancel.get(params.get("requestId")):
                ev.set()
        elif method == "ping":
            self.reply(mid, {})
        elif mid is not None:
            self.error(mid, -32601, f"method not found: {method}")
        # other notifications need no reply

    def _call(self, mid, args: dict, cancel: threading.Event, token=None) -> None:
        try:
            text, is_error = self.ask(args, cancel, token)
        except Exception as exc:          # never leave a call hanging on a bug
            text, is_error = f"ask failed: {exc}", True
        self._cancel.pop(mid, None)
        if text is not None:
            self.reply(mid, {"content": [{"type": "text", "text": text}], "isError": is_error})

    # ------------------------------------------------------------- the tool
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
