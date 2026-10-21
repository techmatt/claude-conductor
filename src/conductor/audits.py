"""Audit notes and the standard prompt templates conductor owns.

An item with `audit: true` (or `template: general_cleanup` or `self_audit`) keeps notes beside its
report, `reports/<repo>__NNN_name.audit.md`. A `template: cleanup` item is handed every
note of its repo not yet used by an earlier cleanup; an `audit_used` event records the
notes a cleanup (or a general cleanup, its own) used, so the next one sees only newer ones.
The template texts live in `templates/` beside this module, versioned with the repo.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .events import Event
from .util import clock, read_text

TEMPLATE_DIR = Path(__file__).with_name("templates")
TEMPLATES = ("cleanup", "general_cleanup", "test_cleanup", "disk_audit", "claude_md_cleanup",
             "bug_hunt", "self_audit", "watch")  # what `template:` may name
# Old names, still read: "audit" is kept for passes that only read and take notes, so the
# two that edit code were renamed (prompt 36). Items and events written before keep theirs.
ALIASES = {"general_audit": "general_cleanup", "test_perf": "test_cleanup"}


def canonical(name: str | None) -> str | None:
    """A template's current name: an alias (`general_audit`) → `general_cleanup`."""
    return ALIASES.get(name, name) if isinstance(name, str) else name


def _named(*names: str) -> tuple[str, ...]:
    """`names` plus their aliases, so a run record or event written before a rename matches."""
    return names + tuple(old for old, new in ALIASES.items() if new in names)


NOTE_TEMPLATES = _named("general_cleanup", "self_audit")   # keep audit notes without `audit: true`
USES_NOTES = _named("cleanup", "general_cleanup")          # a finish not red logs `audit_used`
# What they are handed is fixed at launch, so they never stage.
NO_STAGE = ("cleanup", "disk_audit", "watch")
# Repo-wide templates that run alone in their repo: never beside another item, readonly ones
# included (a perf or disk measurement is skewed by anything else running), and nothing
# behind one in its queue starts early (`state.early_starts`), so they are never starved.
EXCLUSIVE = _named("cleanup", "general_cleanup", "test_cleanup", "disk_audit", "claude_md_cleanup")


def template_text(name: str) -> str:
    return read_text(TEMPLATE_DIR / f"{canonical(name)}.md")


def template_version(name: str) -> str:
    """The first 12 hex digits of the template's sha256: which text an item was sent."""
    return hashlib.sha256(template_text(name).encode("utf-8")).hexdigest()[:12]


def fill(text: str, **values: str) -> str:
    """`{{name}}` placeholders, so the texts need no brace escaping."""
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def note_path(cfg: Config, repo: str, name: str) -> Path:
    report = cfg.report_path(repo, name)
    return report.with_name(report.stem + ".audit.md")


def writes_notes(meta: dict) -> bool:
    """An item (its front-matter or run record) that keeps audit notes."""
    return bool(meta.get("audit")) or meta.get("template") in NOTE_TEMPLATES


@dataclass
class Note:
    item: str           # the item that wrote it
    path: Path
    finish: Event       # its finish event
    used_by: Event | None = None      # the `audit_used` event that consumed it


def pending(count: int, items: list[str] | None = None) -> str:
    """The *audit pending* marker the app, the inbox and `conductor status` share:
    `audit pending: 3 audit notes`, with the items' short names when given."""
    text = f"audit pending: {count} audit note{'' if count == 1 else 's'}"
    return text + (f" ({', '.join(i.split('/', 1)[-1] for i in items)})" if items else "")


def notes(cfg: Config, repo: str, finishes: dict[str, Event], used: dict[str, Event],
          claimed: set[str] = frozenset(), include_used: bool = False) -> list[Note]:
    """A repo's audit notes, oldest first: finished items that kept notes and left a
    non-empty file. Unless `include_used`, only those no cleanup has used and no live
    cleanup (running or staged) has claimed."""
    out = []
    for item_id, ev in finishes.items():
        if ev.repo != repo or not writes_notes(ev.detail):
            continue
        if not include_used and (item_id in used or item_id in claimed):
            continue
        path = note_path(cfg, repo, item_id.split("/", 1)[1])
        try:
            if not path.is_file() or not read_text(path).strip():
                continue
        except (OSError, UnicodeDecodeError):
            continue
        out.append(Note(item_id, path, ev, used.get(item_id)))
    return sorted(out, key=lambda n: n.finish.id)


