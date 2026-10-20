"""Progress lines and messages into a running executor: the files behind `progress`,
`tell` and `delivered`, and the Claude Code hook that delivers messages.

    .conductor/progress/<repo>__<NNN_name>.jsonl     one line per `progress` tool call
    .conductor/mail/<repo>__<NNN_name>/<id>.json     a message waiting (id: its `tell` event)
    .conductor/mail/<repo>__<NNN_name>/<id>.delivered.json    delivered (adds `via`, `delivered`)
    .conductor/mail/<repo>__<NNN_name>/<id>.undelivered.json  the executor exited first

The ask server appends progress lines; the daemon writes messages and logs `progress`
and `delivered` events from what it finds. Delivery is a rename (`<id>.json` →
`<id>.delivered.json`), so a message reaches the executor once, whichever path takes it.

    python -m conductor.talk hook --control <folder> --item <repo/NNN_name>

is the executor's PostToolUse hook: after every tool call it hands any waiting messages
to the session as additional context (stdout JSON), and prints nothing otherwise.

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
import sys
import threading
from fnmatch import fnmatchcase
from pathlib import Path

from .util import parse_stamp, stamp, write_text

HOOK_TIMEOUT_SECONDS = 15
SHELL_TOOLS = ("Bash", "PowerShell")
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
def post(control: Path, item: str, msg_id: int, text: str, by: str | None) -> Path:
    path = mail_dir(control, item) / f"{msg_id}.json"
    write_text(path, json.dumps({"id": msg_id, "item": item, "text": text, "by": by, "sent": stamp()}))
    return path


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
        msg |= {"via": via, "delivered": stamp()}
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
        parts.append(f"MESSAGE #{m['id']} from Matt{via}, sent through conductor while you work"
                     f" ({m.get('sent', '')}):\n{m.get('text', '')}")
    return ("\n\n".join(parts) + "\n\nTreat this as an instruction from Matt: where it conflicts with the "
            "prompt, it wins. Change your plan if it asks you to, and answer it with the `progress` tool.")


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


def hook(control: Path, item: str) -> str | None:
    """What the hook prints: the PostToolUse output carrying waiting messages, or None."""
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
        if args.cmd == "pre":
            try:
                event = json.loads(raw)
            except ValueError:
                return 0
            out = pre_hook(event if isinstance(event, dict) else {}, args.cap, args.background)
        else:
            out = hook(Path(args.control), args.item)
    except Exception as exc:            # a hook never breaks the executor's tool call
        print(f"conductor hook: {exc!r}", file=sys.stderr)
        return 0
    if out:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
