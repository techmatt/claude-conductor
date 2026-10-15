"""Executors: the preamble, the `claude -p` command line, and reading what a run left."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import Config
from .items import Item
from .util import clock, read_text

PREAMBLE = """\
You are a conductor executor: an unattended headless Claude Code session running queue
item {item_id} for project {project}. Nobody is watching and nobody will answer a
question. Never stop to ask: every gate is a branch — do what fits the goal, record
what didn't, and continue. The only hard stop is a wrong-repo guard in the prompt.

Your working directory is the code repo `{repo}` ({repo_path}).{readonly}

Time limit: {max_hours:g}h. Conductor hard-kills this session at {deadline}.

When you finish, whatever the outcome, write your report to exactly this path:
    {report}
That file is the only thing you may write outside the working directory. Do not read,
write or commit anything else in that folder: conductor checks it after you exit,
reverts any change, and marks this item red.

The report must start with exactly these three lines:
status: done | partial | red
blocks: <what this blocks, or none>
carry: <what state.md should change, or none>
`done` = goal met; `partial` = some of it landed, the rest is said in the report;
`red` = goal not met. The rest of the report follows the repo's CLAUDE.md.

The prompt follows.

---

"""

READONLY = ("\nThis item is readonly: commit nothing and change no tracked file. "
            "Conductor checks the repo afterwards and marks a change red.")

HEADER_KEYS = ("status", "blocks", "carry")
STATUSES = ("done", "partial", "red")


@dataclass
class RunPaths:
    report: Path
    log: Path          # stream-json stdout, the raw output
    stderr: Path
    prompt: Path       # exactly what was sent on stdin

    @classmethod
    def for_item(cls, cfg: Config, item: Item) -> "RunPaths":
        report = cfg.report_path(item.repo, item.name)
        base = report.with_suffix("")
        return cls(report, base.with_name(base.name + ".log"),
                   base.with_name(base.name + ".stderr.log"), base.with_name(base.name + ".prompt.md"))


def build_prompt(cfg: Config, item: Item, paths: RunPaths, max_hours: float, deadline: datetime) -> str:
    return PREAMBLE.format(
        item_id=item.id, project=cfg.project, repo=item.repo, repo_path=cfg.repos[item.repo],
        readonly=READONLY if item.readonly else "", max_hours=max_hours,
        deadline=clock(deadline), report=paths.report.resolve(),
    ) + item.body


def command(cfg: Config, session_id: str) -> list[str]:
    from .procs import resolve
    return [resolve(cfg.command[0]), *cfg.command[1:], "-p", "--dangerously-skip-permissions",
            "--output-format", "stream-json", "--verbose", "--session-id", session_id, *cfg.args]


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
