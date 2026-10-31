"""The `ask` MCP server: one stdio server per executor, launched by Claude Code from the
`--mcp-config` conductor writes for the item.

    python -m conductor.ask_server --control <folder> --item <repo/NNN_name> --default-minutes 10

Four tools. `ask(question, options?, default?, block?)` writes the question file, then
holds the tool call open until the question resolves: an answer file appears, or (with
a default and `block` false) the default deadline passes; a message for the item hands
the call back early with the question still open, and `ask(resume=<id>)` waits on. `progress(note, eta?, done?,
remaining?, outline?)` appends a progress line (`talk.record_progress`) and returns at once; it
hands out no waiting message (the PostToolUse hook after it does, on the lead's call only).
`wait(minutes?)` is how a paused item idles: it returns once a message waits for the item
(the hook after it delivers it) or the minutes pass. `wait_for(path | pid | log + line, every?,
minutes?)` replaces an executor's shell polls: it checks every ~50s and returns when the
target changes, a message waits, or the minutes pass, saying what changed. JSON-RPC 2.0 over
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

from . import questions, talk, waitfor

POLL_SECONDS = 1.0
# Claude Code aborts a tool call that sends no response or progress for 30 minutes
# (its MCP idle timeout), so a waiting ask reports progress well inside that.
PROGRESS_SECONDS = 60.0
SERVER_NAME = "conductor"
TOOL_NAME = "ask"
PROGRESS_NAME = "progress"
WAIT_NAME = "wait"
WAIT_FOR_NAME = "wait_for"
OUTLINE_ROWS = 12               # the preamble asks for ~8; a few more are taken, not refused
WAIT_MINUTES = 10               # `wait`'s default; at most WAIT_MAX_MINUTES, inside Claude Code's MCP idle timeout
WAIT_MAX_MINUTES = 20
WAIT_FOR_EVERY = 50             # seconds between `wait_for`'s checks of its target; mail is looked at each POLL_SECONDS

TOOL = {
    "name": TOOL_NAME,
    "description": (
        "Ask Matt (via the project's planner) a question and wait for the answer. Ask only "
        "when a wrong guess would waste real work or be hard to undo. Give `default` "
        "whenever a reasonable one exists: if nobody answers within the default window "
        "(minutes, set by conductor) the call returns the default and you carry on with it. "
        "Set `block` true only when every default would be worse than waiting; a blocking "
        "call waits until answered, however long, and that time does not count toward your "
        "time limit. With no default the call blocks. If `default` contradicts a line of the "
        "prompt, start `question` with the line `default overrides prompt: <that line>`. A "
        "message from Matt arriving while you "
        "wait returns the call early with the question still open: act on the message, then "
        "call `ask` with `resume` set to the question's id to wait for its answer again."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The question, self-contained: what you need decided and why."},
            "options": {"type": "array", "items": {"type": "string"}, "description": "Candidate answers, if any."},
            "default": {"type": "string", "description": "What you will do if nobody answers in time."},
            "block": {"type": "boolean", "description": "Wait until answered instead of falling back to the default.", "default": False},
            "resume": {"type": "string", "description": ("The id of your own question a message cut short: wait for "
                                                          "its answer again (its default and deadline stand; give no other field).")},
        },
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


WAIT_FOR_TOOL = {
    "name": WAIT_FOR_NAME,
    "description": (
        "Wait for background work without polling: until `path` exists, `pid` exits, or a line "
        "matching `line` appears in `log`. Give exactly one of `path`, `pid` or `log`. Returns "
        "with what changed, or early for a message from conductor (delivered as the call "
        "returns), or after `minutes` with how things stand; then call it again if you still "
        "need to. `pid` is a Windows pid: Git Bash's `$!` is not one, so there use a `log` line "
        "or a marker `path` the command writes last."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "A file or folder to wait for, absolute or relative to your working directory."},
            "pid": {"type": "integer", "description": "A process to wait out."},
            "log": {"type": "string", "description": "A log file to read, absolute or relative to your working directory; lines already in it count."},
            "line": {"type": "string", "description": "With `log`: the regular expression a line must match."},
            "every": {"type": "number", "description": f"Seconds between checks (default {WAIT_FOR_EVERY})."},
            "minutes": {"type": "number", "description": f"At most this long (default and most: {WAIT_MAX_MINUTES})."},
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
                 out=sys.stdout, workdir: Path | None = None):
        self.control, self.item = control, item
        self.workdir = workdir or Path.cwd()
        self.default_minutes, self.session_id = default_minutes, session_id
        self.out = out
        self._lock = threading.Lock()
        self._cancel: dict = {}
        self._announced: set[str] = set()       # mail files an `ask` already returned early for
        self._away: dict[str, float] = {}       # question id → seconds spent off the call, on a message
        self._away_since: dict[str, float] = {}
        self._running: list[str] | None = None  # what it last heard runs beside it

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
            self.reply(mid, {"tools": [TOOL, PROGRESS_TOOL, WAIT_TOOL, WAIT_FOR_TOOL]})
        elif method == "tools/call" and params.get("name") == PROGRESS_NAME:
            try:
                text, is_error = self.progress(params.get("arguments") or {})
            except Exception as exc:      # never leave a call hanging on a bug
                text, is_error = f"progress failed: {exc}", True
            self.reply(mid, {"content": [{"type": "text", "text": text}], "isError": is_error})
        elif method == "tools/call":
            if params.get("name") not in (TOOL_NAME, WAIT_NAME, WAIT_FOR_NAME):
                self.error(mid, -32602, f"unknown tool {params.get('name')!r}")
                return
            cancel = threading.Event()
            self._cancel[mid] = cancel
            token = (params.get("_meta") or {}).get("progressToken")
            fn = {TOOL_NAME: self.ask, WAIT_NAME: self.wait, WAIT_FOR_NAME: self.wait_for}[params["name"]]
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
        return "Logged; carry on." + self._running_now(), False

    def _running_now(self) -> str:
        """The preamble's *Running beside you now* line, when what runs beside it changed."""
        if self._running is None:
            self._running = talk.told_beside(self.control, self.item)
        if self._running is None:                   # a watch, or no run record
            return ""
        now = talk.running_beside(self.control, self.item)
        if now == self._running:
            return ""
        self._running = now
        return f" Running beside you now: {', '.join(now) or 'nothing'}."

    def _resume(self, ref) -> tuple[questions.Question | None, str | None]:
        if not isinstance(ref, str) or not ref.strip():
            return None, "`resume` must be the id of your open question"
        q = questions.load(questions.qdir(self.control) / f"{ref.strip()}.md")
        if q is None or q.item != self.item:
            return None, f"no question {ref.strip()!r} of this item to resume"
        if (since := self._away_since.pop(q.id, None)) is not None:
            self._away[q.id] = self._away.get(q.id, 0.0) + time.time() - since
        questions.set_away(q, False)
        return q, None

    def ask(self, args: dict, cancel: threading.Event, token=None) -> tuple[str | None, bool]:
        if args.get("resume") is not None:
            q, why = self._resume(args["resume"])
            if why:
                return why, True
        else:
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
            away = {"away_seconds": round(self._away[q.id])} if self._away.get(q.id) else {}
            if cancel.is_set():
                questions.resolve(q, "cancelled", None, "server", reason="the tool call was cancelled", **away)
                return None, False
            q = questions.load(q.path) or q
            if q.result:
                return self._text(q), q.result.get("outcome") not in ("answered", "default")
            answer = questions.read_answer(q)
            if answer is not None:
                questions.resolve(q, "answered", answer, "server", **away)
                continue
            if deadline is not None and time.time() >= deadline:
                questions.resolve(q, "default", q.default, "server", **away)
                continue
            # A message is never an answer: the call hands back so the hook after it delivers
            # the message, and the question stays open for `resume`. Each mail file cuts a wait
            # short once, so a helper's ask (the hook never delivers to one) can't spin.
            if fresh := [p.name for p in talk.pending(self.control, self.item) if p.name not in self._announced]:
                self._announced.update(fresh)
                self._away_since[q.id] = time.time()
                questions.set_away(q, True)
                return self._cut_short(q), False
            time.sleep(POLL_SECONDS)

    def _cut_short(self, q: questions.Question) -> str:
        when = (f"its default still applies at {datetime.fromisoformat(q.deadline).strftime('%I:%M%p').lstrip('0').lower()}"
                if q.deadline else "it blocks until answered")
        return (f"{q.id} is still open: nobody has answered it. A message from conductor is waiting and "
                f"reaches you as this call returns. Act on it (it is not the answer), then call `ask` with "
                f"`resume: \"{q.id}\"` to wait for the answer again; {when}.")

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

    def wait_for(self, args: dict, cancel: threading.Event, token=None) -> tuple[str | None, bool]:
        """Return when the target changes, a message waits (once per mail file, as `ask`: a
        helper's call must not spin on mail the hook won't give it), or after `minutes`."""
        target, why = waitfor.target(args, self.workdir)
        if why:
            return why, True
        every, minutes = args.get("every", WAIT_FOR_EVERY), args.get("minutes", WAIT_MAX_MINUTES)
        for name, value in (("every", every), ("minutes", minutes)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                return f"`{name}` must be a positive number", True
        started = last_progress = time.time()
        end, checked = started + min(minutes, WAIT_MAX_MINUTES) * 60, 0.0
        while not cancel.is_set():
            at = time.time()
            if at - checked >= every:
                checked = at
                if hit := target.check():
                    return f"{hit} (after {round(at - started)}s).", False
            if fresh := [p.name for p in talk.pending(self.control, self.item) if p.name not in self._announced]:
                self._announced.update(fresh)
                return (f"A message from conductor is waiting: it reaches you as this call returns. Do what it "
                        f"says, then call `wait_for` again if you still need to. So far: {target.state()}."), False
            if at >= end:
                return f"Nothing yet after {round(at - started)}s: {target.state()}.", False
            if token is not None and at - last_progress >= PROGRESS_SECONDS:
                last_progress = at
                self.send({"jsonrpc": "2.0", "method": "notifications/progress", "params": {
                    "progressToken": token, "progress": round(at - started), "message": "waiting"}})
            time.sleep(min(POLL_SECONDS, every))
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
    p.add_argument("--workdir")
    args = p.parse_args(argv)
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    server = Server(Path(args.control), args.item, args.default_minutes, args.session_id,
                    workdir=Path(args.workdir) if args.workdir else None)
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
