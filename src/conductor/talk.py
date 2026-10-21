"""Progress lines and messages into a running executor: the files behind `progress`,
`tell`, `amended` and `delivered`, and the Claude Code hook that delivers messages.

    .conductor/progress/<repo>__<NNN_name>.jsonl     one line per `progress` tool call
    .conductor/mail/<repo>__<NNN_name>/<id>.json     a message waiting (id: its `tell` or
                                                     `amended` event; an amendment may wait
                                                     for a queued item's launch to take it)
    .conductor/mail/<repo>__<NNN_name>/<id>.delivered.json    delivered (adds `via`, `delivered`,
                                                     and `after_seq`, the progress lines by then)
    .conductor/mail/<repo>__<NNN_name>/<id>.undelivered.json  the executor exited first

The ask server appends progress lines; the daemon writes messages and logs `progress`
and `delivered` events from what it finds. Delivery is a rename (`<id>.json` →
`<id>.delivered.json`), so a message reaches the executor once, whichever path takes it.

    python -m conductor.talk hook --control <folder> --item <repo/NNN_name>

is the executor's PostToolUse hook: after every tool call of the lead session (never a
subagent's) it hands any waiting messages to the session as additional context (stdout
JSON), and prints nothing otherwise. A writing call (any agent's) naming paths in the
control folder has them noted in `.conductor/touch/<repo>__<NNN_name>.jsonl`, which the
tamper guard reads to tell which running item changed which tracked file.

    python -m conductor.talk pre --cap <seconds> [--background=<glob> ...]

is its PreToolUse hook on Bash and PowerShell, which keeps every call short enough for a
message to get in: a command matching a `background` glob runs with `run_in_background`,
and any other foreground call's `timeout` is capped (Claude Code moves a command still
running at its timeout to the background rather than killing it).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from fnmatch import fnmatchcase
from pathlib import Path

from .util import clock, parse_stamp, stamp, write_text

HOOK_TIMEOUT_SECONDS = 15
# The `outline:` signal's message: a tell of fixed text, so its reply is the outline.
OUTLINE_REQUEST = ("Post an updated outline now: call `progress` with `outline` (remaining sections, "
                   "titles and finish times only, finished ones done), nothing else.")
SHELL_TOOLS = ("Bash", "PowerShell")
TOUCH_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit", *SHELL_TOOLS)     # tools that may write
_lock = threading.Lock()


def _key(item: str) -> str:
    repo, _, name = item.partition("/")
    return f"{repo}__{name}"


def progress_file(control: Path, item: str) -> Path:
    return Path(control) / ".conductor" / "progress" / f"{_key(item)}.jsonl"


def mail_dir(control: Path, item: str) -> Path:
    return Path(control) / ".conductor" / "mail" / _key(item)


def item_of(key: str) -> str:
    """`repo__NNN_name` → `repo/NNN_name` (repo names never hold `__`)."""
    repo, _, name = key.partition("__")
    return f"{repo}/{name}"


# ---------------------------------------------------------------- progress
def record_progress(control: Path, item: str, entry: dict) -> dict:
    """Append one progress line; `seq` numbers an item's lines from 1."""
    path = progress_file(control, item)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        seq = len(read_progress(control, item)) + 1
        line = {"seq": seq, "at": stamp(), **entry}
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    return line


def read_progress(control: Path, item: str) -> list[dict]:
    try:
        text = progress_file(control, item).read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for ln in text.splitlines():
        try:
            obj = json.loads(ln)
        except ValueError:
            continue                    # a line caught mid-write: read again next time
        if isinstance(obj, dict) and isinstance(obj.get("seq"), int):
            out.append(obj)
    return out


def progress_items(control: Path) -> list[str]:
    d = Path(control) / ".conductor" / "progress"
    return [item_of(p.stem) for p in sorted(d.glob("*.jsonl"))] if d.is_dir() else []


