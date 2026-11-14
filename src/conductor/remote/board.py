"""The broker's state: workers, jobs, shards, leases and fingerprint gates. Pure and
locked; the HTTP layer (`broker.py`) and the tests drive it with an explicit clock.

Placement lives here and in `client.py`: a worker pulls (`poll`); the main box claims
(`claim`), at most `main_slots` shards at once over every open job, and once nothing is left
unleased it re-runs a shard still out on a worker, the first copy back winning (`result`).
A free shard goes to the worker expected to finish it soonest (`placement.py`). Outputs are
bytes, never re-encoded."""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import cores as policy, diag, placement
from .board_views import (BUILD_STUCK_SECONDS, GATE_SECONDS, GATE_TRIES, STATES, STEPS,  # noqa: F401 - callers' names
                          Views, _short, gate_why)

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
KINDS = {"browser": "wasm", "local-remote": "native", "main": "native"}   # worker kind -> engine
LOG_CHARS = 64_000              # a worker's log, per result
LOG_FILE_CAP = 2_000_000        # a worker's log file per job stops growing here
WORKER_FORGET_SECONDS = 3600.0
LEASE_FLOOR = 60.0              # a derived lease is never shorter than this
LEASE_FACTOR = 4.0              # a derived lease: this many times a shard's expected time
POLL_FRESH = 5.0                # a worker's free cores not polling for this long aren't waited for (placement)
OVERDUE = 1.5                   # a remote shard out this many times its worker's expected time: a main slot copies it (103)


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
    tries: dict[str, int] = field(default_factory=dict)   # key -> gates handed out for it (GATE_TRIES)
    why: dict[str, str] = field(default_factory=dict)     # key -> why its gate failed (`gate_why`)
    hw: int = 0                 # a browser's hardware cores (its hello); 0: an old page, no plan (`cores.py`)
    cap: int = 0                # its slider's cap; 0: none
    polled: float = 0.0         # its last poll (a free core asking for work)

    @property
    def plan_name(self) -> str:
        return self.label or self.id

    @property
    def limit(self) -> int:
        return min(self.hw, self.cap or self.hw)

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
    tags: dict[str, int] = field(default_factory=dict)      # "shard:worker" -> a browser's cores when it was handed out
    failed: list[str] = field(default_factory=list)         # workers that failed a shard here: no more of its shards

    @property
    def complete(self) -> bool:
        return all(s.done for s in self.shards)


@dataclass
class Key:
    fingerprint: str             # the native reference: the `fingerprint` command on the main box
    bundle: str = "none"         # none | building | referencing (the Node wasm reference) | ready | failed
    error: str = ""              # a failed build's line; while referencing, a try past its deadline
    wasm: str = ""               # the wasm reference: the bundle's own fingerprint() on the main box
    since: float = 0.0           # when `bundle` last changed (a build's elapsed time)
    note: str = ""               # ready without a wasm reference: why browsers gate against the native one

    def reference(self, engine: str) -> str:
        """What a worker of this engine must reproduce (without a wasm reference, the native one)."""
        return self.wasm if engine == "wasm" and self.wasm else self.fingerprint


def _drop_failed(w: Worker, pending: bool = False) -> None:
    """Forget the keys this worker failed (and, with `pending`, those it never answered: a
    reloaded page's cores are new ones), so its next poll gates them again."""
    for key in [k for k, (state, _) in w.gates.items() if state == "fail" or (pending and state == "pending")]:
        del w.gates[key]
        w.tries.pop(key, None)
        w.why.pop(key, None)


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
    if report["state"] == "draining" and report.get("why") == "memory":
        out["why"] = "memory"           # 116: reloading after a Wasm out-of-memory, not for a new page
    return out


def who_path(out: Path) -> Path:
    return out.with_suffix(".who.json")


