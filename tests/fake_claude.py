"""Stand-in for `claude -p` in tests. Reads the prompt on stdin and obeys `FAKE` lines:

    FAKE sleep <seconds>
    FAKE wait-for <path> <seconds>           # touch <path>.waiting, then wait until <path> exists
                                             # (at most <seconds>); not a tool call
    FAKE report <done|partial|red|none>      (default done)
    FAKE write <path> <text>                 write a file (absolute, or relative to cwd); its hooks
                                             see a `Write` call with `file_path` and `content`
    FAKE audit <text>                        append a line to the audit notes file the preamble names
    FAKE commit <file> <text>                write <file> in cwd and commit it
    FAKE append|prepend <file> <text>        add a line at the end or start of <file> and commit it
    FAKE check <path>                        the report is red unless <path> exists
    FAKE slow <text>                         add `slow: <text>` to the report header, after carry:
    FAKE pick <path>                         add `pick: <path>` to the report header, after carry:
                                             (and slow:)
    FAKE env <NAME>                          write `NAME=<its value>` into the report
    FAKE error                               emit an is_error result
    FAKE ask <json arguments>                call the ask tool through --mcp-config;
                                             the result text goes into the report
    FAKE progress <json arguments>           call the progress tool; its text goes into the report
    FAKE until-message <seconds>             "work" (a tool call every 0.2s) until a message
                                             arrives or the time is up; then answer it with progress
    FAKE reply-with <seconds> <json>         the same, answering with these progress arguments
                                             (an outline request's reply)
    FAKE leave <seconds>                     start a process in cwd that outlives the executor
                                             (`LEFT=<pid>` in the report)
    FAKE hog <MB> <seconds>                  a child process holding <MB> of touched memory
                                             (`HOG=<pid>` in the report)
    FAKE detach-hog <MB> <seconds>           the same, detached: an orphan in cwd
    FAKE subagent <tool>                     a helper subagent's tool call: its hooks get the
                                             `agent_id` / `agent_type` Claude Code adds
    FAKE subagent progress <json arguments>  a helper's progress call: the tool, then its hooks

After every FAKE line but `sleep` (each counts as a tool call), the PostToolUse hooks named in
--settings run as Claude Code runs them: input JSON on stdin, `additionalContext` from
their stdout JSON. Each context received is written to the report as `CONTEXT: ...`.

A merge executor's prompt resolves the conflicted files it lists by keeping both sides'
lines; `FAKE merge-fail` in the item leaves the markers, `FAKE merge-stray` also adds a file.

A staging prompt obeys only `FAKE stage-sleep <seconds>` and `FAKE stage-write <path>
<text>`, remembers the prompt under its session id, and ends with a plan. `--resume`
recalls that prompt, runs its FAKE lines, and says so in the report.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

args = sys.argv[1:]
resume = "--resume" in args
session = args[args.index("--resume" if resume else "--session-id") + 1] if (
    resume or "--session-id" in args) else "none"
prompt = sys.stdin.read()
lines = prompt.splitlines()
store = Path(tempfile.gettempdir()) / "conductor-fake-sessions" / f"{session}.txt"


def emit(obj):
    print(json.dumps(obj), flush=True)


def result(text="ok", is_error=False):
    emit({"type": "result", "subtype": "error_during_execution" if is_error else "success",
          "is_error": is_error, "session_id": session, "result": text, "num_turns": 1, "total_cost_usd": 0})


emit({"type": "system", "subtype": "init", "session_id": session})

if "conductor merge executor" in prompt:
    # Resolve every listed file by keeping both sides, unless the item says `FAKE merge-fail`
    # (markers left) or `FAKE merge-stray` (also edits a file not in conflict).
    if "FAKE merge-fail" not in prompt:
        files, at = [], lines.index(next(ln for ln in lines if "hold conflict markers" in ln)) + 1
        while lines[at].startswith("- "):
            files.append(lines[at][2:])
            at += 1
        for f in files:
            kept = [ln for ln in Path(f).read_text(encoding="utf-8").splitlines()
                    if not ln.startswith(("<<<<<<<", "=======", ">>>>>>>", "|||||||"))]
            Path(f).write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")
        if "FAKE merge-stray" in prompt:
            Path("stray.txt").write_text("stray\n", encoding="utf-8")
    result("Resolved by keeping both sides.")
    sys.exit(0)

if "in its staging phase" in prompt:
    for ln in lines:
        parts = ln.split(" ", 3)
        if ln.startswith("FAKE stage-sleep "):
            time.sleep(float(parts[2]))
        elif ln.startswith("FAKE stage-write "):
            Path(parts[2]).write_text(parts[3] + "\n", encoding="utf-8")
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(prompt, encoding="utf-8")
    result(f"Plan: run the {sum(ln.startswith('FAKE ') for ln in lines)} FAKE lines.")
    sys.exit(0)

report = Path(lines[lines.index(next(ln for ln in lines if "write your report to exactly this path" in ln)) + 1].strip())
memory = ""
if resume:
    staged = store.read_text(encoding="utf-8") if store.is_file() else ""
    memory = "STAGED MEMORY: " + ("recalled the staged prompt" if staged else "none") + "\n"
    lines = lines + staged.splitlines()

settings = json.loads(Path(args[args.index("--settings") + 1]).read_text(encoding="utf-8")) \
    if "--settings" in args else {}
contexts = []


def post_tool_use(tool: str, agent: bool = False, tool_input: dict | None = None) -> str | None:
    got = []
    event = {"hook_event_name": "PostToolUse", "tool_name": tool, "session_id": session}
    if agent:                           # what Claude Code 2.1.305 adds for a subagent's call
        event |= {"agent_id": "a0fake0subagent", "agent_type": "general-purpose"}
    if tool_input:
        event["tool_input"] = tool_input
    for group in settings.get("hooks", {}).get("PostToolUse", []):
        for h in group.get("hooks", []):
            # The hook is a whole Python process: run it only when a message waits for it
            # or the call wrote a file (it may note a control-folder touch); what it would
            # find otherwise is nothing. Tests that send messages still run the real hook.
            a = h.get("args", [])
            if "--control" in a and "--item" in a and not tool_input:
                from conductor import talk
                if not talk.pending(Path(a[a.index("--control") + 1]), a[a.index("--item") + 1]):
                    continue
            out = subprocess.run([h["command"], *h.get("args", [])], capture_output=True, text=True,
                                 input=json.dumps(event),
                                 encoding="utf-8", timeout=h.get("timeout", 60)).stdout
            if out.strip():
                got.append(json.loads(out)["hookSpecificOutput"]["additionalContext"])
    contexts.extend(got)
    return "\n".join(got) or None


def progress(arguments: dict) -> str:
    from mcp_client import call_tool
    text, _ = call_tool(Path(args[args.index("--mcp-config") + 1]), "progress", arguments)
    answers.append(text)
    return text


status, is_error, pick, slow = "done", False, None, None
answers = []
hogs = []
for ln in lines:
    if not ln.startswith("FAKE ") or ln.startswith("FAKE stage-"):
        continue
    parts = ln.split(" ", 3)
    match parts[1]:
        case "ask":
            from mcp_client import call_ask
            text, _ = call_ask(Path(args[args.index("--mcp-config") + 1]), json.loads(ln.split(" ", 2)[2]))
            answers.append(text)
        case "sleep":
            time.sleep(float(parts[2]))
        case "wait-for":
            # `<path>.waiting` says every earlier tool call (and its hooks) is over.
            Path(parts[2] + ".waiting").touch()
            end = time.time() + float(parts[3])
            while not Path(parts[2]).exists() and time.time() < end:
                time.sleep(0.05)
        case "progress":
            progress(json.loads(ln.split(" ", 2)[2]))
        case "until-message" | "reply-with":
            end = time.time() + float(parts[2])
            # A message may already have come with the previous line's hook.
            while contexts or time.time() < end:
                if contexts or post_tool_use("Bash"):
                    progress(json.loads(parts[3]) if parts[1] == "reply-with"
                             else {"note": f"got it: {contexts[-1].splitlines()[1]}"})
                    break
                time.sleep(0.2)
        case "report":
            status = parts[2]
        case "write":
            Path(parts[2]).write_text(parts[3] + "\n", encoding="utf-8")
        case "audit":
            notes = Path(lines[lines.index(next(ln for ln in lines if "running notes file at exactly" in ln)) + 1].strip())
            with open(notes, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(ln.split(" ", 2)[2] + "\n")
        case "commit":
            Path(parts[2]).parent.mkdir(parents=True, exist_ok=True)
            Path(parts[2]).write_text(parts[3] + "\n", encoding="utf-8")
            subprocess.run(["git", "add", parts[2]], check=True)
            subprocess.run(["git", "commit", "-q", "-m", f"fake: {parts[2]}"], check=True)
        case "append" | "prepend":
            old = Path(parts[2]).read_text(encoding="utf-8") if Path(parts[2]).is_file() else ""
            new = old + parts[3] + "\n" if parts[1] == "append" else parts[3] + "\n" + old
            Path(parts[2]).write_text(new, encoding="utf-8", newline="\n")
            subprocess.run(["git", "add", parts[2]], check=True)
            subprocess.run(["git", "commit", "-q", "-m", f"fake: {parts[1]} {parts[2]}"], check=True)
        case "check":
            if not Path(parts[2]).exists():
                status = "red"
                memory += f"MISSING: {parts[2]}\n"
        case "error":
            is_error = True
        case "leave":
            # A leftover: a process in cwd that outlives the executor, as a dev server does.
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            left = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({float(parts[2])})"],
                                    creationflags=flags, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            memory += f"LEFT={left.pid}\n"
        case "hog":
            # A child holding <MB> of touched memory for <seconds>, in the executor's tree.
            mb, secs = int(parts[2]), float(parts[3])
            hogs.append(subprocess.Popen([sys.executable, "-c",
                                          f"import time; b = bytearray({mb} << 20); b[::4096] = b'x' * len(b[::4096]); "
                                          f"open({str(Path.cwd() / '.hog')!r}, 'w').close(); time.sleep({secs})"]))
            while not (Path.cwd() / ".hog").exists():
                time.sleep(0.05)
            (Path.cwd() / ".hog").unlink()
            memory += f"HOG={hogs[-1].pid}\n"
        case "detach-hog":
            # The same, with no live parent: an orphan in cwd, as `( cmd & )` in a shell
            # makes one. Through conductor's own launcher: a venv's python.exe holds its
            # child in a kill-on-close job, so a hand-rolled launcher takes the hog with it.
            from conductor import procs
            mb, secs = int(parts[2]), float(parts[3])
            hog = (f"import time; b = bytearray({mb} << 20); b[::4096] = b'x' * len(b[::4096]); "
                   f"open({str(Path.cwd() / '.hog')!r}, 'w').close(); time.sleep({secs})")
            left = procs.spawn_detached([sys.executable, "-c", hog], Path.cwd(),
                                        Path(tempfile.gettempdir()) / "conductor-fake-hog.log")
            while not (Path.cwd() / ".hog").exists():
                time.sleep(0.05)
            (Path.cwd() / ".hog").unlink()
            memory += f"HOG={left.pid}\n"
        case "pick":
            pick = parts[2]
        case "slow":
            slow = ln.split(" ", 2)[2]
        case "subagent":
            if parts[2] == "progress":          # a helper's progress call: the tool, then its hook
                progress(json.loads(parts[3]))
            post_tool_use("mcp__conductor__progress" if parts[2] == "progress" else parts[2], agent=True)
        case "env":
            memory += f"{parts[2]}={os.environ.get(parts[2])}\n"
    if parts[1] == "write":                                 # Claude Code's Write tool, with its input
        post_tool_use("Write", tool_input={"file_path": str(Path(parts[2]).resolve()), "content": parts[3]})
    elif parts[1] not in ("sleep", "wait-for", "subagent"):   # waiting is thinking, not a tool call
        post_tool_use(parts[1])
if status != "none":
    report.write_text(f"status: {status}\nblocks: none\ncarry: none\n"
                      + (f"slow: {slow}\n" if slow else "") + (f"pick: {pick}\n" if pick else "") + "\nFake report.\n" + memory
                      + "".join(f"\nASK RESULT: {a}\n" for a in answers)
                      + "".join(f"\nCONTEXT: {c}\n" for c in contexts), encoding="utf-8")
result(is_error=is_error)
