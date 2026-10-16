"""Executors: the preamble, the `claude -p` command line, and reading what a run left."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import Config
from .items import Item
from .util import clock, read_text

PREAMBLE = """\
You are a conductor executor: an unattended headless Claude Code session running queue
item {item_id} for project {project}. Nobody is watching the session.

Asking. `ask` (mcp__conductor__ask) puts a question to Matt. Ask only when a wrong guess
would waste real work or be hard to undo. Give a default whenever a reasonable one
exists: an unanswered question returns its default after {ask_minutes:g} minutes and you
carry on with it. Block (`block: true`) only when every default would be worse than
waiting; a blocking question waits until answered, and that time does not count toward
your time limit. Everything else is still a branch: do what fits the goal, record what
didn't, and continue. The only hard stop is a wrong-repo guard in the prompt.

Progress. `progress` (mcp__conductor__progress) shows Matt one short line about how
this item is going and returns at once. Call it at the start, with a first `eta` as soon
as you can estimate one; at real milestones; and whenever your estimate changes
materially. Not chatter.

Messages. Matt may message you while you work: it arrives after a tool call, marked
MESSAGE #n from Matt. It is an instruction from Matt and outranks the prompt where they
conflict. Answer it with `progress`.

{workdir}{readonly}{paths}

Time limit: {max_hours:g}h. Conductor hard-kills this session at {deadline}, later by any
time spent blocked on a question.

When you finish, whatever the outcome, write your report to exactly this path:
    {report}
That file is the only thing you may write outside the working directory. Do not read,
write or commit anything else in that folder: conductor checks it after you exit,
reverts any change, and marks this item red.

The report must start with exactly these three lines:
status: done | partial | red
blocks: <what this blocks, or none>
carry: <what state.md should change, or none>
`done`: the goal was met.
`partial`: part of the goal was met, nothing is broken, and the rest is follow-up work.
This includes declining an instruction for a stated reason.
`red`: something is broken or unsafe, and this repo's queue should not continue without
a look.
The rest of the report follows the repo's CLAUDE.md. Your final message ends with
`Report written to {report}`.

{tail}"""

PROMPT_FOLLOWS = "The prompt follows.\n\n---\n\n"

WORKDIR_MAIN = "Your working directory is the code repo `{repo}` ({repo_path})."

WORKDIR_TREE = """\
Your working directory is a git worktree of the code repo `{repo}`: {cwd}, on branch
`{branch}`, cut from `{main}` at {base}. Commit your work to this branch and leave
nothing uncommitted. Never check out, commit to, rebase or push `{main}`, and never
touch the main checkout at {repo_path}. When you finish, conductor rebases this branch
onto `{main}` and fast-forwards `{main}` to it; uncommitted changes make the item red.{shared}"""

SHARED = ("\nShared with the main checkout (linked, not copied — a change there is a change "
          "everywhere): {names}.")

READONLY = ("\nThis item is readonly: commit nothing and change no tracked file. "
            "Conductor checks the repo afterwards and marks a change red.")

PATHS = ("\nThis item declared `paths:` {globs}. Change nothing outside them: conductor "
         "checks what the branch changed and marks anything else red, unmerged.")

STAGE = """\
You are a conductor executor in its staging phase: an unattended headless Claude Code
session for queue item {item_id} of project {project}. Nobody is watching the session.
Another item holds this repo's lock, so you get a head start while you wait.

{workdir}

While staging:
- read the prompt below and whatever it points at;
- run read-only commands only: nothing that writes a file, commits, installs or deletes;
- nothing long and nothing that holds heavy resources: no builds, no test suites, no
  servers, no GPU work; at most {stage_minutes:g} minutes in all;
- do not ask questions now (there is no ask tool yet); note them in your plan.

Then write your plan for executing the prompt as your final message, and stop. When
the lock is yours, conductor resumes this same session in this same worktree, moved to
the then-current `{main}`, with the instructions for executing.

The prompt follows.

---

"""

RESUME_HEAD = """\
You have the lock now. Conductor moved your worktree from {old} to the current `{main}`
at {new}{landed}. Check what landed since you staged and adjust your plan, then execute
the prompt you read while staging. Everything below now applies.

