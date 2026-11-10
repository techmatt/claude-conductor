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
from dataclasses import asdict, dataclass, field
from pathlib import Path

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
KINDS = {"browser": "wasm", "local-remote": "native", "main": "native"}   # worker kind -> engine
LOG_CHARS = 64_000              # a worker's log, per result
LOG_FILE_CAP = 2_000_000        # a worker's log file per job stops growing here
WORKER_FORGET_SECONDS = 3600.0
LEASE_FLOOR = 60.0              # a derived lease is never shorter than this
LEASE_FACTOR = 4.0              # a derived lease: this many times a shard's expected time
BUILD_STUCK_SECONDS = 20 * 60   # a bundle building this long is shown as an error (client.BUILD_STALE_SECONDS)
STATES = ("offline", "idle", "preparing", "working", "draining", "error")   # a worker's state (`Board.state_of`)
STEPS = {"downloading": "downloading the bundle", "compiling": "compiling the bundle",
         "starting": "starting the bundle"}   # what a page may say it is preparing (its hello's `report`)


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
    day: str = ""               # the local date `today` counts for
    today: int = 0              # shards it won that day (the app's Workers tab)
    report: dict = field(default_factory=dict)   # what the worker says it is doing (its hello), with `at`

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
    since: float = 0.0           # when `bundle` last changed (a build's elapsed time)

    def reference(self, engine: str) -> str:
        """What a worker of this engine must reproduce (without a wasm reference, the native one)."""
        return self.wasm if engine == "wasm" and self.wasm else self.fingerprint


def _drop_failed(w: Worker) -> None:
    """Forget the keys this worker failed, so its next poll gates them again."""
    for key in [k for k, (state, _) in w.gates.items() if state == "fail"]:
        del w.gates[key]


def _report(report, now: float) -> dict:
    """A worker's own word on what it is doing (the page's hello), kept only in this shape:
    `{state: preparing|draining|error|paused, step, key, for (seconds in), error}`; `since`
    is put on the broker's clock, so a skewed laptop clock costs nothing."""
    if not isinstance(report, dict) or report.get("state") not in ("preparing", "draining", "error", "paused"):
        return {}
    out = {"state": report["state"], "at": now}
    if report["state"] == "preparing":
        out["step"] = report.get("step") if report.get("step") in STEPS else "starting"
    key = report.get("key")
    if isinstance(key, str) and ID_RE.match(key):
        out["key"] = key
    try:
        out["since"] = now - max(0.0, min(float(report.get("for", 0)), 86400.0))
    except (TypeError, ValueError):
        out["since"] = now
    if report["state"] == "error":
        out["error"] = str(report.get("error", ""))[:200]
    return out


def _short(key: str) -> str:
    return key[:7] + (" +changes" if "-" in key else "")


def who_path(out: Path) -> Path:
    return out.with_suffix(".who.json")