def render_notes(cfg: Config, ids: list[str], finishes: dict[str, Event]) -> str:
    """The notes a cleanup was handed, inlined for its prompt: executors never see the
    control folder."""
    parts = []
    for item_id in ids:
        repo, _, name = item_id.partition("/")
        ev = finishes.get(item_id)
        when = f", finished {ev.detail.get('status')} {clock(ev.ts)}" if ev else ""
        if ev and ev.detail.get("template") == "self_audit" and ev.detail.get("resume_of"):
            when += f": a self-audit of {ev.detail['resume_of']}, written by its own session"
        elif ev and ev.detail.get("resume_of"):
            when += f": notes from a resume of {ev.detail['resume_of']}"
        try:
            text = read_text(note_path(cfg, repo, name)).strip()
        except (OSError, UnicodeDecodeError) as exc:
            text = f"(could not read the notes: {exc})"
        parts.append(f"### Notes from {item_id}{when}\n\n{text}\n")
    return "\n".join(parts)


# ---------------------------------------------------------------- disk audit
def tree_size(path: Path) -> int:
    """Bytes under `path`, never following a symlink or junction (a Drive-homed folder
    or a worktree's shared link would count its target twice, or walk off the folder)."""
    total, stack = 0, [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for entry in it:
                    try:
                        if entry.is_symlink() or entry.is_junction():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def control_usage(cfg: Config, repo: str) -> str:
    """What the control folder occupies, by location, as a markdown table: a disk
    audit's executor never sees the control folder, so conductor measures it."""
    control = cfg.control
    rows: list[tuple[str, int]] = []
    logs = [p for p in (control / "reports").glob("*.log") if p.is_file()]
    log_bytes = sum(p.stat().st_size for p in logs)
    mine = sum(p.stat().st_size for p in logs if p.name.startswith(f"{repo}__"))
    rows.append((f"`reports/*.log` (run logs; {human(mine)} of it `{repo}`'s)", log_bytes))
    rows.append(("`reports/`, everything else (reports, prompts, audit notes)",
                 tree_size(control / "reports") - log_bytes))
    rows.append(("`events.db` (with `-wal`, `-shm`)",
                 sum(p.stat().st_size for p in control.glob("events.db*") if p.is_file())))
    named = {"reports", ".git", ".conductor", "done"}
    for rel in (".git", ".conductor", "done"):
        rows.append((f"`{rel}/`", tree_size(control / rel)))
    rest = sum(tree_size(p) if p.is_dir() else p.stat().st_size for p in control.iterdir()
               if p.name not in named and not p.name.startswith("events.db") and not p.is_symlink())
    rows.append(("everything else (queues, docs, picks, signals)", rest))
    total = sum(n for _, n in rows)
    out = ["| Control folder | Size |", "|---|---|"]
    out += [f"| {what} | {human(n)} |" for what, n in rows]
    out.append(f"| **total** `{control}` | **{human(total)}** |")
    big = sorted(logs, key=lambda p: -p.stat().st_size)[:5]
    if big:
        out += ["", "Largest run logs: " + ", ".join(f"`{p.name}` {human(p.stat().st_size)}" for p in big) + "."]
    return "\n".join(out)


def worktree_listing(cfg: Config, repo: str, live: dict[str, str], leftovers: list[dict],
                     kept: set[str]) -> str:
    """Each folder under this repo's worktree root, with what conductor knows of it:
    `live` maps a worktree path to its running or staged item, `leftovers` are the
    removals conductor still retries, `kept` red items' worktrees kept for inspection."""
    root = cfg.worktree(repo, "x").parent
    lines = [f"Worktrees of `{repo}`: `{root}`."]
    if not root.is_dir():
        return lines[0] + " The folder does not exist."
    stale = {str(Path(r["worktree"])) for r in leftovers if r.get("repo") == repo}
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        key = str(d)
        if key in live:
            what = f"live ({live[key]}): never touch it"
        elif key in stale:
            what = "a removal conductor could not finish (a file was held); it retries it"
        elif d.name in kept:
            what = "a red item's worktree, kept for inspection"
        else:
            what = "no conductor record (stray)"
        lines.append(f"- `{d.name}`: {what}")
    if len(lines) == 1:
        lines.append("- (empty)")
    return "\n".join(lines)
