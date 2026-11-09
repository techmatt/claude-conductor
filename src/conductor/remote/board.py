"""The broker's state: workers, jobs, shards, leases and fingerprint gates. Pure and
locked; the HTTP layer (`broker.py`) and the tests drive it with an explicit clock.

Placement lives here and in `client.py`: a worker pulls (`poll`); the main box claims
(`claim`), and once nothing is left unleased it re-runs a shard still out on a worker,
the first copy back winning (`result`). Outputs are bytes, never re-encoded."""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
KINDS = {"browser": "wasm", "local-remote": "native", "main": "native"}   # worker kind -> engine
LOG_CHARS = 64_000              # a worker's log, per result
LOG_FILE_CAP = 2_000_000        # a worker's log file per job stops growing here
WORKER_FORGET_SECONDS = 3600.0


@dataclass
class Worker:
    id: str
    kind: str
    cores: int = 1
    label: str = ""
    first_seen: float = 0.0
    last_seen: float = 0.0
    gates: dict[str, tuple[str, float]] = field(default_factory=dict)   # key -> (pass|fail|pending, when)
    shards: int = 0             # shards it won

    @property
    def engine(self) -> str:
        return KINDS[self.kind]


@dataclass
class Shard:
    id: int
    first: int
    count: int
    leases: dict[str, float] = field(default_factory=dict)   # worker id -> leased at
    done: dict | None = None     # {worker, kind, engine, ms}


@dataclass
class Job:
    id: str
    key: str
    args: list[str]
    shards: list[Shard]
    native: dict | None          # {cwd, argv} for native workers
    created: float
    folder: Path
    seen: float = 0.0            # the main box's last call: a job it stopped calling is abandoned
    gate_failures: list[dict] = field(default_factory=list)
    item: str = ""               # the item that ran `farm-seeds`, for the worker page
    rates: dict[str, float] = field(default_factory=dict)   # worker id -> its ms a seed in this job (moving average)

    @property
    def complete(self) -> bool:
        return all(s.done for s in self.shards)


@dataclass
class Key:
    fingerprint: str             # the native reference: the `fingerprint` command on the main box
    bundle: str = "none"         # none | building | ready | failed
    error: str = ""
    wasm: str = ""               # the wasm reference: the bundle's own fingerprint() on the main box

    def reference(self, engine: str) -> str:
        """What a worker of this engine must reproduce (without a wasm reference, the native one)."""
        return self.wasm if engine == "wasm" and self.wasm else self.fingerprint