def write_who(out: Path, done: dict) -> None:
    """Who ran the kept copy of a shard (`shards/<first>.who.json` beside `<first>.out`)."""
    tmp = out.with_suffix(f".who.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(done) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, who_path(out))


class Board(Views):
    def __init__(self, root: Path, lease_seconds: float | None = None, fresh_seconds: float = 10.0,
                 bundle_js: str = "bundle.js", native_argv: list[str] | None = None, clock=time.time,
                 seed_seconds: float = 0.05, main_slots: int | None = None, start_cores: int = policy.START):
        """`lease_seconds` None: each lease is sized to its shard (`lease_for`). `main_slots`:
        the main box's shards at once over every job (None: no cap, one slot to place by).
        `start_cores`: a new browser name's count; a known name keeps its learned plan (103)."""
        self.root = Path(root)
        self.main_slots = main_slots if main_slots and main_slots > 0 else None
        self.ratios: dict[str, float] = {}   # worker id -> its ms a seed over the main box's, last measured together
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
        self._handed: set[tuple[str, str]] = set()   # (job, worker): its first shard is in the steps log
        self.plans: dict[str, policy.Plan] = {}       # a browser's name -> its core count (`cores.py`)
        self.start_cores = max(1, start_cores)
        self._n = 0
        self._notes = 0

    # ------------------------------------------------------------ workers
    def hello(self, wid: str, kind: str, cores: int = 1, label: str = "", fresh: bool = False,
              report=None, hw=None, cap=None) -> dict:
        """`fresh`: the first hello of a page load or a worker start, which re-gates every key
        this worker failed (a fix needs no new tab or worker id). `report`: what the worker
        says it is doing that the broker can't see (`_report`), for `state_of`. `hw` (a
        browser's hardware cores) and `cap` (its slider, None for none): the reply's `cores`
        is the count the broker chose for it under its name (`cores.py`)."""
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
            gap = min(now - w.last_seen, self.fresh_seconds) if w.last_seen else 0.0   # connected time since
            w.cores, w.label, w.last_seen = max(0, min(int(cores), 1024)), str(label)[:80], now
            w.report = _report(report, now)
            if fresh:
                _drop_failed(w, pending=True)
            reply = {"ok": True, "poll_ms": 500}
            if kind == "browser" and hw is not None:
                w.hw = max(1, min(int(hw), 1024))
                w.cap = max(0, min(int(cap or 0), w.hw))
                p = self.plans.get(w.plan_name)
                if p is None:
                    p = self.plans[w.plan_name] = self._new_plan(now)
                    policy.prune(self.plans)
                if p.cores > w.hw:
                    policy.change(p, w.hw, f"this machine has {w.hw} cores", now)
                for why in (policy.connected(p, gap, now), policy.lift(p, now)):
                    self._note_cores(w, why, now)
                reply |= {"cores": p.cores, "why": p.why}
            return reply

    def _fresh(self, w: Worker, now: float) -> bool:
        return now - w.last_seen <= self.fresh_seconds

    def live(self, key: str | None = None) -> list[Worker]:
        """Workers heard from recently, other than the main box; with `key`, not failed for it."""
        now = self.clock()
        return [w for w in self.workers.values() if w.kind != "main" and self._fresh(w, now)
                and not (key and w.gates.get(key, ("",))[0] == "fail")]

    @property
    def _patience(self) -> float:
        """How long a job's main box is waited for. A gate has its own deadline (GATE_SECONDS):
        aicomplete's `lease_seconds = 600` held a gate no core answered for 10 minutes (98)."""
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
            w.last_seen = w.polled = now
            if label:
                w.label = str(label)[:80]
            jobs = self._active(now)
            for job in jobs:
                k = self.keys[job.key]
                if (w.engine == "wasm" and k.bundle != "ready") or (w.engine == "native" and not job.native):
                    continue
                state, at = w.gates.get(job.key, ("", 0.0))
                if state == "pending" and now - at > GATE_SECONDS:
                    state = self._late_gate(w, job.key, at, now)
                if state in ("fail", "pending"):
                    continue
                if state != "pass":
                    w.gates[job.key] = ("pending", now)
                    n = w.tries[job.key] = w.tries.get(job.key, 0) + 1
                    diag.step(self.root, "gate", "start", f"gate-{wid}-{job.key}-{n}", now, worker=wid, key=job.key,
                              job=job.id, reference=self._ref_kind(w, job.key), **{"try": n})
                    return {"task": "gate", "key": job.key, **self._payload(job, w)}
                free = [s for s in job.shards if not s.done and not self._leased(job, s, now)]
                if not free or not self._share(job, wid, len(free), jobs, now):
                    continue                     # a faster worker is expected to finish what is free sooner
                s = free[0]
                s.leases[wid] = now
                if w.hw:
                    job.tags[f"{s.id}:{wid}"] = w.cores
                if (job.id, wid) not in self._handed:
                    self._handed.add((job.id, wid))
                    diag.step(self.root, "handout", "note", f"handout-{job.id}-{wid}", now, worker=wid,
                              key=job.key, job=job.id, result=f"first shard of {job.id} (shard {s.id})")
                return {"task": "shard", "job": job.id, "shard": s.id, "key": job.key, "first": s.first,
                        "count": s.count, "args": job.args, **self._payload(job, w)}
            return {"task": "idle", "wait_ms": 500}

    # ------------------------------------------------------------ placement (`placement.py`)
    def _ms_seed(self, job: Job, wid: str) -> float:
        """A worker's expected ms a seed a core in this job: measured here, else the main box's
        rate here times the ratio last measured between them (1 for a worker never measured
        beside it). The main box's rate before it has one here: from a worker measured here and
        its ratio, else the mean of what is measured here, else `seed_seconds`."""
        if wid in job.rates:
            return job.rates[wid]
        base = job.rates.get("main")
        if base is None:
            known = [r / self.ratios[x] for x, r in job.rates.items() if self.ratios.get(x, 0) > 0]
            rates = list(job.rates.values())
            base = known[0] if known else sum(rates) / len(rates) if rates else self.seed_seconds * 1000
        return base * (1.0 if wid == "main" else self.ratios.get(wid, 1.0))

    def _ends(self, jobs: list[Job], now: float) -> dict[str, list[float]]:
        """Each worker's busy slots over the open jobs: when each is expected free."""
        out: dict[str, list[float]] = {}
        for j in jobs:
            for s in j.shards:
                if not s.done:
                    for wid, at in s.leases.items():
                        if self._live(j, s, wid, at, now):
                            out.setdefault(wid, []).append(
                                placement.busy_until(at, s.count * self._ms_seed(j, wid), now))
        return out

    def _serves(self, w: Worker, job: Job, now: float) -> bool:
        """A remote worker that can take this job's shards now: heard from, gated, with cores,
        not draining, paused or in error."""
        if w.kind == "main" or w.cores < 1 or not self._fresh(w, now) or w.gates.get(job.key, ("",))[0] != "pass":
            return False
        if (w.engine == "wasm" and self.keys[job.key].bundle != "ready") or (w.engine == "native" and not job.native):
            return False
        rep = w.report if now - w.report.get("at", 0) <= self.fresh_seconds else {}
        return rep.get("state") not in ("draining", "paused", "error")

    def _share(self, job: Job, wid: str, free: int, jobs: list[Job], now: float) -> int:
        """How many of the job's `free` shards the deal by expected finish gives to `wid`'s
        free slots (`placement.deal`): the main box's `main_slots` over every job, each
        serving worker's cores (its free ones only while it polls). A worker that failed a
        shard of this job gets none of the rest."""
        if wid in job.failed:
            return 0
        ends = self._ends(jobs, now)
        n = next(s.count for s in job.shards if not s.done and not self._leased(job, s, now))
        main_busy = ends.get("main", [])
        slots = {"main": placement.slots_of(main_busy, self.main_slots or len(main_busy) + 1, now, wid == "main")}
        for w in self.workers.values():
            if w.id == wid or (w.id not in job.failed and self._serves(w, job, now)):
                busy = ends.get(w.id, [])
                count = w.cores if w.id == wid or now - w.polled <= POLL_FRESH else len(busy)
                slots[w.id] = placement.slots_of(busy, count, now, w.id == wid)
        ms = {x: n * self._ms_seed(job, x) for x in slots}
        return placement.deal(free, slots, ms, now, asking=wid).get(wid, 0)

    def _main_out(self, jobs: list[Job]) -> int:
        """Shards the main box is running over every open job (`main_slots` caps it)."""
        return sum(1 for j in jobs for s in j.shards if not s.done and "main" in s.leases)

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

    def _late_gate(self, w: Worker, key: str, at: float, now: float) -> str:
        """A gate past its deadline with no answer: its step ends `timeout`. The first is handed
        out again (the page replaces a core stuck that long); the second fails."""
        n = w.tries.get(key, 1)
        diag.step(self.root, "gate", "end", f"gate-{w.id}-{key}-{n}", now, worker=w.id, key=key, result="timeout",
                  detail=f"no answer in {now - at:.0f} s")
        if n < GATE_TRIES:
            del w.gates[key]
            return ""
        w.why[key] = f"no answer to the fingerprint check in {GATE_SECONDS:.0f} s, {n} times"
        self._fail_gate(w, key, w.why[key])
        return "fail"

    def _fail_gate(self, w: Worker, key: str, got: str) -> None:
        k = self.keys[key]
        w.gates[key] = ("fail", self.clock())
        note = {"worker": w.id, "kind": w.kind, "label": w.label, "key": key, "reference": self._ref_kind(w, key),
                "expected": k.reference(w.engine), "got": got[:200]}
        for job in self.jobs.values():
            if job.key == key:
                job.gate_failures.append(note)
        self._append(self.root / "gates.log", json.dumps(note))
        self.gate_fails = [*self.gate_fails[-19:], {**note, "at": self.clock()}]

    def gate(self, wid: str, key: str, value: str) -> bool:
        with self.lock:
            w, k = self.workers.get(wid), self.keys.get(key)
            if w is None or k is None:
                return False
            ok = str(value).strip() == k.reference(w.engine)
            diag.step(self.root, "gate", "end", f"gate-{wid}-{key}-{w.tries.get(key, 1)}", self.clock(), worker=wid, key=key,
                      result="ok" if ok else "failed", detail="" if ok else f"got {str(value)[:120]}")
            if ok:
                w.gates[key] = ("pass", self.clock())
                w.why.pop(key, None)
            else:
                w.why[key] = gate_why(str(value), k.reference(w.engine))
                self._fail_gate(w, key, str(value))
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
                if "main" in job.rates:            # across jobs only as a ratio (placement)
                    for x, r in job.rates.items():
                        if x != "main":
                            self.ratios[x] = r / job.rates["main"]
            self._plan_result(job, s, w, ok, ms)
            if not ok and w.kind != "main" and wid not in job.failed:
                job.failed.append(wid)             # 167: a laptop failed every shard of a spec its bundle lacked
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

    def _plan_result(self, job: Job, s: Shard, w: Worker, ok: bool, ms: float) -> None:
        """A result, for the core counts (`cores.py`): a browser's own shard is a sample and
        won or lost; the main box's copy landing first is a loss for each browser holding it."""
        now = self.clock()
        if w.kind == "main":
            if ok and not s.done:
                for wid in [x for x in s.leases if x != "main"]:
                    other = self.workers.get(wid)
                    n = job.tags.pop(f"{s.id}:{wid}", None)
                    if other and other.hw and n is not None:
                        p = self._plan(other)
                        policy.outcome(p, job.id, n, won=False)
                        self._decide(other, p, now)
            return
        n = job.tags.pop(f"{s.id}:{w.id}", None)
        if not w.hw or n is None:
            return
        p = self._plan(w)
        if ok and ms > 0:
            policy.sample(p, job.id, n, ms / s.count)
        if ok:
            policy.outcome(p, job.id, n, won=not s.done)
        self._decide(w, p, now)

    def _plan(self, w: Worker) -> policy.Plan:
        return self.plans.setdefault(w.plan_name, self._new_plan(self.clock()))

    def _new_plan(self, now: float) -> policy.Plan:
        return policy.Plan(cores=self.start_cores, why=f"new worker: starts at {self.start_cores}", at=now)

    def _decide(self, w: Worker, p: policy.Plan, now: float) -> None:
        self._note_cores(w, policy.lift(p, now), now)
        self._note_cores(w, policy.decide(p, w.limit, now), now)

    def _note_cores(self, w: Worker, why: str | None, now: float) -> None:
        if why:                                    # a lift and a raise can share an instant: numbered
            self._notes += 1
            diag.step(self.root, "cores", "note", f"cores-{w.id}-{now:.3f}-{self._notes}", now, worker=w.id, result=why)

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

    def set_bundle(self, key: str, state: str, error: str = "", wasm: str = "", note: str = "") -> None:
        """A bundle's state; once ready, with its own fingerprint (the wasm reference), or a
        `note` saying why there is none (browsers then gate against the native one). A changed
        wasm reference unsets every browser's gate for the key."""
        with self.lock:
            k = self.keys.get(key)
            if k is None:
                return
            if state != k.bundle:
                k.since = self.clock()
            k.bundle, k.error, k.note = state, error[:300], note[:300]
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
            jobs = self._active(now)
            if self.main_slots and self._main_out(jobs) >= self.main_slots:
                return {"wait": True}              # every main slot is busy, in this job or another
            free = [s for s in job.shards if not s.done and not self._leased(job, s, now)]
            if free and not self._share(job, "main", len(free), jobs, now):
                late = self._overdue(job, now) if rerun else []
                if not late:
                    return {"wait": True}          # a worker expected to finish them sooner takes them
                free = late                        # a free slot here copies a lagging remote shard
            elif not free and rerun:
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

    def _overdue(self, job: Job, now: float) -> list[Shard]:
        """Shards out only on remote workers past OVERDUE times their worker's expected time,
        the most overdue first. A read waits for its slowest shard: aicomplete's laptop ran
        2-seed shards of one job 7 to 11 min (j1794328936-30) while free shards still went
        its way, so the main box's tail rerun came late (103)."""
        late = []
        for s in job.shards:
            if s.done or "main" in s.leases or not s.leases:
                continue
            over = max((now - at) / max(s.count * self._ms_seed(job, wid) / 1000, 1e-3) for wid, at in s.leases.items())
            if over > OVERDUE:
                late.append((-over, s.id, s))
        return [s for _, _, s in sorted(late)]

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
                                 "gates": {k: v[0] for k, v in w.gates.items()},
                                 "why": {k: w.why[k] for k, v in w.gates.items() if v[0] == "fail" and k in w.why}}
                                for w in live],
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
                if gate["state"] == "fail" and key in me.why:
                    gate["why"] = me.why[key]
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

    # ------------------------------------------------------------ across a broker restart
    def save(self, path: Path) -> None:
        """Everything above as JSON (the broker's loop, every 2 s, and its last act on a
        handover), so a new broker carries on: jobs, shards done and leased, workers and
        their gates. A worker mid-shard posts its result to the new broker and it counts."""
        with self.lock:
            data = {"at": self.clock(), "n": self._n, "gate_fails": self.gate_fails, "reruns": self.reruns,
                    "plans": {k: asdict(v) for k, v in self.plans.items()}, "ratios": self.ratios,
                    "workers": [asdict(w) for w in self.workers.values()],
                    "keys": {k: asdict(v) for k, v in self.keys.items()},
                    "jobs": [asdict(j) | {"folder": str(j.folder)} for j in self.jobs.values()]}
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, path)

    def restore(self, path: Path) -> bool:
        """Take up a saved board (`save`), the core counts learned by name with it; False, and nothing changed, if there is none or it
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
            plans = {k: policy.Plan(**v) for k, v in (data.get("plans") or {}).items()}
            ratios = {str(k): float(v) for k, v in (data.get("ratios") or {}).items() if float(v) > 0}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return False
        for w in workers.values():
            _drop_failed(w)
        with self.lock:
            self.workers, self.keys, self.jobs, self._n, self.plans = workers, keys, jobs, n, plans
            self.ratios = ratios          # a known name keeps its plan whatever `start_cores` is now (103)
            self.gate_fails, self.reruns = list(data.get("gate_fails") or []), list(data.get("reruns") or [])
        return True

    @staticmethod
    def _append(path: Path, text: str, cap: int | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if cap is not None and path.exists() and path.stat().st_size > cap:
            return
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(text.rstrip("\n") + "\n")