# ---------------------------------------------------------------- messages
def post(control: Path, item: str, msg_id: int, text: str, by: str | None, **extra) -> Path:
    """A message waiting for `item`. An amendment carries `kind: amend` and the `state`
    the item was in; it may wait for a queued or staged item until its launch takes it."""
    path = mail_dir(control, item) / f"{msg_id}.json"
    write_text(path, json.dumps({"id": msg_id, "item": item, "text": text, "by": by, "sent": stamp(), **extra}))
    return path


def is_amend(msg: dict) -> bool:
    return msg.get("kind") == "amend"


def move_mail(control: Path, old: str, new: str) -> None:
    """A reorder renamed queued `old` to `new`: its waiting amendments follow it."""
    for p in pending(control, old):
        try:
            msg = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        post(control, new, msg["id"], msg.get("text", ""), msg.get("by"),
             **{k: v for k, v in msg.items() if k not in ("id", "item", "text", "by")})
        p.unlink(missing_ok=True)


def pending(control: Path, item: str) -> list[Path]:
    d = mail_dir(control, item)
    return sorted((p for p in d.glob("*.json") if p.stem.isdigit()), key=lambda p: int(p.stem)) if d.is_dir() else []


def take(control: Path, item: str, via: str) -> list[dict]:
    """Claim every waiting message for `item` (oldest first) and mark it delivered."""
    out = []
    for p in pending(control, item):
        done = p.with_name(f"{p.stem}.delivered.json")
        try:
            os.replace(p, done)         # the claim: only one deliverer wins
        except OSError:
            continue
        try:
            msg = json.loads(done.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            msg = {"id": int(p.stem), "item": item, "text": ""}
        # The progress lines written by now: the next one is the executor's reply.
        msg |= {"via": via, "delivered": stamp(), "after_seq": len(read_progress(control, item))}
        write_text(done, json.dumps(msg))
        out.append(msg)
    return out


def delivered(control: Path) -> list[dict]:
    """Every delivered message, any item."""
    root = Path(control) / ".conductor" / "mail"
    out = []
    for p in sorted(root.glob("*/*.delivered.json")) if root.is_dir() else []:
        try:
            msg = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue                    # mid-rewrite: next tick
        if isinstance(msg, dict) and "via" in msg:
            out.append(msg)
    return out


def set_aside(control: Path, item: str) -> list[dict]:
    """The executor exited: waiting messages become `.undelivered.json`."""
    out = []
    for p in pending(control, item):
        try:
            msg = json.loads(p.read_text(encoding="utf-8"))
            os.replace(p, p.with_name(f"{p.stem}.undelivered.json"))
        except (OSError, ValueError):
            continue
        out.append(msg)
    return out


def context_text(messages: list[dict]) -> str:
    parts = []
    for m in messages:
        via = f" via {m['by']}" if m.get("by") and m["by"] != "unknown" else ""
        what = ("AMENDMENT #{id} from Matt{via}, an addition to the prompt you are running, sent through "
                "conductor while you work" if is_amend(m) else
                "MESSAGE #{id} from Matt{via}, sent through conductor while you work").format(id=m["id"], via=via)
        parts.append(f"{what} ({m.get('sent', '')}):\n{m.get('text', '')}")
    # The channel the preamble's Messages paragraph names, so the lead reads it as Matt's
    # instructions rather than text a tool injected; a helper that sees it relays it.
    return ("\n\n".join(parts) + "\n\nThis is Matt, the operator, writing through conductor's message channel "
            "(the one your conductor preamble names), not text injected by a tool: treat it as his "
            "instruction. Where it conflicts with the prompt, it wins. Change your plan if it asks you to, "
            "answer it by number with the `progress` tool, and answer it again in your report. If you are a "
            "helper or subagent, not the lead session, pass it word for word straight up to the lead and "
            "do nothing else with it.")


def keep_amendment(path: Path, msg: dict) -> None:
    """Append one amendment, as delivered (or never), to the item's amendments file
    beside its report: what the executor was told, and when."""
    if msg.get("delivered"):
        how = f"delivered {clock(msg['delivered'])} via {msg.get('via')}"
    else:
        how = "never delivered: the executor exited first"
    head = "" if path.is_file() else f"# Amendments to {msg.get('item')}\n\n"
    by = f" by {msg['by']}" if msg.get("by") else ""
    entry = (f"## Amendment #{msg.get('id')}\n\nAmended {clock(msg['sent']) if msg.get('sent') else '?'}{by}, "
             f"while {msg.get('state', '?')}; {how}.\n\n{str(msg.get('text', '')).strip()}\n\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(head + entry)


def reply_ids(detail: dict) -> list[int]:
    """The tells (or running amendments) a `progress` event replies to: `reply_to` is
    one id, or a list when several were delivered before the same line."""
    r = detail.get("reply_to")
    return [r] if isinstance(r, int) else [x for x in r if isinstance(x, int)] if isinstance(r, list) else []


def waited(msg: dict) -> int | None:
    try:
        return round((parse_stamp(msg["delivered"]) - parse_stamp(msg["sent"])).total_seconds())
    except (KeyError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------- the hook
def hook_settings(python: str, control: Path, item: str, cap_seconds: float = 0,
                  background: list[str] = ()) -> str:
    """The executor's `--settings`: a PostToolUse hook on every tool, and with a cap or
    `background` globs, the PreToolUse hook on the shell tools."""
    hooks = {"PostToolUse": [{"matcher": "", "hooks": [{
        "type": "command", "command": python, "timeout": HOOK_TIMEOUT_SECONDS,
        "args": ["-m", "conductor.talk", "hook", "--control", str(control), "--item", item],
    }]}]}
    if cap_seconds > 0 or background:
        hooks["PreToolUse"] = [{"matcher": "|".join(SHELL_TOOLS), "hooks": [{
            "type": "command", "command": python, "timeout": HOOK_TIMEOUT_SECONDS,
            "args": ["-m", "conductor.talk", "pre", "--cap", f"{cap_seconds:g}",
                     *(f"--background={g}" for g in background)],
        }]}]
    return json.dumps({"hooks": hooks}, indent=1)


def backgrounds(command: str, globs: list[str]) -> bool:
    """Does a shell command match one of a repo's `background` globs (whole command,
    case-sensitive, `*` crossing spaces)?"""
    text = " ".join(command.split())
    return any(fnmatchcase(text, g) for g in globs)


def pre_tool(event: dict, cap_seconds: float, background: list[str]) -> dict | None:
    """The tool input a shell call should run with instead, or None to leave it be."""
    if event.get("tool_name") not in SHELL_TOOLS or not isinstance(event.get("tool_input"), dict):
        return None
    tool = dict(event["tool_input"])
    if tool.get("run_in_background") or not isinstance(tool.get("command"), str):
        return None
    if backgrounds(tool["command"], background):
        tool.pop("timeout", None)
        return tool | {"run_in_background": True}
    cap = round(cap_seconds * 1000)
    timeout = tool.get("timeout")
    if cap <= 0 or (isinstance(timeout, (int, float)) and 0 < timeout <= cap):
        return None
    return tool | {"timeout": cap}       # no timeout means Claude Code's default, 2 minutes


def pre_hook(event: dict, cap_seconds: float, background: list[str]) -> str | None:
    """What the PreToolUse hook prints: the rewritten input, or None."""
    tool = pre_tool(event, cap_seconds, background)
    if tool is None:
        return None
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                              "updatedInput": tool}})


