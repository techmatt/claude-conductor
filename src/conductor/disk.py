"""Disk retention (147): conductor keeps its own disk use bounded without being asked.

- **Kept worktrees.** A red item's worktree stays for inspection (`worktrees.md`). Once
  nobody needs to look (its finish is `reviewed`, or its repo's hold is cleared after it), its
  rebuildable output goes: each folder named in the repo's `rebuildable` (default `target`,
  `node_modules`) that git ignores, at any depth, never through a shared link and never under
  `out/`. Sources, `out/` and the branch stay, so `land:` and `retry:` still work (a retry
  rebuilds). aicomplete's 112_price_correction kept a 2.1 GB `target/` for 18 days.
- **`.conductor/remote/`**: farm jobs, bundles and trees (`remote/retention.py`).

Each removal is a `pruned` event with its size. The daemon sweeps hourly, off its loop
(`daemon_reap.keep_disk`); `conductor prune <control> [--dry-run]` sweeps at once."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .config import Config
from .events import EventLog

SWEEP_SECONDS = 3600.0
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def _link(p: Path) -> bool:
    try:
        return p.is_symlink() or p.is_junction()
    except OSError:
        return True


def due(cfg: Config, log: EventLog, live: set[str]) -> list[tuple[str, str, Path]]:
    """(repo, item id, worktree) for each kept worktree whose rebuildable output can go: not
    running (`live`, ids), its last finish reviewed or its repo cleared since, not run again
    since, and not already trimmed since."""
    out = []
    for repo in cfg.repos:
        opts = cfg.opts(repo)
        base = cfg.worktree(repo, "")
        if not opts.worktrees or not opts.rebuildable or not base.is_dir():
            continue
        for wt in sorted(p for p in base.iterdir() if p.is_dir() and not _link(p)):
            item = f"{repo}/{wt.name}"
            if item in live:
                continue
            fin = log.last("finish", item=item)
            if fin is None:
                continue
            later = [e for kind in ("start", "pruned") if (e := log.last(kind, item=item)) and e.id > fin.id]
            if later:
                continue
            reviewed = any(e.detail.get("finish") == fin.id for e in log.query(since_id=fin.id - 1,
                                                                              kinds=["reviewed"], item=item))
            clear = log.last("clear", repo=repo)
            if reviewed or (clear and clear.id > fin.id):
                out.append((repo, item, wt))
    return out


def ignored(wt: Path, rel: str) -> bool:
    try:
        return subprocess.run(["git", "-C", str(wt), "check-ignore", "-q", rel], capture_output=True,
                              **NO_WINDOW).returncode == 0
    except OSError:
        return False


def rebuildable(wt: Path, names: list[str]) -> list[str]:
    """The worktree's folders named in `names` that git ignores (relative, `/`), outermost only."""
    want, found = set(names), []
    for root, dirs, _ in os.walk(wt):
        here = Path(root)
        keep = []
        for d in dirs:
            p = here / d
            rel = p.relative_to(wt).as_posix()
            if d == ".git" or _link(p) or rel == "out":
                continue
            if d in want and ignored(wt, rel):
                found.append(rel)
                continue
            keep.append(d)
        dirs[:] = keep
    return sorted(found)


def trim(wt: Path, names: list[str], dry: bool = False) -> dict[str, int]:
    """Remove the worktree's rebuildable folders; their sizes by relative path."""
    from .remote.retention import remove, size
    out = {}
    for rel in rebuildable(wt, names):
        p = wt / rel
        out[rel] = size(p)
        if not dry:
            try:
                remove(p)
            except OSError:
                out.pop(rel)
    return out


def sweep(cfg: Config, log: EventLog, live: set[str], dry: bool = False) -> list[dict]:
    """Everything above once; each removal logged `pruned` (never with `dry`). The details logged."""
    rows = []
    for repo, item, wt in due(cfg, log, live):
        if not dry and (s := log.last("start", item=item)) and (f := log.last("finish", item=item)) and s.id > f.id:
            continue                                # a retry started meanwhile
        dirs = trim(wt, cfg.opts(repo).rebuildable, dry)
        row = {"what": "build output", "item": item, "repo": repo, "path": wt.as_posix(), "dirs": dirs,
               "bytes": sum(dirs.values())}
        rows.append(row)
        if not dry:                                 # logged even when empty: the worktree isn't walked again
            log.append("pruned", item=item, repo=repo, paths=[wt.as_posix()],
                       **{k: v for k, v in row.items() if k not in ("item", "repo")})
    from .remote import retention
    for p in retention.run(cfg.control, dry):
        row = {"what": f"farm {p.what}", "removed": len(p.names), "bytes": p.bytes, "names": p.names[:20],
               "kept": p.kept, "kept_bytes": p.kept_bytes}
        rows.append(row)
        if p.names and not dry:
            log.append("pruned", **row)
    return rows


def gb(n: int) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 1e8 else f"{n / 1e6:.1f} MB"


def lines(rows: list[dict], dry: bool = False) -> list[str]:
    """`conductor prune`'s output, a line a row."""
    verb = "would remove" if dry else "removed"
    out = []
    for r in rows:
        if r["what"] == "build output":
            parts = ", ".join(f"{k} {gb(v)}" for k, v in r["dirs"].items()) or "nothing rebuildable"
            out.append(f"{r['item']}: {verb} {parts}")
        else:
            out.append(f"{r['what']}: {verb} {r['removed']} ({gb(r['bytes'])}); kept {r['kept']} ({gb(r['kept_bytes'])})")
    return out or ["nothing to prune"]
