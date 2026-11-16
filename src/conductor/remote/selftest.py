"""The built-in worker test (124): a synthetic farm job conductor ships itself, so remote
workers can be tested on any project's broker with no project build, gate or `native_build`.

    conductor remote test <control>        (also the app's Workers tab: **Test workers**)

Its bundle is `selftest.js`: each seed steps a 32-bit LCG `ITERS` times. Browsers run it as
they run any bundle; the main box runs the same loop in Python, slowly, so it takes part as
it does in a real job. Every result is checked by jumping the LCG ahead (`expected`, cheap),
on the broker as it lands: a wrong copy is never kept. Native (local-remote) workers sit it
out, since they run the project's own command. The result goes to `selftest.json`, which
the Workers tab and `remote-diag.md` show."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from . import settings

SOURCE = Path(__file__).with_name("selftest.js")
MODULE = "selftest.js"
PREFIX = "selftest-"
RESULT = "selftest.json"            # under .conductor/remote/
SHARDS, SIZE = 40, 4                # the job: 40 shards of 4 seeds
TIMEOUT = 180.0
M = 0xFFFFFFFF
A, C = 1664525, 1013904223
ITERS = int(re.search(r"ITERS = (\d+)", SOURCE.read_text(encoding="utf-8")).group(1))


def key() -> str:
    """The job's key and bundle folder: the source's hash, so a changed test is a new bundle
    (a tab caches a bundle folder for good)."""
    return PREFIX + hashlib.sha256(SOURCE.read_bytes()).hexdigest()[:12]


def is_test(k: str) -> bool:
    return k.startswith(PREFIX)


def _start(seed: int) -> int:
    return (seed * 2654435761) & M


def _jump(n: int) -> tuple[int, int]:
    """The LCG step applied n times, as one map x -> a*x + c (mod 2**32)."""
    a, c, sa, sc = 1, 0, A, C               # result so far; the step squared k times
    while n:
        if n & 1:
            a, c = (sa * a) & M, (sa * c + sc) & M
        sa, sc = (sa * sa) & M, (sa * sc + sc) & M
        n >>= 1
    return a, c


_JA, _JC = _jump(ITERS)


def one(seed: int) -> int:
    return (_JA * _start(seed) + _JC) & M


def expected(first: int, count: int) -> str:
    """A shard's exact output, as the bundle's run() gives it."""
    return "".join(f"{s} {one(s)}\n" for s in range(first, first + count))


def reference() -> str:
    return expected(0, 1).strip()


def slow(seed: int) -> int:
    """The busy-loop itself, as the main box runs it (the real cost, not the shortcut)."""
    x = _start(seed)
    for _ in range(ITERS):
        x = (A * x + C) & M
    return x


def install(state: Path) -> str:
    """The bundle where the broker serves bundles; its key."""
    k = key()
    folder = state / "bundles" / k
    if not (folder / MODULE).exists():
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f"{MODULE}.{os.getpid()}.tmp"
        tmp.write_bytes(SOURCE.read_bytes())
        os.replace(tmp, folder / MODULE)
    return k


def _write(state: Path, data: dict) -> None:
    tmp = state / f"{RESULT}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, state / RESULT)


def read(state: Path) -> dict | None:
    try:
        return json.loads((state / RESULT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def rows(tally: dict, labels: dict) -> list[dict]:
    """Per worker: shards offered, finished, credited (won), seconds a shard, copies the main
    box overtook (it won a shard the worker held), and results that didn't match."""
    out = []
    for wid, t in sorted(tally.items(), key=lambda p: (p[0] != "main", p[0])):
        fin = t.get("finished", 0)
        out.append({"id": wid, "label": labels.get(wid, wid), "offered": t.get("offered", 0), "finished": fin,
                    "credited": t.get("credited", 0), "overtaken": t.get("overtaken", 0),
                    "mismatched": t.get("mismatched", 0),
                    "s_shard": round(t.get("ms", 0) / fin / 1000, 2) if fin else None})
    return out


def line(w: dict) -> str:
    match = "all matched" if not w["mismatched"] else f"{w['mismatched']} did not match"
    per = f", {w['s_shard']} s a shard" if w["s_shard"] is not None else ""
    over = f", {w['overtaken']} overtaken by the main box" if w["overtaken"] else ""
    return (f"{w['label']}: offered {w['offered']}, finished {w['finished']}, credited {w['credited']}"
            f"{per}{over}; {match}")


def run(control: Path, timeout: float = TIMEOUT, say=print) -> int:
    """Run the test job on the project's broker: 0 when it completed, every kept shard
    matched and a remote worker was credited; 1 otherwise; 2 when it couldn't start."""
    from .client import Broker, BrokerDown, split
    r = settings.load(control)
    if r is None:
        say("no [remote] section in conductor.toml: there is no broker to test")
        return 2
    broker = Broker.find(r)
    started = time.time()
    try:
        live = broker.call("status", {}) if broker else None
    except BrokerDown:
        live = None
    if live is None:
        say("the broker is not running (start the daemon)")
        return 2
    if not live.get("browsers"):
        say("no browser worker is connected: open the worker link in a browser first")
        _write(r.state, {"at": started, "ok": False, "note": "no browser worker connected", "workers": []})
        return 2
    k = install(r.state)
    ref = reference()
    jid = broker.call("job", {"key": k, "fingerprint": ref, "args": [], "ranges": split(0, SHARDS * SIZE, SIZE),
                              "cwd": None, "item": "worker test"})["job"]
    broker.call("bundle", {"key": k, "state": "ready", "wasm": ref})
    _write(r.state, {"at": started, "running": True, "job": jid, "workers": []})
    say(f"worker test: job {jid}, {SHARDS} shards of {SIZE} seeds, {live['browsers']} browser(s) connected")
    state: dict = {}
    end = time.time() + timeout
    try:
        while time.time() < end:
            c = broker.call("claim", {"job": jid})
            if c.get("done") or c.get("gone"):
                break
            if "shard" not in c:
                time.sleep(0.2)
                broker.call("state", {"job": jid})
                continue
            t, out, lost = time.time(), [], False
            for s in range(c["first"], c["first"] + c["count"]):
                st = broker.call("state", {"job": jid})        # also the job's heartbeat
                if c["shard"] in st.get("done", []):
                    lost = True                                 # a worker's copy won: stop here
                    break
                out.append(f"{s} {slow(s)}\n")
            broker.call("result", {"worker": "main", "job": jid, "shard": c["shard"], "ok": not lost,
                                   "output": "".join(out), "ms": (time.time() - t) * 1000})
        state = broker.call("state", {"job": jid})
    except BrokerDown as exc:
        say(f"the broker went away: {exc}")
    finally:
        try:
            broker.call("close", {"job": jid})
        except BrokerDown:
            pass
    workers = rows(state.get("tally") or {}, state.get("labels") or {})
    complete = bool(state.get("complete"))
    remote = [w for w in workers if w["id"] != "main"]
    ok = complete and not any(w["mismatched"] for w in workers) and any(w["credited"] for w in remote)
    note = ("" if ok else "timed out before every shard was in" if not complete
            else "a result did not match" if any(w["mismatched"] for w in workers)
            else "no remote worker was credited a shard")
    _write(r.state, {"at": started, "seconds": round(time.time() - started, 1), "job": jid, "ok": ok,
                     "note": note, "shards": SHARDS, "workers": workers})
    for w in workers:
        say("  " + line(w))
    say(f"worker test {'passed' if ok else 'failed: ' + note} in {time.time() - started:.0f} s")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    return run(Path(args[0] if args else "."))


if __name__ == "__main__":
    sys.exit(main())
