"""Audit notes and the standard prompt templates conductor owns.

An item with `audit: true` (or `template: general_audit`) keeps notes beside its
report, `reports/<repo>__NNN_name.audit.md`. A `template: cleanup` item is handed every
note of its repo not yet used by an earlier cleanup; an `audit_used` event records the
notes a cleanup (or a general audit, its own) used, so the next one sees only newer ones.
The template texts live in `templates/` beside this module, versioned with the repo.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .events import Event
from .util import clock, read_text

TEMPLATE_DIR = Path(__file__).with_name("templates")
TEMPLATES = ("cleanup", "general_audit", "test_perf")     # what `template:` may name
USES_NOTES = ("cleanup", "general_audit")                  # a finish not red logs `audit_used`


def template_text(name: str) -> str:
    return read_text(TEMPLATE_DIR / f"{name}.md")


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
    return bool(meta.get("audit")) or meta.get("template") == "general_audit"


@dataclass
class Note:
    item: str           # the item that wrote it
    path: Path
    finish: Event       # its finish event
    used_by: Event | None = None      # the `audit_used` event that consumed it


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
        try:
            text = read_text(note_path(cfg, repo, name)).strip()
        except (OSError, UnicodeDecodeError) as exc:
            text = f"(could not read the notes: {exc})"
        parts.append(f"### Notes from {item_id}{when}\n\n{text}\n")
    return "\n".join(parts)
