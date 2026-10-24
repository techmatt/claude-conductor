"""Memory: the box's commit charge and free RAM, each live item's process tree by kind,
and the pressure level the daemon guards with.

    .conductor/memory.jsonl    one compact sample per line, every `memory_sample_seconds`,
                               pruned to the last `memory_keep_hours`

A level is `ok`, `pressure` or `critical`: commit at or over a percent of its limit, or
free RAM at or under a size (`[defaults]` in conductor.toml; 0 turns a threshold off). A
level is left only once it is clear by a margin (`SLACK_PERCENT`, `SLACK_GB`), so a box
hovering at a threshold does not flap.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from . import procs
from .config import Config
from .util import now, parse_stamp, stamp, write_text

GIB = 2 ** 30
LEVELS = ("ok", "pressure", "critical")
SLACK_PERCENT = 3.0
SLACK_GB = 0.5
FILE = "memory.jsonl"


def gb(n: float) -> float:
    return round(n / GIB, 2)


@dataclass
class Tree:
    """One item's processes: totals and, per kind, [count, private bytes]."""
    private: int = 0
    wset: int = 0
    kinds: dict[str, list[int]] = field(default_factory=dict)


def tree(*pids: int | None) -> Tree:
    """Each process in `pids`, every descendant and everything in its job (an executor,
    its subagents, the servers, browsers and shells under it, orphans included; an
    orphan it left in its worktree), by kind."""
    t = Tree()
    members = []
    for pid in sorted(set().union(set(), *(procs.family(p) for p in pids if p))):
        try:
            members.append(psutil.Process(pid))
        except psutil.Error:
            continue
    for p in members:
        try:
            with p.oneshot():
                name = p.name()
                k = procs.kind(name, p.cmdline() if name.lower().startswith("node") else None)
                private, wset = procs.usage(p)
        except psutil.Error:
            continue
        t.private += private
        t.wset += wset
        row = t.kinds.setdefault(k, [0, 0])
        row[0] += 1
        row[1] += private
    return t


def level(s: procs.MemoryStatus, cfg: Config, prev: str = "ok") -> str:
    """The level `s` is at; `prev` keeps a level until it is clear by the slack."""
    pct = 100.0 * s.commit / s.limit if s.limit else 0.0
    free = s.available / GIB

    def at(percent: float, free_gb: float, held: bool) -> bool:
        slack_p, slack_g = (SLACK_PERCENT, SLACK_GB) if held else (0.0, 0.0)
        return bool((percent and pct >= percent - slack_p) or (free_gb and free <= free_gb + slack_g))

    if at(cfg.memory_critical_percent, cfg.memory_critical_free_gb, prev == "critical"):
        return "critical"
    if at(cfg.memory_pressure_percent, cfg.memory_pressure_free_gb, prev in ("pressure", "critical")):
        return "pressure"
    return "ok"


def sample(s: procs.MemoryStatus, lv: str, trees: dict[str, Tree]) -> dict:
    """The compact record stored per sample: GiB, items by private bytes, kinds in MiB."""
    return {
        "at": stamp(), "level": lv, "commit": gb(s.commit), "limit": gb(s.limit),
        "free": gb(s.available), "total": gb(s.total),
        "items": {i: {"private": gb(t.private), "wset": gb(t.wset),
                      "kinds": {k: [c, round(b / 2 ** 20)] for k, (c, b) in sorted(t.kinds.items())}}
                  for i, t in sorted(trees.items(), key=lambda kv: -kv[1].private)},
    }


PRUNE_BYTES = 4_000_000     # a day at the default rate is ~1.5 MB


def store(state_dir: Path, rec: dict, keep_hours: float) -> None:
    """Append a sample; past `PRUNE_BYTES`, keep only the last `keep_hours` of them."""
    path = state_dir / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    if path.stat().st_size < PRUNE_BYTES:
        return
    cutoff = now().timestamp() - keep_hours * 3600
    kept = [ln for ln in path.read_text(encoding="utf-8").splitlines() if _at(ln) >= cutoff]
    write_text(path, "".join(ln + "\n" for ln in kept))


def _at(line: str) -> float:
    try:
        return parse_stamp(json.loads(line)["at"]).timestamp()
    except (ValueError, KeyError, TypeError):
        return 0.0


def latest(state_dir: Path) -> dict | None:
    """The newest sample, read from the end of the file."""
    path = state_dir / FILE
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 65536))
            tail = fh.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for ln in reversed(tail):
        try:
            return json.loads(ln)
        except ValueError:
            continue
    return None


def top(rec: dict, n: int = 5) -> list[dict]:
    """The biggest item × kind rows of a sample: `{item, kind, count, private_mb}`."""
    rows = [{"item": i, "kind": k, "count": c, "private_mb": mb}
            for i, t in rec.get("items", {}).items() for k, (c, mb) in t.get("kinds", {}).items()]
    return sorted(rows, key=lambda r: -r["private_mb"])[:n]


def others(skip: set[int], n: int = 3) -> list[dict]:
    """The biggest processes outside conductor's items, grouped by name: what else is
    using the box (`{name, count, private_mb}`). A full scan: only at a level change."""
    by: dict[str, list[int]] = {}
    for p in psutil.process_iter(["name"]):
        if p.pid in skip:
            continue
        try:
            private, _ = procs.usage(p)
        except psutil.Error:
            continue
        row = by.setdefault(p.info.get("name") or "?", [0, 0])
        row[0] += 1
        row[1] += private
    return [{"name": k, "count": c, "private_mb": round(b / 2 ** 20)}
            for k, (c, b) in sorted(by.items(), key=lambda kv: -kv[1][1])[:n]]


def fresh(rec: dict | None, cfg: Config) -> bool:
    """Is this sample recent enough to speak for the daemon (three intervals, at least 90s)?"""
    if not rec or not cfg.memory_sample_seconds:
        return False
    try:
        age = now().timestamp() - parse_stamp(rec["at"]).timestamp()
    except (KeyError, ValueError, TypeError):
        return False
    return age <= max(3 * cfg.memory_sample_seconds, 90)


def line(cfg: Config, rec: dict | None = None) -> tuple[str, str]:
    """(text, level) for a status line: the box measured now, the level and the biggest
    item from the daemon's latest sample when it is fresh (else the level computed now).
    `memory 29.4 of 36.6 GiB committed (80%), 10.9 GiB free · biggest wordwarriors/040 2.1 GiB · ok`."""
    rec = rec if rec is not None else latest(cfg.state_dir)
    s = procs.memory_status()
    live = fresh(rec, cfg)
    lv = rec["level"] if live else level(s, cfg)
    pct = round(100 * s.commit / s.limit) if s.limit else 0
    text = f"memory {gb(s.commit):.1f} of {gb(s.limit):.1f} GiB committed ({pct}%), {gb(s.available):.1f} GiB free"
    if live and rec.get("items"):
        item, t = next(iter(rec["items"].items()))
        text += f" · biggest {item} {t['private']:.1f} GiB"
    if lv != "ok":
        text += f" · {lv.upper()}: launches and stages paused"
    else:
        text += " · ok"
    return text, lv
