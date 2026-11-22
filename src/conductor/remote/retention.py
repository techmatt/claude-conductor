"""Retention for `.conductor/remote/` (147): farm job folders, wasm bundles and farm trees
are pruned so none grows without bound. Before it, aicomplete's folder reached 35 GB: 367 job
folders (31 GB of shard outputs, each already merged into its item's out file), 305 bundles
(1.2 GB) and six farm trees (2.7 GB).

Never pruned: a job the broker still has open (`board.json`), one whose `farm-seeds` is alive
(`owner.json`), or one a queued or running item's file names (`j<digits>-<n>`); a bundle such
a job or a held farm tree uses, one being built, or one an item names; a tree a live process
holds. The rest go past `[remote]`'s bounds (`settings.Remote.keep_*`). The daemon runs this
hourly (`disk.py`); `conductor prune` runs it at once."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from . import selftest, settings, tree

JOB_RE = re.compile(r"\bj\d+-\d+\b")
KEY_RE = re.compile(r"\b[0-9a-f]{12}(?:-[0-9a-f]{8})?\b")
BUILD_STALE_SECONDS = 45 * 60           # as `client.BUILD_STALE_SECONDS`: an older lock is a dead build's
REV_STALE_SECONDS = 86400               # a `--at` extraction left by a killed farm-seeds


@dataclass
class Pruned:
    what: str                           # jobs | bundles | trees
    names: list[str] = field(default_factory=list)
    bytes: int = 0
    kept: int = 0
    kept_bytes: int = 0


def size(path: Path) -> int:
    """Bytes under `path`, never following a link or junction."""
    total = 0
    try:
        if path.is_file():
            return path.stat().st_size
    except OSError:
        return 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not _link(Path(root) / d)]
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def _link(p: Path) -> bool:
    try:
        return p.is_symlink() or p.is_junction()
    except OSError:
        return True


def remove(path: Path) -> None:
    """Delete a file or folder, read-only files too; a link is unlinked, never followed."""
    def writable(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if _link(path) or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path, onexc=writable)


def item_text(control: Path) -> str:
    """Every queued or running item's file, for the job ids and bundle keys it names."""
    parts = []
    for where in ("queue", "running"):
        for p in (control / where).glob("*/*.md"):
            try:
                parts.append(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return "\n".join(parts)


def _board(state: Path) -> tuple[set[str], set[str]]:
    """The saved board's open jobs and their keys."""
    try:
        data = json.loads((state / "board.json").read_text(encoding="utf-8"))
        jobs = [j for j in data.get("jobs") or [] if isinstance(j, dict)]
        return {str(j.get("id")) for j in jobs}, {str(j.get("key")) for j in jobs}
    except (OSError, ValueError, AttributeError):
        return set(), set()


def _owner_alive(folder: Path) -> bool:
    try:
        pid = int(json.loads((folder / "owner.json").read_text(encoding="utf-8"))["pid"])
        return psutil.pid_exists(pid)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _mtime(*paths: Path) -> float:
    out = 0.0
    for p in paths:
        try:
            out = max(out, p.stat().st_mtime)
        except OSError:
            pass
    return out


def _held_keys(state: Path) -> set[str]:
    out = set()
    for meta in (state / tree.TREES).glob("*.json"):
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if any(tree._alive(int(k), v) for k, v in (data.get("holds") or {}).items()):
            out.add(str(data.get("key")))
    return out


def prune_jobs(r: settings.Remote, text: str, dry: bool = False, now: float | None = None) -> Pruned:
    """Job folders, newest first: one past `keep_jobs_days` old, or past `keep_jobs_gb` in all
    (the newest always stays), goes unless something still uses it."""
    now = time.time() if now is None else now
    out = Pruned("jobs")
    base = r.state / "jobs"
    if not base.is_dir():
        return out
    open_ids, _ = _board(r.state)
    named = set(JOB_RE.findall(text))
    rows = []
    for p in base.iterdir():
        if p.is_dir() and p.name.startswith("j"):
            rows.append((_mtime(p, p / "shards", p / "job.json", p / "main.log"), p))
    rows.sort(reverse=True)
    total, cap, cutoff = 0, r.keep_jobs_gb * 1e9, now - r.keep_jobs_days * 86400
    for i, (at, p) in enumerate(rows):
        n = size(p)
        total += n
        used = p.name in open_ids or p.name in named or _owner_alive(p)
        if used or (at >= cutoff and (i == 0 or total <= cap)):
            out.kept, out.kept_bytes = out.kept + 1, out.kept_bytes + n
            continue
        total -= n
        if not dry:
            remove(p)
        out.names.append(p.name)
        out.bytes += n
    return out


def prune_bundles(r: settings.Remote, text: str, dry: bool = False, now: float | None = None) -> Pruned:
    """Bundles by key (its folder, `.build.log`, `.spawn.log`, `.failed`), newest first: past
    `keep_bundles` or `keep_bundles_days` old, unless in use or being built."""
    now = time.time() if now is None else now
    out = Pruned("bundles")
    base = r.state / "bundles"
    if not base.is_dir():
        return out
    groups: dict[str, list[Path]] = {}
    for p in base.iterdir():
        groups.setdefault(p.name.split(".", 1)[0], []).append(p)
    _, open_keys = _board(r.state)
    keep = open_keys | _held_keys(r.state) | set(KEY_RE.findall(text))
    rows = sorted(((_mtime(*ps), k, ps) for k, ps in groups.items()), reverse=True)
    cutoff = now - r.keep_bundles_days * 86400
    for i, (at, key, paths) in enumerate(rows):
        building = any(".tmp" in p.name or (p.name.endswith(".lock") and now - _mtime(p) < BUILD_STALE_SECONDS)
                       for p in paths)
        n = sum(size(p) for p in paths)
        if key in keep or building or selftest.is_test(key) or (i < r.keep_bundles and at >= cutoff):
            out.kept, out.kept_bytes = out.kept + 1, out.kept_bytes + n
            continue
        if not dry:
            for p in paths:
                remove(p)
        out.names.append(key)
        out.bytes += n
    return out


def prune_trees(r: settings.Remote, dry: bool = False, now: float | None = None) -> Pruned:
    """Farm trees: while there are more than `keep_trees`, the highest-numbered one no live
    process holds goes (a free tree is reused lowest first, so the high ones are the idle ones).
    Also a `--at` extraction a killed run left behind."""
    now = time.time() if now is None else now
    out = Pruned("trees")
    trees = r.state / tree.TREES
    for tmp in r.state.glob("rev-*"):
        if tmp.is_dir() and now - _mtime(tmp) > REV_STALE_SECONDS:
            n = size(tmp)
            if not dry:
                remove(tmp)
            out.names.append(tmp.name)
            out.bytes += n
    if not trees.is_dir():
        return out
    with tree._Lock(r.state / tree.LOCK):
        metas = sorted((p for p in trees.glob("*.json") if p.stem.isdigit()), key=lambda p: int(p.stem))
        free = []
        for meta in metas:
            data = tree._read(meta)
            if not any(tree._alive(int(k), v) for k, v in (data.get("holds") or {}).items()):
                free.append(meta)
        count = len(metas)
        for meta in reversed(free):
            if count <= r.keep_trees:
                break
            folder = trees / meta.stem
            n = size(folder)
            if not dry:
                remove(folder)
                meta.unlink(missing_ok=True)
            out.names.append(meta.stem)
            out.bytes += n
            count -= 1
        for meta in metas:
            if meta.stem not in out.names:
                out.kept, out.kept_bytes = out.kept + 1, out.kept_bytes + size(trees / meta.stem)
    return out


def run(control: Path, dry: bool = False) -> list[Pruned]:
    """Every rule, for a project with `[remote]` (else nothing)."""
    try:
        r = settings.load(control)
    except settings.RemoteError:
        return []
    if r is None or not r.state.is_dir():
        return []
    text = item_text(r.control)
    return [prune_jobs(r, text, dry), prune_bundles(r, text, dry), prune_trees(r, dry)]
