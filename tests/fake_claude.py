"""Stand-in for `claude -p` in tests. Reads the prompt on stdin and obeys `FAKE` lines:

    FAKE sleep <seconds>
    FAKE report <done|partial|red|none>      (default done)
    FAKE write <path> <text>                 write a file (absolute, or relative to cwd)
    FAKE commit <file> <text>                write <file> in cwd and commit it
    FAKE error                               emit an is_error result
"""

import json
import subprocess
import sys
import time
from pathlib import Path

args = sys.argv[1:]
session = args[args.index("--session-id") + 1] if "--session-id" in args else "none"
prompt = sys.stdin.read()
lines = prompt.splitlines()
report = Path(lines[lines.index(next(ln for ln in lines if "write your report to exactly this path" in ln)) + 1].strip())


def emit(obj):
    print(json.dumps(obj), flush=True)


emit({"type": "system", "subtype": "init", "session_id": session})
status, is_error = "done", False
for ln in lines:
    if not ln.startswith("FAKE "):
        continue
    parts = ln.split(" ", 3)
    match parts[1]:
        case "sleep":
            time.sleep(float(parts[2]))
        case "report":
            status = parts[2]
        case "write":
            Path(parts[2]).write_text(parts[3] + "\n", encoding="utf-8")
        case "commit":
            Path(parts[2]).write_text(parts[3] + "\n", encoding="utf-8")
            subprocess.run(["git", "add", parts[2]], check=True)
            subprocess.run(["git", "commit", "-q", "-m", f"fake: {parts[2]}"], check=True)
        case "error":
            is_error = True
if status != "none":
    report.write_text(f"status: {status}\nblocks: none\ncarry: none\n\nFake report.\n", encoding="utf-8")
emit({"type": "result", "subtype": "error_during_execution" if is_error else "success",
      "is_error": is_error, "session_id": session, "result": "ok", "num_turns": 1, "total_cost_usd": 0})