class Board:
    def __init__(self, root: Path, lease_seconds: float = 60.0, fresh_seconds: float = 10.0,
                 bundle_js: str = "bundle.js", native_argv: list[str] | None = None, clock=time.time):
        self.root = Path(root)
        self.lease_seconds, self.fresh_seconds = lease_seconds, fresh_seconds
        self.bundle_js = bundle_js
        self.native_argv = native_argv   # from the settings, never from a request
        self.clock = clock
        self.lock = threading.Lock()
        self.workers: dict[str, Worker] = {}
        self.jobs: dict[str, Job] = {}
        self.keys: dict[str, Key] = {}
        self.load: dict | None = None    # the main box's load ({cpu, top, text}), set by the broker's loop
        self._n = 0

    # ------------------------------------------------------------ workers
    def hello(self, wid: str, kind: str, cores: int = 1, label: str = "") -> dict:
        if not ID_RE.match(wid) or kind not in KINDS or kind == "main":
            raise ValueError("bad worker id or kind")
        with self.lock:
            now = self.clock()
            for old in [w for w in self.workers.values()
                        if w.kind != "main" and now - w.last_seen > WORKER_FORGET_SECONDS]:
                del self.workers[old.id]
            w = self.workers.get(wid)
            if w is None or w.kind != kind:
                w = self.workers[wid] = Worker(wid, kind, first_seen=now)
            w.cores, w.label, w.last_seen = max(1, min(int(cores), 1024)), str(label)[:80], now
            return {"ok": True, "poll_ms": 500}

    def _fresh(self, w: Worker, now: float) -> bool:
        return now - w.last_seen <= self.fresh_seconds

    def live(self, key: str | None = None) -> list[Worker]:
        """Workers heard from recently, other than the main box; with `key`, not failed for it."""
        now = self.clock()
        return [w for w in self.workers.values() if w.kind != "main" and self._fresh(w, now)
                and not (key and w.gates.get(key, ("",))[0] == "fail")]

    def _leased(self, shard: Shard, now: float) -> bool:
        """A live lease: within its timeout and held by a worker still heard from."""
        for wid, at in shard.leases.items():
            w = self.workers.get(wid)
            if now - at <= self.lease_seconds and w and (w.kind == "main" or self._fresh(w, now)):
                return True
        return False

    def _active(self, now: float) -> list[Job]:
        """Open jobs, oldest first; one whose main box went quiet is dropped (`farm-seeds` killed)."""
        for job in [j for j in self.jobs.values() if now - j.seen > max(self.lease_seconds, 3 * self.fresh_seconds)]:
            del self.jobs[job.id]
        return sorted((j for j in self.jobs.values() if not j.complete and now - j.seen <= self.fresh_seconds),
                      key=lambda j: j.created)

    def poll(self, wid: str) -> dict:
        with self.lock:
            w = self.workers.get(wid)
            if w is None:
                return {"task": "hello"}          # unknown (a broker restart): say hello again
            now = self.clock()
            w.last_seen = now
            for job in self._active(now):
                k = self.keys[job.key]
                if (w.engine == "wasm" and k.bundle != "ready") or (w.engine == "native" and not job.native):
                    continue
                state, at = w.gates.get(job.key, ("", 0.0))
                if state == "fail" or (state == "pending" and now - at <= self.lease_seconds):
                    continue
                if state != "pass":
                    w.gates[job.key] = ("pending", now)
                    return {"task": "gate", "key": job.key, **self._payload(job, w)}
                for s in job.shards:
                    if not s.done and not self._leased(s, now):
                        s.leases[wid] = now
                        return {"task": "shard", "job": job.id, "shard": s.id, "key": job.key, "first": s.first,
                                "count": s.count, "args": job.args, **self._payload(job, w)}
            return {"task": "idle", "wait_ms": 500}

    def _payload(self, job: Job, w: Worker) -> dict:
        if w.engine == "wasm":
            return {"bundle": f"bundle/{job.key}/", "module": self.bundle_js}
        return {"native": job.native}

    def gate(self, wid: str, key: str, value: str) -> bool:
        with self.lock:
            w, k = self.workers.get(wid), self.keys.get(key)
            if w is None or k is None:
                return False
            expected = k.reference(w.engine)
            ok = str(value).strip() == expected
            w.gates[key] = ("pass" if ok else "fail", self.clock())
            if not ok:
                note = {"worker": wid, "kind": w.kind, "label": w.label, "key": key,
                        "reference": "wasm" if w.engine == "wasm" and k.wasm else "native",
                        "expected": expected, "got": str(value)[:200]}
                for job in self.jobs.values():
                    if job.key == key:
                        job.gate_failures.append(note)
                self._append(self.root / "gates.log", json.dumps(note))
            return ok

    # ------------------------------------------------------------ results
    def result(self, wid: str, job_id: str, shard_id: int, ok: bool, output: bytes = b"", log: str = "",
               ms: float = 0.0) -> bool:
        """A shard's output (bytes, kept exactly), or its failure. The first good copy wins."""
        with self.lock:
            job, w = self.jobs.get(job_id), self.workers.get(wid)
            if job is None or w is None or not 0 <= shard_id < len(job.shards):
                return False
            if w.kind != "main" and w.gates.get(job.key, ("",))[0] != "pass":
                return False                       # never gated for this commit
            if w.kind == "main":
                job.seen = self.clock()
            s = job.shards[shard_id]
            s.leases.pop(wid, None)
            if log:
                self._append(job.folder / "logs" / f"{wid}.log", log[-LOG_CHARS:], cap=LOG_FILE_CAP)
            ms = ms if isinstance(ms, (int, float)) and math.isfinite(ms) and ms >= 0 else 0.0
            if ok and ms > 0:                      # per job: another job's args may cost more a seed
                per, old = ms / s.count, job.rates.get(wid)
                job.rates[wid] = per if old is None else 0.7 * old + 0.3 * per
            if s.done or not ok:
                return False
            done = {"worker": wid, "kind": w.kind, "engine": w.engine, "label": w.label, "ms": round(ms)}
            path = job.folder / "shards" / f"{s.first:012d}.out"
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{wid}.tmp")
            tmp.write_bytes(output)
            os.replace(tmp, path)
            s.done = done
            s.leases.clear()
            w.shards += 1
            return True

    # ------------------------------------------------------------ the main box
    def add_job(self, key: str, fingerprint: str, args: list[str], ranges: list[tuple[int, int]],
                cwd: str | None, item: str = "") -> str:
        if not ID_RE.match(key) or not ranges or any(a < 0 or n < 1 for a, n in ranges):
            raise ValueError("bad key or ranges")
        with self.lock:
            k = self.keys.setdefault(key, Key(fingerprint))
            if k.fingerprint != fingerprint:   # the same commit, another answer: trust neither old gate
                self.keys[key] = Key(fingerprint, k.bundle, k.error, k.wasm)
                for w in self.workers.values():
                    w.gates.pop(key, None)
            self._n += 1
            now = self.clock()
            jid = f"j{int(now)}-{self._n}"
            folder = self.root / "jobs" / jid
            folder.mkdir(parents=True, exist_ok=True)
            self.workers.setdefault("main", Worker("main", "main", label="main box", first_seen=now))
            native = {"cwd": cwd, "argv": self.native_argv} if cwd and self.native_argv else None
            self.jobs[jid] = Job(jid, key, list(args), [Shard(i, a, n) for i, (a, n) in enumerate(ranges)],
                                 native, now, folder, seen=now, item=str(item)[:80])
            return jid

    def set_bundle(self, key: str, state: str, error: str = "", wasm: str = "") -> None:
        """A bundle's state; once ready, with its own fingerprint (the wasm reference). A
        changed wasm reference unsets every browser's gate for the key."""
        with self.lock:
            k = self.keys.get(key)
            if k is None:
                return
            k.bundle, k.error = state, error[:300]
            if state == "ready" and wasm.strip() != k.wasm:
                k.wasm = wasm.strip()[:200]
                for w in self.workers.values():
                    if w.engine == "wasm":
                        w.gates.pop(key, None)

    def claim(self, job_id: str, rerun: bool = True) -> dict:
        """The main box's next shard: an unleased one, else (with `rerun`) the oldest still
        out on a worker and not already running here. `done` once every shard is in."""
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return {"gone": True}
            now = self.clock()
            job.seen = self.workers["main"].last_seen = now
            if job.complete:
                return {"done": True}
            free = [s for s in job.shards if not s.done and not self._leased(s, now)]
            if not free and rerun:
                free = sorted((s for s in job.shards if not s.done and "main" not in s.leases),
                              key=lambda s: min(s.leases.values(), default=now))
            if not free:
                return {"wait": True}
            s = free[0]
            s.leases["main"] = now
            return {"shard": s.id, "first": s.first, "count": s.count, "rerun": bool(s.leases.keys() - {"main"})}

    def state(self, job_id: str) -> dict:
        """A job's shards and who ran them; also the main box's heartbeat for it."""
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return {"gone": True}
            job.seen = self.clock()
            return {"complete": job.complete, "done": [s.id for s in job.shards if s.done],
                    "shards": [{"first": s.first, "count": s.count, **(s.done or {})} for s in job.shards],
                    "gate_failures": list(job.gate_failures),
                    "fingerprints": {"native": self.keys[job.key].fingerprint, "wasm": self.keys[job.key].wasm or None}}

    def close(self, job_id: str) -> None:
        with self.lock:
            self.jobs.pop(job_id, None)

    # ------------------------------------------------------------ views
    def summary(self) -> dict:
        with self.lock:
            now = self.clock()
            live = [w for w in self.workers.values() if w.kind != "main" and self._fresh(w, now)]
            return {"workers": [{"id": w.id, "kind": w.kind, "cores": w.cores, "label": w.label, "shards": w.shards,
                                 "gates": {k: v[0] for k, v in w.gates.items()}} for w in live],
                    "jobs": len([j for j in self.jobs.values() if not j.complete])}

    def view(self, wid: str) -> dict:
        """What the worker page shows beside its own measurements, sent with each hello (no
        polling of its own): the current job as counts, this worker's gate, the other
        workers, the main box's native rate and load."""
        with self.lock:
            now = self.clock()
            me = self.workers.get(wid)
            jobs = self._active(now)
            job = None
            if jobs:
                j = jobs[0]
                done = [s for s in j.shards if s.done]
                out = [s for s in j.shards if not s.done and self._leased(s, now)]
                seeds, seeds_done = sum(s.count for s in j.shards), sum(s.count for s in done)
                elapsed = max(0.001, now - j.created)
                eta = (seeds - seeds_done) * elapsed / seeds_done if seeds_done else None
                job = {"id": j.id, "item": j.item, "key": j.key, "shards": len(j.shards), "done": len(done),
                       "mine": sum(1 for s in done if s.done["worker"] == wid), "out": len(out),
                       "pending": len(j.shards) - len(done) - len(out), "seeds": seeds, "seeds_done": seeds_done,
                       "elapsed": round(elapsed, 1), "eta": None if eta is None else round(eta, 1),
                       "jobs": len(jobs)}
            gate = None
            if me and me.gates:
                key = job["key"] if job and job["key"] in me.gates else max(me.gates, key=lambda k: me.gates[k][1])
                k = self.keys.get(key)
                wasm_ref = me.engine == "wasm" and bool(k and k.wasm)
                gate = {"key": key, "state": me.gates[key][0], "reference": "wasm" if wasm_ref else "native",
                        "value": (k.reference(me.engine) if k else "")[:16]}
            bundle = None
            if job and (k := self.keys.get(job["key"])):
                bundle = {"state": k.bundle, "error": k.error}
            rates = jobs[0].rates if jobs else {}
            others = [{"id": w.id, "kind": w.kind, "cores": w.cores, "label": w.label, "shards": w.shards,
                       "ms_seed": round(rates[w.id], 1) if w.id in rates else None}
                      for w in self.workers.values() if w.kind != "main" and w.id != wid and self._fresh(w, now)]
            return {"job": job, "gate": gate, "bundle": bundle, "workers": others,
                    "main": {"ms_seed": round(rates["main"], 1) if "main" in rates else None},
                    "load": self.load}

    def now_line(self) -> str | None:
        """The inbox's *Now* line, or None while no worker is connected."""
        live = self.summary()["workers"]
        if not live:
            return None
        kinds: dict[str, int] = {}
        for w in live:
            kinds[w["kind"]] = kinds.get(w["kind"], 0) + 1
        failed = sum(1 for w in live if "fail" in w["gates"].values())
        return (f"remote workers: {len(live)} connected ({', '.join(f'{n} {k}' for k, n in sorted(kinds.items()))}), "
                f"{sum(w['cores'] for w in live)} cores" + (f"; {failed} failed the fingerprint gate" if failed else ""))

    @staticmethod
    def _append(path: Path, text: str, cap: int | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if cap is not None and path.exists() and path.stat().st_size > cap:
            return
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(text.rstrip("\n") + "\n")
