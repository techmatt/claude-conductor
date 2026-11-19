"""Why a shard failed (134). Each shard's native run writes its output (stdout and stderr)
to a file of its own, and a failure keeps that output's tail. The main box keeps its own;
a worker sends its tail with the failed result (`error`), the board keeps it on the job
(`state`'s `errors`), and `farm-seeds` prints it when the run fails. Shards that failed the
same way are shown once, with a count: aicomplete 282 failed every shard on one out-of-range
config value and saw only "exit 2" per shard, so its executor reran one shard natively to
read the error.

A failure's tail can hold "error", which ends an item's `wait_for`. That is fine here,
because only a failing run prints it and `farm-seeds` exits straight after."""

from __future__ import annotations

import os
import re
import shutil
import sys
import threading
from pathlib import Path

TAIL_LINES = 20
LINE_CHARS = 300
TAIL_CHARS = 8_000          # what a worker may send, and the board keeps, per failure
KEPT = 50                   # failures a job keeps


def tail(text: str, lines: int = TAIL_LINES) -> str:
    """The last `lines` non-blank lines, each cut to LINE_CHARS."""
    rows = [x.rstrip()[:LINE_CHARS] for x in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join([x for x in rows if x.strip()][-lines:])


def read_tail(path: Path) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 64_000))
            return tail(f.read().decode("utf-8", errors="replace"))
    except OSError:
        return ""


def run_shard(argv: list[str], cwd: Path, first: int, count: int, out: Path, args: list[str],
              log=None, stop=None) -> tuple[int, str]:
    """`client.run_native` with its output in a file of its own beside `out`, then appended
    to `log`: its exit code and, on a failure, that output's tail."""
    from .client import run_native
    cap = out.with_name(f".{out.name}.{os.getpid()}.{threading.get_ident()}.log")
    try:
        with open(cap, "wb") as f:
            code = run_native(argv, cwd, first, count, out, args, log=f, stop=stop)
        if log:
            with open(cap, "rb") as f:
                shutil.copyfileobj(f, log)
            log.flush()
        return code, read_tail(cap) if code not in (0, -1) or (code == 0 and not out.exists()) else ""
    finally:
        cap.unlink(missing_ok=True)


def _shape(text: str) -> str:
    """A tail with its numbers blanked: shards failing the same way differ only in seeds."""
    return re.sub(r"\d+", "#", text)


def _seeds(f: dict) -> str:
    a, k = f["seeds"]
    return f"seeds {a}..{a + k - 1}"


def report(say, fails: list[dict], total: int) -> None:
    """Each distinct failure once, with its shards' count and its output's tail. `fails`:
    `{seeds: (first, count), code: int | None, who: str, tail: str}`."""
    groups: dict[str, list[dict]] = {}
    for f in sorted(fails, key=lambda f: f["seeds"]):
        groups.setdefault(_shape(f["tail"]), []).append(f)
    for g in groups.values():
        n, first = len(g), g[0]
        who = ", ".join(sorted({f["who"] for f in g}))
        codes = "/".join(sorted({"exit " + str(f["code"]) if f["code"] is not None else "failed" for f in g}))
        what = (f"{n} of the {total} shards failed the same way ({codes}, on {who}; first {_seeds(first)})"
                if n > 1 else f"shard {_seeds(first)} failed ({codes}, on {who})")
        say(what + ("; its output ends:" if first["tail"] else "; it printed nothing"))
        for line in first["tail"].splitlines():
            print(f"  | {line}", file=sys.stderr, flush=True)


def remote(state: dict) -> list[dict]:
    """The workers' failures a broker's `state` reply holds, in `report`'s form."""
    out = []
    for e in state.get("errors") or []:
        try:
            out.append({"seeds": (int(e["first"]), int(e["count"])), "code": None,
                        "who": str(e.get("label") or e["worker"]), "tail": tail(str(e.get("error", "")))})
        except (KeyError, TypeError, ValueError):
            continue
    return out
