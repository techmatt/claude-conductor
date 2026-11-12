"""Kept reads: what an item leaves in its worktree's `out/keep/` (a farm's `games.jsonl`,
its specs, a readonly item's write-up) outlives the worktree, so the next item reads it
instead of farming it again (aicomplete 205 re-farmed two reads, ~15 minutes).

When a worktree is discarded its `out/keep/` moves to `.conductor/kept/<repo>/<item>/`;
each repo keeps its newest `KEEP_ITEMS` and at most `KEEP_BYTES` in all, so disk doesn't
grow. The preamble's tail names what is kept for the item's repo (`section`).
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

from .config import Config
from .util import clock

KEEP_DIR = Path("out") / "keep"
KEEP_ITEMS = 3                   # newest items kept per repo
KEEP_BYTES = 2_000_000_000       # and at most this much per repo; the newest always stays

SECTION = """

---

Kept reads. Earlier items in this repo left read outputs (their `out/keep/`), newest first.
Reuse one instead of producing it again when its base still fits (say which in your report):
{rows}
"""


def root(cfg: Config, repo: str) -> Path:
    return cfg.state_dir / "kept" / repo


def save(cfg: Config, repo: str, worktree: Path) -> Path | None:
    """Move `worktree/out/keep/` aside before the worktree goes; prune to the bounds.
    Where it went, or None (nothing to keep, or the move failed: never blocks a discard)."""
    src = Path(worktree) / KEEP_DIR
    try:
        if not src.is_dir() or not any(src.iterdir()):
            return None
        dest = root(cfg, repo) / Path(worktree).name
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        os.utime(dest)                       # its age is when it was kept
    except OSError:
        return None
    prune(cfg, repo)
    return dest


def listed(cfg: Config, repo: str) -> list[Path]:
    """The repo's kept folders, newest first."""
    base = root(cfg, repo)
    try:
        return sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []


def size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def prune(cfg: Config, repo: str) -> list[str]:
    """Drop all but the newest `KEEP_ITEMS`, then the oldest past `KEEP_BYTES`; the names dropped."""
    dropped, total = [], 0
    for i, p in enumerate(listed(cfg, repo)):
        total += size(p)
        if i >= KEEP_ITEMS or (i and total > KEEP_BYTES):
            shutil.rmtree(p, ignore_errors=True)
            dropped.append(p.name)
    return dropped


def section(cfg: Config, repo: str) -> str:
    """The prompt's *Kept reads* section for an item of `repo`, or nothing."""
    rows = []
    for p in listed(cfg, repo):
        files = sorted(f.relative_to(p).as_posix() for f in p.rglob("*") if f.is_file())
        shown = ", ".join(files[:6]) + (f" and {len(files) - 6} more" if len(files) > 6 else "")
        when = clock(datetime.fromtimestamp(p.stat().st_mtime).astimezone())
        rows.append(f"- `{p.resolve()}` ({p.name}, kept {when}): {shown}")
    return SECTION.format(rows="\n".join(rows)) if rows else ""
