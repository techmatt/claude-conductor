"""`session.md`: the start digest a `session:` signal writes, so a planner session opens
with one signal and one read. It is the inbox since the previous read mark, plus
conductor's own reports past HANDOFF's marker and each repo's maintenance facts."""

from __future__ import annotations

import re
from pathlib import Path

from . import inbox
from .config import Config
from .events import Event, EventLog
from .state import View
from .util import clock, read_text

# Conductor's own checkout: `src/conductor/session.py` → the repo root. Its HANDOFF.md
# holds the planner's marker for conductor's hand-pasted prompts' reports.
CONDUCTOR_ROOT = Path(__file__).resolve().parents[2]
MARKER = "Last report reviewed by the planner:"

# What `state.md`'s *Maintenance* section tracks, in the planner's words.
MAINTENANCE = (("bug_hunt", "bug hunt"), ("general_audit", "general audit"), ("cleanup", "cleanup"),
               ("test_perf", "test perf audit"), ("claude_md_cleanup", "claude.md cleanup"),
               ("disk_audit", "disk space audit"))
SLOW_WINDOW = 5     # a `slow:` component named in two or more of a repo's last five reports


def _report_key(name: str) -> tuple[int, str] | None:
    """Natural order of a prompt name: 12 < 12a < 13."""
    m = re.match(r"(\d+)([a-z]*)(?:_|$)", name)
    return (int(m.group(1)), m.group(2)) if m else None


def reports_past_marker(root: Path) -> tuple[str | None, list[tuple[str, Path]]] | None:
    """HANDOFF's marker and every `reports/NN_name_report.md` of conductor's own repo whose
    prompt is past it, in natural order, as (name, path); None without a HANDOFF.md. The
    marker line may start with `- ` or `**`; without one the marker is None and nothing is
    past it, since listing every report ever written would bury the planner."""
    handoff = root / "HANDOFF.md"
    if not handoff.is_file():
        return None
    marker = next((ln.lstrip("-* ").split(":", 1)[1].strip("* ") for ln in read_text(handoff).splitlines()
                   if ln.lstrip("-* ").startswith(MARKER)), None)
    if marker is None:
        return None, []
    floor = _report_key(marker) or (-1, "")
    found = []
    for p in (root / "reports").glob("*_report.md"):
        name = p.name.removesuffix("_report.md")
        if (key := _report_key(name)) and key > floor:
            found.append((key, name, p))
    return marker, [(name, p) for _, name, p in sorted(found)]


def reports_head(root: Path, marker: str | None, found: list) -> str:
    """`Past `<MARKER> 12_x` in `<root>/HANDOFF.md`:` (or `: none.`)."""
    if marker is None:
        return f"No `{MARKER}` line found in `{root / 'HANDOFF.md'}`; nothing listed."
    return f"Past `{MARKER} {marker or '(none)'}` in `{root / 'HANDOFF.md'}`" + (":" if found else ": none.")


def missing_handoff(root: Path) -> str:
    return f"- conductor's HANDOFF.md not found under `{root}`; nothing to list."


def report_status(text: str) -> str:
    """A report's `status:` line, from its first five non-blank lines."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return next((ln for ln in lines[:5] if ln.lower().startswith("status:")), "no status line")


def conductor_reports(root: Path) -> list[str]:
    """Conductor's own reports past HANDOFF's marker, one line each with its status line."""
    if (past := reports_past_marker(root)) is None:
        return [missing_handoff(root)]
    marker, found = past
    out = []
    for name, p in found:
        try:
            status = report_status(read_text(p))
        except (OSError, UnicodeDecodeError):
            status = "no status line"
        out.append(f"- {name}: {status} — `{p}`")
    return [reports_head(root, marker, found), *out]


def slow_components(text: str | None) -> list[str]:
    """`tests ~25m, why, fix; build ~20m, …` → `["tests", "build"]`."""
    out = []
    for part in (text or "").split(";"):
        name = re.split(r"\s~|,", part.strip(), maxsplit=1)[0].strip().lower()
        if name:
            out.append(name)
    return out


def maintenance(cfg: Config, log: EventLog, view: View, repo: str) -> list[str]:
    """One repo's maintenance facts, with no judgement: when each template last ran and
    how many other items finished since, `CLAUDE.md`'s size, unused audit notes, and
    `slow:` causes repeated in recent reports."""
    fins = sorted((ev for ev in view.finishes.values() if ev.repo == repo), key=lambda e: e.id)
    plain = [ev for ev in fins if not ev.detail.get("template")]
    live = {it.template: it.name for it in view.queued.get(repo, []) if it.template}
    live |= {rec.get("template"): rec["name"] for rec in view.running.values()
             if rec.get("repo") == repo and rec.get("template")}
    out = [f"### `{repo}`"]
    for tmpl, words in MAINTENANCE:
        last = next((ev for ev in reversed(fins) if ev.detail.get("template") == tmpl), None)
        since = sum(1 for ev in plain if last is None or ev.id > last.id)
        text = (f"{last.item.split('/', 1)[1]}, {clock(last.ts)} ({last.detail.get('status')})"
                if last else "never")
        text += f" · {since} non-template item(s) finished since" if last else f" · {since} non-template item(s) finished"
        if tmpl in live:
            text += f" · in flight: {live[tmpl]}"
        out.append(f"- {words}: {text}")
    claude_md = cfg.repos[repo] / "CLAUDE.md"
    try:
        data = claude_md.read_bytes()
        out.append(f"- `CLAUDE.md`: {len(data) / 1024:.1f} KB, {data.count(b'\n')} lines")
    except OSError:
        out.append("- `CLAUDE.md`: none")
    notes = view.unused_notes(cfg, repo)
    out.append(f"- unused audit notes: {len(notes)}" + (f" ({', '.join(n.item.split('/', 1)[1] for n in notes)})"
                                                         if notes else ""))
    recent = fins[-SLOW_WINDOW:]
    named: dict[str, list[str]] = {}
    for ev in recent:
        for comp in dict.fromkeys(slow_components(inbox.slow_text(ev.detail))):
            named.setdefault(comp, []).append(ev.item.split("/", 1)[1].split("_", 1)[0])
    repeats = [f"{c} ({', '.join(ids)})" for c, ids in named.items() if len(ids) >= 2]
    out.append(f"- `slow:` repeated in the last {len(recent)} report(s): {'; '.join(repeats) or 'none'}")
    return out


def render(cfg: Config, log: EventLog, view: View, cp: Event, rel: str, top: int,
           root: Path | None = None) -> str:
    """The digest: the inbox since the previous read mark, headed by the session's
    checkpoint, then conductor's reports and the maintenance facts."""
    head = (f"# Session start — {cfg.project}\n\n"
            f"Written for `{rel}` at {clock(cp.ts)}: {inbox.checkpoint_name(cp)}. "
            f"The read mark moves to #{top} as this is written; no `read:` is needed.")
    body = inbox.render(cfg, log, view, heading=head)
    out = [body.rstrip(), "", "## Conductor reports", *conductor_reports(root or CONDUCTOR_ROOT), "", "## Maintenance facts"]
    for repo in cfg.repos:
        out += maintenance(cfg, log, view, repo)
    return "\n".join(out) + "\n"
