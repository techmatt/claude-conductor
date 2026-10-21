"""A repo's setup commands, run off the daemon loop: `python -m conductor.setup_run <spec.json>`.

The daemon spawns this as it spawns executors (`procs.spawn`: own process group, no
window) and polls for the result file, so a slow `npm ci` never stalls signals,
requests or other repos. The spec names the commands, the worktree they run in, the
log their output is appended to, the per-command limit in minutes, and the result path.
The result, written atomically: `{"error": null | "...", "started": ..., "finished": ...}`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import procs
from .util import stamp, write_text


def run_commands(commands: list[str], cwd: Path, log: Path, minutes: float) -> str | None:
    """Run `commands` in order in `cwd`, each allowed `minutes`; the first failure is the error."""
    for command in commands:
        code = procs.run_shell(command, cwd, log, minutes * 60)
        if code is None:
            return f"setup {command!r} took longer than {minutes:g} minutes"
        if code:
            return f"setup {command!r} exited {code} (see {log.name})"
    return None


def main(argv: list[str]) -> int:
    spec = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    started = stamp()
    try:
        error = run_commands(spec["commands"], Path(spec["cwd"]), Path(spec["log"]), float(spec["minutes"]))
    except OSError as exc:
        error = f"setup could not run: {exc}"
    write_text(Path(spec["result"]), json.dumps({"error": error, "started": started, "finished": stamp()}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