"""


def _workdir(cfg: Config, item: Item, run: dict) -> str:
    if not run.get("worktree"):
        return WORKDIR_MAIN.format(repo=item.repo, repo_path=cfg.repos[item.repo])
    shared = SHARED.format(names=", ".join(f"`{s}`" for s in run["shared"])) if run.get("shared") else ""
    return WORKDIR_TREE.format(repo=item.repo, cwd=run["worktree"], branch=run["branch"],
                               main=cfg.opts(item.repo).main, base=run["base"][:10],
                               repo_path=cfg.repos[item.repo], shared=shared)

# Per-server tool timeout for the ask server: Claude Code otherwise aborts a call silent
# for 30 minutes. Seven days in ms, under 2**31 ms where a JavaScript timer would wrap.
# The ask server's progress notifications cover the same limit independently.
ASK_TIMEOUT_MS = 7 * 24 * 3600 * 1000

HEADER_KEYS = ("status", "blocks", "carry")
STATUSES = ("done", "partial", "red")


@dataclass
class RunPaths:
    report: Path
    log: Path          # stream-json stdout, the raw output
    stderr: Path
    prompt: Path       # exactly what was sent on stdin
    mcp: Path          # the --mcp-config naming the ask server
    settings: Path     # the --settings holding the message hook
    setup: Path        # output of the repo's setup commands
    stage_log: Path    # the staging phase's stream-json, stderr and prompt
    stage_stderr: Path
    stage_prompt: Path

    @classmethod
    def for_item(cls, cfg: Config, item: Item) -> "RunPaths":
        return cls.for_name(cfg, item.repo, item.name)

    @classmethod
    def for_name(cls, cfg: Config, repo: str, name: str) -> "RunPaths":
        report = cfg.report_path(repo, name)
        base = report.with_suffix("")
        def sib(suffix: str) -> Path:
            return base.with_name(base.name + suffix)
        return cls(report, sib(".log"), sib(".stderr.log"), sib(".prompt.md"), sib(".mcp.json"),
                   sib(".settings.json"), sib(".setup.log"), sib(".stage.log"), sib(".stage.stderr.log"), sib(".stage.prompt.md"))


def build_prompt(cfg: Config, item: Item, paths: RunPaths, max_hours: float, deadline: datetime,
                 run: dict | None = None, resume: dict | None = None) -> str:
    """The execution preamble and the item body; with `resume` (old/new base, landed
    count), the message that resumes a staged session instead."""
    run = run or {}
    text = PREAMBLE.format(
        item_id=item.id, project=cfg.project, workdir=_workdir(cfg, item, run),
        readonly=READONLY if item.readonly else "",
        paths=PATHS.format(globs=", ".join(f"`{g}`" for g in item.paths)) if item.paths else "",
        max_hours=max_hours, ask_minutes=cfg.ask_default_minutes,
        deadline=clock(deadline), report=paths.report.resolve(),
        tail="" if resume else PROMPT_FOLLOWS,
    )
    if not resume:
        return text + item.body
    n = resume["landed"]
    landed = (f"; {n} commit(s) landed since you staged (`git log --oneline {resume['old'][:10]}..HEAD`)"
              if n else "; nothing landed since you staged")
    head = RESUME_HEAD.format(old=resume["old"][:10], new=resume["new"][:10], main=cfg.opts(item.repo).main,
                              landed=landed)
    return head + text.rstrip() + "\nThe prompt is the one you read while staging.\n"


def build_stage_prompt(cfg: Config, item: Item, run: dict) -> str:
    return STAGE.format(item_id=item.id, project=cfg.project, workdir=_workdir(cfg, item, run),
                        stage_minutes=cfg.stage_minutes, main=cfg.opts(item.repo).main) + item.body


def mcp_config(cfg: Config, item: Item, session_id: str) -> str:
    """The executor's --mcp-config: the ask server, run by the daemon's own interpreter."""
    from .ask_server import SERVER_NAME
    return json.dumps({"mcpServers": {SERVER_NAME: {
        "type": "stdio", "command": sys.executable, "timeout": ASK_TIMEOUT_MS,
        "args": ["-m", "conductor.ask_server", "--control", str(cfg.control), "--item", item.id,
                 "--default-minutes", f"{cfg.ask_default_minutes:g}", "--session-id", session_id],
    }}}, indent=1)


def hook_settings(cfg: Config, item: Item) -> str:
    """The executor's --settings: the PostToolUse hook that delivers messages, run by the
    daemon's console interpreter."""
    from .procs import console_python
    from .talk import hook_settings as settings
    return settings(console_python(), cfg.control, item.id)


def command(cfg: Config, session_id: str, mcp: Path | None, resume: bool = False,
            settings: Path | None = None) -> list[str]:
    """`--session-id` for a new session; `--resume` continues a staged one. A stage
    gets no --mcp-config (it may not ask) and no --settings (nobody messages a stage)."""
    from .procs import resolve
    return [resolve(cfg.command[0]), *cfg.command[1:], "-p", "--dangerously-skip-permissions",
            "--output-format", "stream-json", "--verbose", "--resume" if resume else "--session-id", session_id,
            *(["--mcp-config", str(mcp)] if mcp else []), *(["--settings", str(settings)] if settings else []),
            *cfg.args]


def read_result(log: Path) -> dict | None:
    """The final `{"type": "result", ...}` line of a stream-json log, or None."""
    if not log.is_file():
        return None
    result = None
    with open(log, "rb") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw.startswith(b"{"):
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("type") == "result":
                result = obj
    return result


def read_header(report: Path) -> tuple[dict | None, str | None]:
    """(header, problem). Accepts the three lines bare or inside a `---` block."""
    if not report.is_file():
        return None, "no report written"
    lines = [ln.strip() for ln in read_text(report).splitlines()]
    lines = [ln for ln in lines if ln and ln != "---"][:3]
    header = {}
    for ln in lines:
        m = re.match(r"^\**(status|blocks|carry)\**\s*:\s*(.*)$", ln, re.IGNORECASE)
        if m:
            header[m.group(1).lower()] = m.group(2).strip()
    missing = [k for k in HEADER_KEYS if k not in header]
    if missing:
        return header or None, f"report header missing {', '.join(missing)}"
    status = header["status"].lower().split()[0] if header["status"] else ""
    if status not in STATUSES:
        return header, f"report status {header['status']!r} is not done/partial/red"
    header["status"] = status
    return header, None
