"""A farm job's record in its folder (`.conductor/remote/jobs/<id>/`): `job.json` from the job's
start (147: a stopped job's shards had no record of their args or out file), its `state`
when it ends without one, the provenance record that replaces it on success, and `header`,
the `[remote] header` command's output (only a job's first shard leads with the project's spec
header, and a reader of the others misread a cell, aicomplete 316)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from . import settings
from .board import who_path

MAIN_DONE = {"worker": "main", "kind": "main", "engine": "native", "label": "main box"}
HEADER = "header"
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def say(text: str) -> None:
    print(f"farm-seeds: {text}", file=sys.stderr, flush=True)


def shard_path(folder: Path, first: int) -> Path:
    return folder / "shards" / f"{first:012d}.out"


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def start(folder: Path, **fields) -> None:
    """`job.json` as the job starts: `state: running`, its key, args, out, range, shard size, item."""
    write_json(folder / "job.json", {"state": "running", **fields})


def mark(folder: Path, state: str) -> None:
    """A job that ended without its record (`cancelled`, `failed`): `job.json`'s `state` says how."""
    try:
        data = json.loads((folder / "job.json").read_text(encoding="utf-8"))
        write_json(folder / "job.json", {**data, "state": state})
    except (OSError, ValueError, TypeError):
        pass


def write_header(r: settings.Remote, cmd: list[str], cwd: Path, folder: Path) -> None:
    """`cmd` (the header command, filled and resolved)'s stdout, once, as `jobs/<id>/header`.
    A failure is said, never fatal."""
    try:
        p = subprocess.run(cmd, cwd=str(cwd), stdin=subprocess.DEVNULL, capture_output=True, timeout=60, **NO_WINDOW)
        if p.returncode != 0:
            raise OSError(f"exit {p.returncode}: {p.stderr.decode('utf-8', 'replace').strip()[-200:]}")
        (folder / HEADER).write_bytes(p.stdout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        say(f"the header command failed ({exc}); {folder / HEADER} not written")


def kept_by(folder: Path, a: int, k: int, state_shard: dict) -> dict:
    """Who ran the copy of a shard that is in the output: the note written beside it when it
    was kept (`board.write_who`), else the broker's state, else the main box (a shard run here
    after the broker went away). The first finish that was kept, never a re-run that lost."""
    try:
        who = json.loads(who_path(shard_path(folder, a)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        who = state_shard if state_shard.get("worker") else MAIN_DONE
    return {"first": a, "count": k, **{x: who.get(x) for x in ("worker", "kind", "engine", "label", "ms") if x in who}}


def record(folder: Path, key: str, args: list[str], ranges, state: dict, out: Path, wall: float,
           fallback: str = "", item: str = "") -> None:
    """Provenance: who ran each shard, whether native and wasm mixed, any gate failure, and
    (`references.fallback`) why browsers were gated against the native reference."""
    shards = [kept_by(folder, a, k, s) for (a, k), s in zip(ranges, state.get("shards") or [{}] * len(ranges))]
    engines = sorted({s["engine"] for s in shards})
    fps = state.get("fingerprints") or {}
    differ = bool(fps.get("wasm")) and fps.get("wasm") != fps.get("native")
    rec = {"state": "done", "item": item, "key": key, "args": args, "out": str(out), "seconds": round(wall, 2),
           "first": ranges[0][0], "count": sum(k for _, k in ranges), "engines": engines,
           "mixed": len(engines) > 1, "shards": shards, "gate_failures": state.get("gate_failures", []),
           "references": {"native": fps.get("native"), "wasm": fps.get("wasm"), "differ": differ}}
    if fallback:
        rec["references"]["fallback"] = fallback
    if rec["mixed"] and differ:
        rec["mixed_note"] = "native and wasm references differ: this output need not equal a local run's"
    write_json(folder / "job.json", rec)
    by: dict[tuple[str, str], int] = {}
    for s in shards:
        label = s.get("label") or ""
        name = ("main box" if s["worker"] == "main" else f"{s['kind']} {label} ({s['worker']})"
                if label and label != s["worker"] else f"{s['kind']} {s['worker']}")
        by[(name, s["engine"])] = by.get((name, s["engine"]), 0) + 1
    parts = ", ".join(f"{name} {n} ({eng})" for (name, eng), n in sorted(by.items()))
    say(f"{sum(s['count'] for s in shards)} seeds in {len(shards)} shards, {wall:.1f}s: {parts}"
        + ("; mixed native and wasm" if rec["mixed"] else "")
        + (", whose references differ (not what a local run gives)" if rec["mixed"] and differ else "")
        + (f"; {fallback}" if fallback else "")
        + f"; record {folder / 'job.json'}")
    for g in rec["gate_failures"]:
        say(f"{g['kind']} worker {g['worker']} failed the fingerprint gate (expected {g['expected']}, got {g['got']}); "
            "it was given no shards")
