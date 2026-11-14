"""The broker's one state model (`Board`'s views, split from `board.py` in 116): what every
view shows for a worker (`_state`: the page, the app's chip and Workers tab, `remote-diag.md`),
the app's panel and the inbox's *Now* line. A mixin: `Board` holds the data and the lock."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from . import cores as policy, diag

if TYPE_CHECKING:
    from .board import Job, Worker

BUILD_STUCK_SECONDS = 20 * 60   # a bundle building this long is shown as an error (client.BUILD_STALE_SECONDS)
GATE_SECONDS = diag.DEADLINES["gate"]   # a gate unanswered this long is late: handed out again, once
GATE_TRIES = 2                  # the second late gate fails (the worker's error, until it reloads)
STATES = ("offline", "idle", "preparing", "working", "draining", "error")   # a worker's state (`Board.state_of`)
STEPS = {"downloading": "downloading the bundle", "compiling": "compiling the bundle",
         "starting": "starting the bundle"}   # what a page may say it is preparing (its hello's `report`)


def _short(key: str) -> str:
    return key[:7] + (" +changes" if "-" in key else "")


def gate_why(got: str, expected: str) -> str:
    """Why a gate failed, in words: the page's `load failed: <error>` as it is (a Wasm
    out-of-memory is a load failure, not a wrong answer: aicomplete 2026-11-13), else the
    two fingerprints."""
    got = " ".join(got.split())
    if got.startswith("load failed"):
        return got[:200]
    return f"wrong fingerprint: got {got[:24] or 'nothing'}, expected {expected[:24]}"


class Views:
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
                "bundle": {"state": k.bundle, "error": k.error, "since": k.since or None, "note": k.note} if k else None}

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
                    "detail": "finishing its shards, then reloading "
                              + ("to recover from a Wasm out-of-memory" if rep.get("why") == "memory" else "for a new page version")}
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
                        "detail": f"failed the fingerprint gate for {_short(job.key)}"
                        + (f": {w.why[job.key]}" if job.key in w.why else "")}
            if w.engine == "wasm":
                if k.bundle == "referencing":       # an error once a try passed its deadline (retried once)
                    return {**base, "state": "error" if k.error else "preparing", "since": k.since,
                            "step": "referencing",
                            "detail": f"the Node wasm reference for {_short(job.key)}: {k.error}" if k.error
                            else f"the broker is computing the wasm reference for {_short(job.key)}"}
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
            if gate[0] == "pending" and now - gate[1] > GATE_SECONDS:
                return {**base, "state": "error", "since": gate[1] + GATE_SECONDS, "step": "gating",
                        "detail": f"the fingerprint check for {_short(job.key)} passed its {GATE_SECONDS:.0f} s "
                                  f"deadline (try {w.tries.get(job.key, 1)} of {GATE_TRIES})"}
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
            w = self.workers.get(wid)
            return self._ref_kind(w, key) if w else "native"

    def _ref_kind(self, w: Worker, key: str) -> str:
        k = self.keys.get(key)
        return "wasm" if k and w.engine == "wasm" and k.wasm else "native"

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
            from .board import Worker     # board imports this module: at call time only
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
                             **self._plan_row(w),
                             **self._state(w, now, jobs, n)})
            return {"now": now, "job": job, "workers": rows, "items": list(self.items),
                    "gate_fails": [dict(g) for g in self.gate_fails], "reruns": list(self.reruns)}

    def _plan_row(self, w: Worker) -> dict:
        """A browser's chosen count, its cap and the last change's reason (`cores.py`)."""
        p = self.plans.get(w.plan_name) if w.hw else None
        return {"chosen": min(p.cores, w.hw), "cap": w.cap or None, "hw": w.hw, "why": p.why, "why_at": p.at,
                "ceiling": p.ceiling or None, "ceiling_until": p.ceiling_at + policy.CEILING_SECONDS if p.ceiling else None,
                "lifted": p.lifted or None, "lifted_at": p.lifted_at or None} if p else {}

    def now_line(self) -> str | None:
        """The inbox's *Now* line, or None while no worker is connected."""
        live = self.summary()["workers"]
        if not live:
            return None
        kinds: dict[str, int] = {}
        for w in live:
            kinds[w["kind"]] = kinds.get(w["kind"], 0) + 1
        failed = [w for w in live if "fail" in w["gates"].values()]
        # 116: the reason, not just the count (aicomplete's laptop idled on a Wasm out-of-memory
        # while the line said only "failed the fingerprint gate").
        why = "; ".join(f"{w['label'] or w['id']}: {next(iter(w['why'].values()))[:120]}" for w in failed if w["why"])
        return (f"remote workers: {len(live)} connected ({', '.join(f'{n} {k}' for k, n in sorted(kinds.items()))}), "
                f"{sum(w['cores'] for w in live)} cores"
                + (f"; {len(failed)} failed the fingerprint gate" + (f" ({why})" if why else "") if failed else ""))