def write_who(out: Path, done: dict) -> None:
    """Who ran the kept copy of a shard (`shards/<first>.who.json` beside `<first>.out`)."""
    tmp = out.with_suffix(f".who.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(done) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, who_path(out))


class Board:
    def __init__(self, root: Path, lease_seconds: float | None = None, fresh_seconds: float = 10.0,
                 bundle_js: str = "bundle.js", native_argv: list[str] | None = None, clock=time.time,
                 seed_seconds: float = 0.05):
        """`lease_seconds` None: each lease is sized to its shard (`lease_for`)."""
        self.root = Path(root)
        self.lease_seconds, self.fresh_seconds, self.seed_seconds = lease_seconds, fresh_seconds, seed_seconds
        self.bundle_js = bundle_js
        self.native_argv = native_argv   # from the settings, never from a request
        self.clock = clock
        self.lock = threading.Lock()
        self.workers: dict[str, Worker] = {}
        self.jobs: dict[str, Job] = {}
        self.keys: dict[str, Key] = {}
        self.load: dict | None = None    # the main box's load ({cpu, top, text}), set by the broker's loop
        self.gate_fails: list[dict] = []   # recent gate failures, with `at` (the app's problem line)
        self.reruns: list[float] = []      # when the main box re-ran a shard still out on a worker
        self.items: list[str] = []         # items running on the main box (the broker's loop: `running_items`)
        self._wasm: dict[str, str] = {}    # key -> its bundle's one .wasm file (the page compiles it once)
        self._n = 0

    # ------------------------------------------------------------ workers
    def hello(self, wid: str, kind: str, cores: int = 1, label: str = "", fresh: bool = False,
              report=None) -> dict:
        """`fresh`: the first hello of a page load or a worker start, which re-gates every key
        this worker failed (a fix needs no new tab or worker id). `report`: what the worker
        says it is doing that the broker can't see (`_report`), for `state_of`."""
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
            w.cores, w.label, w.last_seen = max(0, min(int(cores), 1024)), str(label)[:80], now
            w.report = _report(report, now)
            if fresh:
                _drop_failed(w)
            return {"ok": True, "poll_ms": 500}

    def _fresh(self, w: Worker, now: float) -> bool:
        return now - w.last_seen <= self.fresh_seconds

    def live(self, key: str | None = None) -> list[Worker]:
        """Workers heard from recently, other than the main box; with `key`, not failed for it."""
        now = self.clock()
        return [w for w in self.workers.values() if w.kind != "main" and self._fresh(w, now)
                and not (key and w.gates.get(key, ("",))[0] == "fail")]

    @property
    def _patience(self) -> float:
        """How long a job's main box or a pending gate is waited for."""
        return self.lease_seconds if self.lease_seconds is not None else LEASE_FLOOR

    def lease_for(self, job: Job, shard: Shard) -> float:
        """How long one worker may hold a shard: `lease_seconds` when set, else LEASE_FACTOR
        times the shard at the slowest rate measured in this job (any worker), or at
        `seed_seconds` before any is, never under LEASE_FLOOR. aicomplete's 4 seeds at 28 s
        is 448 s, where the old flat 60 s re-ran 33 of 48 shards (161)."""
        if self.lease_seconds is not None:
            return self.lease_seconds
        ms = max([*job.rates.values(), self.seed_seconds * 1000])
        return max(LEASE_FLOOR, LEASE_FACTOR * shard.count * ms / 1000)

    def _live(self, job: Job, shard: Shard, wid: str, at: float, now: float) -> bool:
        """A live lease: the main box's while its job is (it runs what it claims until the
        result), else held by a worker still heard from and within `lease_for` of its lease or
        last renewal. A worker that says it is running the shard renews it (`renew`), so a
        slow live worker keeps its shard; one that never says so loses it at `lease_for`."""
        w = self.workers.get(wid)
        if w is None:
            return False
        if w.kind == "main":
            return True
        return self._fresh(w, now) and now - at <= self.lease_for(job, shard)

    def _leased(self, job: Job, shard: Shard, now: float) -> bool:
        return any(self._live(job, shard, wid, at, now) for wid, at in shard.leases.items())

    def _active(self, now: float) -> list[Job]:
        """Open jobs, oldest first; one whose main box went quiet is dropped (`farm-seeds` killed)."""
        for job in [j for j in self.jobs.values() if now - j.seen > max(self._patience, 3 * self.fresh_seconds)]:
            del self.jobs[job.id]
        return sorted((j for j in self.jobs.values() if not j.complete and now - j.seen <= self.fresh_seconds),
                      key=lambda j: j.created)

    def poll(self, wid: str, label: str | None = None) -> dict:
        """A worker asks for work (a lease); `label`, its name as it stands now, so a rename
        is in the records of the shards it runs from then on."""
        with self.lock:
            w = self.workers.get(wid)
            if w is None:
                return {"task": "hello"}          # unknown (a broker restart): say hello again
            now = self.clock()
            w.last_seen = now
            if label:
                w.label = str(label)[:80]
            for job in self._active(now):
                k = self.keys[job.key]
                if (w.engine == "wasm" and k.bundle != "ready") or (w.engine == "native" and not job.native):
                    continue
                state, at = w.gates.get(job.key, ("", 0.0))
                if state == "fail" or (state == "pending" and now - at <= self._patience):
                    continue
                if state != "pass":
                    w.gates[job.key] = ("pending", now)
                    return {"task": "gate", "key": job.key, **self._payload(job, w)}
                for s in job.shards:
                    if not s.done and not self._leased(job, s, now):
                        s.leases[wid] = now
                        return {"task": "shard", "job": job.id, "shard": s.id, "key": job.key, "first": s.first,
                                "count": s.count, "args": job.args, **self._payload(job, w)}
            return {"task": "idle", "wait_ms": 500}

    def _payload(self, job: Job, w: Worker) -> dict:
        if w.engine == "wasm":
            return {"bundle": f"bundle/{job.key}/", "module": self.bundle_js, "wasm": self._wasm_file(job.key)}
        return {"native": job.native}

    def _wasm_file(self, key: str) -> str:
        """The bundle's one `.wasm` file, which the page fetches and compiles once for all its
        cores; "" when there is none or several (each core then lets init() fetch its own)."""
        if key not in self._wasm:
            try:
                names = [p.name for p in (self.root / "bundles" / key).iterdir() if p.suffix == ".wasm"]
            except OSError:
                return ""
            self._wasm[key] = names[0] if len(names) == 1 else ""
        return self._wasm[key]

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
                self.gate_fails = [*self.gate_fails[-19:], {**note, "at": self.clock()}]
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
            # Who ran the copy that is kept, beside it: the job's record reads these, so it
            # matches the output even when the broker is gone by the end (162's did not).
            write_who(path, done)
            tmp = path.with_suffix(f".{wid}.tmp")
            tmp.write_bytes(output)
            os.replace(tmp, path)
            s.done = done
            s.leases.clear()
            w.shards += 1
            day = time.strftime("%Y-%m-%d", time.localtime(self.clock()))
            w.day, w.today = day, (w.today if w.day == day else 0) + 1
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
            if state != k.bundle:
                k.since = self.clock()
            k.bundle, k.error = state, error[:300]
            self._wasm.pop(key, None)
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
            free = [s for s in job.shards if not s.done and not self._leased(job, s, now)]
            if not free and rerun:
                free = sorted((s for s in job.shards if not s.done and "main" not in s.leases),
                              key=lambda s: min(s.leases.values(), default=now))
            if not free:
                return {"wait": True}
            s = free[0]
            s.leases["main"] = now
            rerun = bool(s.leases.keys() - {"main"})
            if rerun:
                self.reruns = [t for t in self.reruns if now - t < 3600] + [now]
            return {"shard": s.id, "first": s.first, "count": s.count, "rerun": rerun}

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

    @staticmethod
    def _pairs(running) -> list[tuple[str, int]]:
        out = []
        for pair in running if isinstance(running, list) else []:
            try:
                out.append((str(pair[0]), int(pair[1])))
            except (TypeError, ValueError, IndexError):
                continue
        return out

    def renew(self, wid: str, running) -> None:
        """A worker's check-in names the [job, shard] pairs its cores are running: their
        leases start over, so a slow worker that is still at it never loses one."""
        with self.lock:
            now = self.clock()
            for jid, sid in self._pairs(running):
                job = self.jobs.get(jid)
                if job and 0 <= sid < len(job.shards) and wid in job.shards[sid].leases:
                    job.shards[sid].leases[wid] = now

    def _unwanted(self, running) -> list[list]:
        """Of the [job, shard] pairs a page's cores are running, those nobody needs any more:
        the job is closed (or gone), or another copy of the shard is in."""
        drop = []
        for jid, sid in self._pairs(running):
            job = self.jobs.get(jid)
            if job is None or job.complete or not 0 <= sid < len(job.shards) or job.shards[sid].done:
                drop.append([jid, sid])
        return drop

    def view(self, wid: str, running=None) -> dict:
        """What the worker page shows beside its own measurements, sent with each hello (no
        polling of its own): the current job as counts, this worker's gate, the other
        workers, the main box's native rate and load; and `drop`, the shards in `running`
        (its cores' [job, shard] pairs) it should stop, so a closed job's work ends at once."""
        with self.lock:
            now = self.clock()
            drop = self._unwanted(running)
            me = self.workers.get(wid)
            jobs = self._active(now)
            job = None
            if jobs:
                j = jobs[0]
                job = self._job_view(j, now)
                job |= {"mine": sum(1 for s in j.shards if s.done and s.done["worker"] == wid), "jobs": len(jobs)}
            gate = None
            if me and me.gates:
                key = job["key"] if job and job["key"] in me.gates else max(me.gates, key=lambda k: me.gates[k][1])
                k = self.keys.get(key)
                wasm_ref = me.engine == "wasm" and bool(k and k.wasm)
                gate = {"key": key, "state": me.gates[key][0], "reference": "wasm" if wasm_ref else "native",
                        "value": (k.reference(me.engine) if k else "")[:16]}
            rates = jobs[0].rates if jobs else {}
            out_by = self._out_by(jobs, now)
            others = [{"id": w.id, "kind": w.kind, "cores": w.cores, "label": w.label, "shards": w.shards,
                       "ms_seed": round(rates[w.id], 1) if w.id in rates else None,
                       **self._state(w, now, jobs, out_by.get(w.id, 0))}
                      for w in self.workers.values() if w.kind != "main" and w.id != wid and self._fresh(w, now)]
            main = self.workers.get("main")
            return {"now": now, "job": job, "gate": gate, "bundle": job and job["bundle"], "workers": others,
                    "me": self._state(me, now, jobs, out_by.get(wid, 0)) if me else {"state": "offline"},
                    "main": {"ms_seed": round(rates["main"], 1) if "main" in rates else None,
                             **(self._state(main, now, jobs, out_by.get("main", 0)) if main else {"state": "idle"})},
                    "items": list(self.items), "load": self.load, "drop": drop}

    # ------------------------------------------------------------ one state model
    def _out_by(self, jobs: list[Job], now: float) -> dict[str, int]:
        """Shards each worker holds a live lease on, over the open jobs."""
        out: dict[str, int] = {}
        for job in jobs:
            for s in job.shards:
                if not s.done:
                    for wid, at in s.leases.items():
                        if self._live(job, s, wid, at, now):
                            out[wid] = out.get(wid, 0) + 1
        return out

    def _job_view(self, j: Job, now: float) -> dict:
        """An open job as counts, with its bundle's state (the wasm side's preparation)."""
        done = [s for s in j.shards if s.done]
        out = [s for s in j.shards if not s.done and self._leased(j, s, now)]
        seeds, seeds_done = sum(s.count for s in j.shards), sum(s.count for s in done)
        elapsed = max(0.001, now - j.created)
        eta = (seeds - seeds_done) * elapsed / seeds_done if seeds_done else None
        k = self.keys.get(j.key)
        return {"id": j.id, "item": j.item, "key": j.key, "shards": len(j.shards), "done": len(done),
                "out": len(out), "pending": len(j.shards) - len(done) - len(out), "seeds": seeds,
                "seeds_done": seeds_done, "elapsed": round(elapsed, 1), "started": j.created,
                "eta": None if eta is None else round(eta, 1),
                "bundle": {"state": k.bundle, "error": k.error, "since": k.since or None} if k else None}

    def state_of(self, wid: str) -> dict:
        """One worker's state (`_state`), for a test or a caller outside the views."""
        with self.lock:
            now = self.clock()
            jobs = self._active(now)
            w = self.workers.get(wid)
            return self._state(w, now, jobs, self._out_by(jobs, now).get(wid, 0)) if w else {"state": "offline"}

    def _state(self, w: Worker, now: float, jobs: list[Job], out: int) -> dict:
        """The one state every view shows for a worker (STATES): the page and its tab title
        (`view`'s `me`), the app's chip and Workers tab (`panel`). `{state, detail, key,
        since, out}`: `detail` is the words after the state, `since` when it began (broker
        clock), `key` the commit it concerns. The main box is working while it holds a shard,
        else idle. A worker: offline unheard; draining when it says so; working while it holds
        a shard; else what its first open job it can serve waits on (the bundle building or
        failed, its gate pending or failed); else what it says (downloading or compiling the
        bundle, a core's error); else idle (paused, when it is)."""
        if w.kind == "main":
            return {"state": "working", "out": out, "detail": f"{out} shard{'s' * (out != 1)}"} if out \
                else {"state": "idle", "detail": ""}
        if not self._fresh(w, now):
            return {"state": "offline", "detail": ""}
        rep = w.report if now - w.report.get("at", 0) <= self.fresh_seconds else {}
        if rep.get("state") == "draining":
            return {"state": "draining", "out": out, "since": rep["since"],
                    "detail": "finishing its shards, then reloading for a new page version"}
        if out:
            return {"state": "working", "out": out, "detail": f"{out} shard{'s' * (out != 1)}"}
        for job in jobs:
            k = self.keys[job.key]
            if w.engine == "native" and not job.native:
                continue
            gate = w.gates.get(job.key, ("", 0.0))
            base = {"key": job.key}
            if gate[0] == "fail":
                return {**base, "state": "error", "since": gate[1],
                        "detail": f"failed the fingerprint gate for {_short(job.key)}"}
            if w.engine == "wasm":
                if k.bundle == "failed":
                    return {**base, "state": "error", "since": k.since,
                            "detail": f"the wasm build failed for {_short(job.key)}" + (f": {k.error}" if k.error else "")}
                if k.bundle == "building" and now - k.since > BUILD_STUCK_SECONDS:
                    return {**base, "state": "error", "since": k.since,
                            "detail": f"the wasm build for {_short(job.key)} has run over {BUILD_STUCK_SECONDS // 60} min"}
                if k.bundle in ("none", "building"):
                    return {**base, "state": "preparing", "since": k.since or job.created, "step": "building",
                            "detail": f"the broker is building the bundle for {_short(job.key)}"
                            if k.bundle == "building" else f"waiting for the bundle build for {_short(job.key)}"}
            if gate[0] == "pending":
                return {**base, "state": "preparing", "since": gate[1], "step": "gating",
                        "detail": f"checking the fingerprint for {_short(job.key)}"}
            if rep.get("state") == "preparing":
                key = rep.get("key", job.key)
                return {"key": key, "state": "preparing", "since": rep["since"], "step": rep["step"],
                        "detail": f"{STEPS[rep['step']]} for {_short(key)}"}
            break
        if rep.get("state") == "error":
            return {"state": "error", "since": rep["since"], "key": rep.get("key"), "detail": rep.get("error", "")}
        if rep.get("state") == "preparing":          # loading ahead of any job (a gate's bundle)
            key = rep.get("key", "")
            return {"key": key, "state": "preparing", "since": rep["since"], "step": rep["step"],
                    "detail": f"{STEPS[rep['step']]}" + (f" for {_short(key)}" if key else "")}
        return {"state": "idle", "detail": "paused" if rep.get("state") == "paused" or not w.cores else ""}

    def reference_kind(self, wid: str, key: str) -> str:
        """Which reference this worker is gated against for `key`: wasm or native."""
        with self.lock:
            w, k = self.workers.get(wid), self.keys.get(key)
            return "wasm" if w and k and w.engine == "wasm" and k.wasm else "native"

    def panel(self, main_slots: int = 1) -> dict:
        """What the app's chip and Workers tab show (`status.json`, written by the broker's
        loop): the current job as counts, one row per worker (the main box first), and the
        recent trouble: gate failures and shards the main box re-ran."""
        with self.lock:
            now = self.clock()
            jobs = self._active(now)
            j = jobs[0] if jobs else None
            out_by = self._out_by(jobs, now)
            job = self._job_view(j, now) if j else None
            today = time.strftime("%Y-%m-%d", time.localtime(now))
            main = self.workers.get("main") or Worker("main", "main", label="main box")
            rows = []
            for w in [main, *sorted((w for w in self.workers.values() if w.kind != "main"), key=lambda w: w.first_seen)]:
                key = j.key if j and j.key in w.gates else max(w.gates, key=lambda k: w.gates[k][1], default=None)
                gate = w.gates[key][0] if key else ""
                fresh = w.kind == "main" or self._fresh(w, now)
                n = out_by.get(w.id, 0)
                rows.append({"id": w.id, "kind": w.kind, "engine": w.engine, "label": w.label or w.id,
                             "cores": w.cores, "out": n, "gate": gate, "fresh": fresh,
                             # the main box is the broker's own machine: never "seen", always local
                             "last_seen": None if w.kind == "main" else w.last_seen or None,
                             "today": w.today if w.day == today else 0,
                             **self._state(w, now, jobs, n)})
            return {"now": now, "job": job, "workers": rows, "items": list(self.items),
                    "gate_fails": [dict(g) for g in self.gate_fails], "reruns": list(self.reruns)}

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

    # ------------------------------------------------------------ across a broker restart
    def save(self, path: Path) -> None:
        """Everything above as JSON (the broker's loop, every 2 s, and its last act on a
        handover), so a new broker carries on: jobs, shards done and leased, workers and
        their gates. A worker mid-shard posts its result to the new broker and it counts."""
        with self.lock:
            data = {"at": self.clock(), "n": self._n, "gate_fails": self.gate_fails, "reruns": self.reruns,
                    "workers": [asdict(w) for w in self.workers.values()],
                    "keys": {k: asdict(v) for k, v in self.keys.items()},
                    "jobs": [asdict(j) | {"folder": str(j.folder)} for j in self.jobs.values()]}
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, path)

    def restore(self, path: Path) -> bool:
        """Take up a saved board (`save`); False, and nothing changed, if there is none or it
        doesn't read. Jobs the main box stopped calling drop out as ever (`_active`). Failed
        gates don't carry over: a new broker (a code change, a fix) gates those workers again."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            workers = {w["id"]: Worker(**{**w, "gates": {k: tuple(v) for k, v in w["gates"].items()}})
                       for w in data["workers"]}
            keys = {k: Key(**v) for k, v in data["keys"].items()}
            jobs = {j["id"]: Job(**{**j, "folder": Path(j["folder"]), "shards": [Shard(**s) for s in j["shards"]]})
                    for j in data["jobs"] if j["key"] in keys}
            n = int(data["n"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return False
        for w in workers.values():
            _drop_failed(w)
        with self.lock:
            self.workers, self.keys, self.jobs, self._n = workers, keys, jobs, n
            self.gate_fails, self.reruns = list(data.get("gate_fails") or []), list(data.get("reruns") or [])
        return True

    @staticmethod
    def _append(path: Path, text: str, cap: int | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if cap is not None and path.exists() and path.stat().st_size > cap:
            return
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(text.rstrip("\n") + "\n")
