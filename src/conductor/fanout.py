"""The agent cap at a Workflow's launch (120; aicomplete 234 ran 16 agents under a
preamble saying 10). The executor's PreToolUse hook (`talk.pre_hook`) hands every
`Workflow` call here before it runs, and a script that breaks the cap is denied with
the reason, so the executor rewrites it and calls again.

What a hook can see is the script, not the run: an agent count that depends on data
(findings fanned out to verifiers) is unknowable before launch. So a script that fans
out (`parallel`, `pipeline`, `.map`, a loop) must declare its cap as a literal,
`const MAX_AGENTS = <n>` with n ≤ `AGENT_CAP`, finders and verifiers counted, and slice
its fan-outs to fit; one with no fan-out counts its `agent(` calls. Whether the script
obeys its own declaration is not checked: that stays the executor's (docs/preambles.md).
A named workflow (`name`, no script) is not checked.
"""

from __future__ import annotations

import re
from pathlib import Path

AGENT_CAP = 10
AGENT_CALL_RE = re.compile(r"\bagent\s*\(")
FAN_OUT_RE = re.compile(r"\b(?:parallel|pipeline)\s*\(|\.(?:map|flatMap|forEach)\s*\(|\bfor\s*\(|\bwhile\s*\(")
DECLARED_RE = re.compile(r"\b(?:const|let|var)\s+MAX_AGENTS\s*=\s*(\d+)\b")
COMMENT_RE = re.compile(r"/\*.*?\*/|(?<![:\"'])//[^\n]*", re.DOTALL)


def script_of(tool_input: dict, cwd: str | None = None) -> str | None:
    """The Workflow call's script: inline, or read from `scriptPath` (relative to the
    session's cwd). None for a named workflow or a path that can't be read."""
    if isinstance(tool_input.get("script"), str) and tool_input["script"].strip():
        return tool_input["script"]
    if isinstance(path := tool_input.get("scriptPath"), str) and path.strip():
        p = Path(path)
        if not p.is_absolute() and cwd:
            p = Path(cwd) / p
        try:
            return p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
    return None


def problem(script: str, cap: int = AGENT_CAP) -> str | None:
    """Why this script breaks the cap, or None."""
    code = COMMENT_RE.sub("", script)
    calls = len(AGENT_CALL_RE.findall(code))
    if not calls:
        return None
    declared = [int(n) for n in DECLARED_RE.findall(code)]
    if declared:
        if max(declared) > cap:
            return f"it declares MAX_AGENTS = {max(declared)}, over the cap of {cap}"
        return None
    if FAN_OUT_RE.search(code):
        return (f"it fans out (parallel, pipeline, map or a loop) with no `const MAX_AGENTS = <n>` "
                f"(n ≤ {cap}) declared")
    if calls > cap:
        return f"it makes {calls} agent() calls, over the cap of {cap}"
    return None


def check(event: dict, cap: int = AGENT_CAP) -> str | None:
    """The deny reason for a `Workflow` PreToolUse event, or None to let it run."""
    ti = event.get("tool_input")
    if event.get("tool_name") != "Workflow" or not isinstance(ti, dict):
        return None
    if (script := script_of(ti, event.get("cwd"))) is None or not (why := problem(script, cap)):
        return None
    return (f"conductor: Workflow refused: {why}. Any fan-out runs at most {cap} agents in all, "
            f"finders and verifiers both (the preamble's load budget). Declare `const MAX_AGENTS = <n>` "
            f"(n ≤ {cap}), cap every fan-out to it (slice the findings you verify, batch reviewers), "
            "and call Workflow again.")