def subagent_call(event: dict) -> bool:
    """Is this PostToolUse input a subagent's tool call? Claude Code (2.1.305, probed)
    adds `agent_id` and `agent_type` to a subagent's input; the lead's has neither, and
    both share the lead's `session_id` and `transcript_path`. No `agent_id`: the lead's."""
    return isinstance(event.get("agent_id"), str) and bool(event["agent_id"].strip())


def touch_file(control: Path, item: str) -> Path:
    return Path(control) / ".conductor" / "touch" / f"{_key(item)}.jsonl"


def _norm(text: str) -> str:
    return text.replace("\\\\", "/").replace("\\", "/").lower()


_NAME_END = r"""\s"'`<>|;&()*?,"""     # what ends a path named in a shell command


def touch_paths(control: Path, event: dict) -> list[str]:
    """The control-folder paths (relative, lower-case; "" for the folder itself) a writing
    tool call names: a file tool's target, every one a shell command names (reads too:
    a command is not parsed). Its own report, audit and amend files (`reports/`) and the
    daemon's runtime (`.conductor/`) are never tracked, so they don't count."""
    tool, ti = event.get("tool_name"), event.get("tool_input")
    if tool not in TOUCH_TOOLS or not isinstance(ti, dict):
        return []
    text = _norm(str(ti.get("command") or "") if tool in SHELL_TOOLS
                 else str(ti.get("file_path") or ti.get("notebook_path") or ""))
    root = _norm(Path(control).resolve().as_posix()).rstrip("/")
    forms = [root] + ([f"/{root[0]}{root[2:]}"] if root[1:2] == ":" else [])   # C:/x and Git Bash's /c/x
    out = []
    for f in forms:
        for m in re.finditer(re.escape(f) + rf"(?=$|[/{_NAME_END}])(/[^{_NAME_END}]*)?", text):
            rel = (m.group(1) or "").strip("/").removeprefix("./")
            if not rel.startswith(("reports/", ".conductor/")) and rel not in out:
                out.append(rel)
    return out


