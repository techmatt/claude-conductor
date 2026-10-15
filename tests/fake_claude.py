"""Stand-in for `claude -p` in tests. Reads the prompt on stdin and obeys `FAKE` lines:

    FAKE sleep <seconds>
    FAKE report <done|partial|red|none>      (default done)
    FAKE write <path> <text>                 write a file (absolute, or relative to cwd)
    FAKE commit <file> <text>                write <file> in cwd and commit it
    FAKE check <path>                        the report is red unless <path> exists
    FAKE error                               emit an is_error result
    FAKE ask <json arguments>                call the ask tool through --mcp-config;
                                             the result text goes into the report

A staging prompt obeys only `FAKE stage-sleep <seconds>` and `FAKE stage-write <path>
<text>`, remembers the prompt under its session id, and ends with a plan. `--resume`
recalls that prompt, runs its FAKE lines, and says so in the report.
"""

import json
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

status, is_error = "done", False
answers = []
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
        case "report":
            status = parts[2]
        case "write":
            Path(parts[2]).write_text(parts[3] + "\n", encoding="utf-8")
        case "commit":
            Path(parts[2]).parent.mkdir(parents=True, exist_ok=True)
            Path(parts[2]).write_text(parts[3] + "\n", encoding="utf-8")
            subprocess.run(["git", "add", parts[2]], check=True)
            subprocess.run(["git", "commit", "-q", "-m", f"fake: {parts[2]}"], check=True)
        case "check":
            if not Path(parts[2]).exists():
                status = "red"
                memory += f"MISSING: {parts[2]}\n"
        case "error":
            is_error = True
if status != "none":
    report.write_text(f"status: {status}\nblocks: none\ncarry: none\n\nFake report.\n" + memory
                      + "".join(f"\nASK RESULT: {a}\n" for a in answers), encoding="utf-8")
result(is_error=is_error)
