"""The local-remote worker: a second process on the main box speaking the browser page's
protocol, running shards with the native command. For tests and failure injection; a
native worker for other PCs could grow from it (it would fetch the code, not use `cwd`).

    python -m conductor.remote.worker <control> [--id ID] [--cores N]
        [--bad-fingerprint] [--die-after N] [--fail-every N] [--hang-after N]
"""

from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import client, settings
from .client import Broker, BrokerDown, resolve, run_native


def _fingerprint(native: dict, fp_argv: list[str]) -> str:
    cwd = Path(native["cwd"])
    out = subprocess.run(resolve(fp_argv, cwd), cwd=str(cwd), capture_output=True, text=True,
                         stdin=subprocess.DEVNULL)
    return out.stdout.strip()


def work(broker: Broker, wid: str, cores: int, fp_argv: list[str], bad_fingerprint: bool = False,
         die_after: int | None = None, fail_every: int | None = None, hang_after: int | None = None,
         stop: threading.Event | None = None) -> None:
    stop = stop or threading.Event()
    lock = threading.Lock()
    ran = [0]
    running: set[tuple[str, int]] = set()          # [job, shard] under way: the beat renews their leases
    broker.call("hello", {"worker": wid, "kind": "local-remote", "cores": cores, "label": f"pid {os.getpid()}",
                          "fresh": True})          # a start re-gates what this id failed before

    def beat() -> None:
        while not stop.wait(2.0):
            try:
                with lock:
                    now = [list(p) for p in running]
                broker.call("hello", {"worker": wid, "kind": "local-remote", "cores": cores,
                                      "label": f"pid {os.getpid()}", "running": now})
            except BrokerDown:
                pass

    def slot() -> None:
        while not stop.is_set():
            try:
                t = broker.call("poll", {"worker": wid})
            except BrokerDown:
                time.sleep(1)
                continue
            task = t.get("task")
            if task in ("hello", "gate"):            # hello: a broker that restarted without its board
                value = "" if task == "hello" else (
                    "not-the-fingerprint" if bad_fingerprint else _fingerprint(t["native"], fp_argv))
                try:
                    broker.call(task, {"worker": wid, "kind": "local-remote", "cores": cores} if task == "hello"
                                else {"worker": wid, "key": t["key"], "value": value})
                except BrokerDown:
                    pass
            elif task == "shard":
                with lock:
                    ran[0] += 1
                    n = ran[0]
                if die_after is not None and n > die_after:
                    os._exit(3)                  # the process dies holding a lease
                if hang_after is not None and n > hang_after:
                    stop.wait()                  # holds the lease and goes quiet
                    return
                native, t0, pair = t["native"], time.time(), (t["job"], t["shard"])
                with lock:
                    running.add(pair)
                try:
                    with tempfile.TemporaryDirectory() as d:
                        out = Path(d) / "shard.out"
                        code = run_native(native["argv"], Path(native["cwd"]), t["first"], t["count"], out, t["args"])
                        ok = code == 0 and out.exists() and not (fail_every and n % fail_every == 0)
                        data = base64.b64encode(out.read_bytes()).decode() if ok else ""
                finally:
                    with lock:
                        running.discard(pair)
                log = f"{time.strftime('%H:%M:%S')} {wid} shard {t['shard']} seeds {t['first']}+{t['count']} " \
                      f"exit {code} in {(time.time() - t0) * 1000:.0f} ms" + ("" if ok else " (failed)")
                try:
                    broker.call("result", {"worker": wid, "job": t["job"], "shard": t["shard"], "ok": ok,
                                           "output_b64": data, "log": log, "ms": (time.time() - t0) * 1000},
                                patience=client.PATIENCE)      # lands on a restarted broker
                except BrokerDown:
                    pass
            else:
                stop.wait(t.get("wait_ms", 500) / 1000)

    threading.Thread(target=beat, daemon=True).start()
    slots = [threading.Thread(target=slot, daemon=True) for _ in range(max(1, cores))]
    for s in slots:
        s.start()
    for s in slots:
        s.join()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m conductor.remote.worker")
    ap.add_argument("control")
    ap.add_argument("--id", default=f"lr{os.getpid()}")
    ap.add_argument("--cores", type=int, default=1)
    ap.add_argument("--bad-fingerprint", action="store_true")
    ap.add_argument("--die-after", type=int)
    ap.add_argument("--fail-every", type=int)
    ap.add_argument("--hang-after", type=int)
    a = ap.parse_args(argv)
    r = settings.load(Path(a.control))
    broker = Broker.find(r) if r else None
    if broker is None:
        print("no broker running for that control folder", file=sys.stderr)
        return 2
    work(broker, a.id, a.cores, r.fingerprint, a.bad_fingerprint, a.die_after, a.fail_every, a.hang_after)
    return 0


if __name__ == "__main__":
    sys.exit(main())