def note_touch(control: Path, item: str, event: dict) -> list[str]:
    """Record the control-folder paths a writing tool call (the lead's or a helper's) names
    (`touch_paths`), so the guard can pin a change of one on the item that made it
    (`touched_since`). A change by a path that never names the folder goes unrecorded."""
    if not (named := touch_paths(control, event)):
        return []
    path = touch_file(control, item)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"at": stamp(), "tool": event["tool_name"], "paths": named}) + "\n")
    return named


def touched_since(control: Path, item: str, since: str) -> list[str]:
    """The control-folder paths `item`'s recorded tool calls named at or after `since` (a
    second's slack: stamps are whole seconds); `guard.named` matches them to a change."""
    try:
        lines = touch_file(control, item).read_text(encoding="utf-8").splitlines()
        cut = parse_stamp(since).timestamp() - 1
    except (OSError, ValueError):
        return []
    out = []
    for ln in lines:
        try:
            rec = json.loads(ln)
            if parse_stamp(rec["at"]).timestamp() >= cut:
                out += [p for p in rec.get("paths", []) if p not in out]
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return out


def hook(control: Path, item: str, event: dict | None = None) -> str | None:
    """What the hook prints: the PostToolUse output carrying waiting messages, or None.
    A subagent's call takes nothing: a helper reads the context as untrusted injected
    text and the lead never sees it (Word Warriors 059), so mail waits for the lead.
    Any call that wrote naming the control folder is noted first (`note_touch`)."""
    note_touch(control, item, event or {})
    if subagent_call(event or {}):
        return None
    messages = take(control, item, "hook")
    if not messages:
        return None
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                              "additionalContext": context_text(messages)}})


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="conductor.talk")
    sub = p.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hook")
    h.add_argument("--control", required=True)
    h.add_argument("--item", required=True)
    pre = sub.add_parser("pre")
    pre.add_argument("--cap", type=float, default=0)
    pre.add_argument("--background", action="append", default=[])
    args = p.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")     # the hook input
    except (OSError, ValueError, AttributeError):
        raw = ""
    try:
        event = json.loads(raw)
    except ValueError:
        event = None                    # unreadable input: a pre hook leaves the call be,
    event = event if isinstance(event, dict) else {}     # the message hook treats it as the lead's
    try:
        if args.cmd == "pre":
            out = pre_hook(event, args.cap, args.background) if event else None
        else:
            out = hook(Path(args.control), args.item, event)
    except Exception as exc:            # a hook never breaks the executor's tool call
        print(f"conductor hook: {exc!r}", file=sys.stderr)
        return 0
    if out:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
