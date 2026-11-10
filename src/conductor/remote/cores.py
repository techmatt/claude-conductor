"""How many cores a browser worker runs: chosen by the broker from what the worker delivers
(`docs/remote-cores.md`). Pure: `Board` feeds it samples and outcomes and applies its answer.

A plan is kept per worker *name*, so a reload, a restart or a new tab under the same name
starts at the learned count. Within one job, the per-core rate at a count is compared with
the best rate at any lower count measured in that job (another job's arguments may cost more
a seed, so jobs are never compared). Not the count just below: 99's sandbox walk crept from
2 to 9 cores on a tab pinned to 4 CPUs, each step within HOLD of the last. Aicomplete 163:
a laptop tab on 7 cores won 1 shard of 7; on 2 it ran 1.22 seeds a minute a core and won
its shards.

- A new name starts at the project's `start_cores` (START unless set; 4 since 101, Matt's call:
  a tab starts useful at once). A saved plan below it is reset to it once (`restart`).
- Raise by UP once `need` samples at the count are in, the rate held (within HOLD of the best
  lower count, or none measured yet in this job) and no shard was lost at it.
- Back off when the rate fell past HOLD, or LOST of the last WINDOW outcomes at this count
  were lost to the main box: to the count below measured in this job, else half (rounded
  up). The count backed off from is not tried again for CEILING_SECONDS; then the ceiling
  lifts (`lift`, a note in the steps log and `remote-diag.md`).
- A shard counts only while its count is the plan's: one handed out before a raise and
  finishing after it ran beside the new cores (99: that dragged the count below's rate down).
- A plan with no sample in STALE_SECONDS of connected time starts the climb again: its
  ceiling, samples and outcomes go (`connected`). Aicomplete 168: a laptop backed off 3 -> 2
  at 4:59am took no sample for 3 hours (a drained core never said it stopped, so the page
  went on reporting 3 cores and every shard was tagged at a count the plan had left).
- A change reaches the page with its next hello; cores leave only between shards.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

START = 4                 # a new worker's count unless `[remote] start_cores` sets one
UP = 1                    # a raise
HOLD = 1.20               # per-core ms a seed within this factor of the best lower count's: the rate held
WINDOW = 4                # outcomes (won / lost) looked at
LOST = 2                  # lost in the window (and at least half of it) backs off
CEILING_SECONDS = 3600.0  # a count backed off from is not tried again for this long
STALE_SECONDS = 3600.0    # connected this long with no sample: the climb starts again
KEEP = 100                # names kept


@dataclass
class Plan:
    cores: int = START
    why: str = ""                 # the last change and its reason, e.g. "7 → 4: shards lost to the main box 3 of 4"
    at: float = 0.0               # when it changed
    ceiling: int = 0              # a count backed off from (not tried again until ceiling_at + CEILING_SECONDS)
    ceiling_at: float = 0.0
    job: str = ""                 # the job the samples are from
    samples: dict[str, list[float]] = field(default_factory=dict)   # str(count) -> per-core ms a seed, in `job`
    outcomes: list[str] = field(default_factory=list)              # won | lost, at this count, in `job`
    quiet: float = 0.0            # connected seconds since the last sample (STALE_SECONDS)
    lifted: int = 0               # the last ceiling that lifted, and when (remote-diag.md)
    lifted_at: float = 0.0


def need(n: int) -> int:
    """Samples at a count before its rate decides anything: a round of its cores, 2 to 4."""
    return max(2, min(n, 4))


def per_min(ms: float) -> str:
    r = 60000.0 / ms if ms > 0 else 0.0
    return f"{r:.2f}" if r < 10 else f"{r:.0f}"


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    return s[len(s) // 2]


def _job(p: Plan, job: str) -> None:
    if p.job != job:
        p.job, p.samples, p.outcomes = job, {}, []


def sample(p: Plan, job: str, count: int, ms_seed: float) -> None:
    """A shard run with `count` cores busy in the tab (as it said when the shard was handed out)."""
    _job(p, job)
    if count == p.cores and ms_seed > 0:
        p.quiet = 0.0
        xs = p.samples.setdefault(str(count), [])
        xs.append(ms_seed)
        del xs[:-8]


def outcome(p: Plan, job: str, count: int, won: bool) -> None:
    """A shard this worker held at `count` cores: its copy kept, or the main box's."""
    _job(p, job)
    if count == p.cores:
        p.outcomes = [*p.outcomes, "won" if won else "lost"][-WINDOW:]


def change(p: Plan, new: int, why: str, now: float) -> str:
    old = p.cores
    if new < old:
        p.ceiling, p.ceiling_at = old, now
    p.cores, p.why, p.at = new, f"{old} → {new}: {why}", now
    p.outcomes = []
    p.samples.pop(str(new), None)      # measured afresh at the new count
    return p.why


def restart(p: Plan, start: int, now: float) -> str | None:
    """A plan below the start count goes to it, its ceiling, samples and outcomes cleared (a
    plan learned under a lower start); the note says so. None for a plan at or above it."""
    if p.cores >= start:
        return None
    old = p.cores
    p.cores, p.why, p.at = start, f"{old} → {start}: reset to the start count, {start}", now
    p.ceiling, p.ceiling_at, p.job, p.samples, p.outcomes, p.quiet = 0, 0.0, "", {}, [], 0.0
    return p.why


def lift(p: Plan, now: float) -> str | None:
    """A ceiling past its hold goes; the note says so (None while it holds, or with none)."""
    if not p.ceiling or now - p.ceiling_at < CEILING_SECONDS:
        return None
    p.lifted, p.lifted_at, p.ceiling, p.ceiling_at = p.ceiling, now, 0, 0.0
    return f"ceiling {p.lifted} lifted: an hour since the back-off from it; {p.cores} may climb to it again"


def connected(p: Plan, seconds: float, now: float) -> str | None:
    """`seconds` more connected; past STALE_SECONDS with no sample, the climb starts again."""
    p.quiet += max(0.0, seconds)
    if p.quiet < STALE_SECONDS:
        return None
    p.quiet, p.job, p.samples, p.outcomes = 0.0, "", {}, []
    gone = f"; ceiling {p.ceiling} cleared" if p.ceiling else ""
    if p.ceiling:
        p.lifted, p.lifted_at, p.ceiling, p.ceiling_at = p.ceiling, now, 0, 0.0
    p.why, p.at = f"at {p.cores}: no rate sample in an hour connected{gone}; climbing again", now
    return p.why


def decide(p: Plan, limit: int, now: float) -> str | None:
    """The plan's next count, applied, with its reason; None when it stays. `limit` is the
    tab's hardware cores, or its slider's cap; a cap under the plan's count is the user's
    choice, and nothing is decided while it holds."""
    lift(p, now)
    n = p.cores
    if n > limit:
        return None
    lower = {int(c): _median(xs) for c, xs in p.samples.items() if int(c) < n and len(xs) >= need(int(c))}
    below = max(lower, default=0)
    lost = p.outcomes.count("lost")
    if lost >= LOST and 2 * lost >= len(p.outcomes):
        return change(p, below or max(1, math.ceil(n / 2)),
                       f"shards lost to the main box {lost} of {len(p.outcomes)}", now)
    xs = p.samples.get(str(n), [])
    if len(xs) < need(n):
        return None
    ms = _median(xs)
    best = min(lower, key=lower.get, default=0)
    if best and ms > lower[best] * HOLD:
        return change(p, below, f"rate fell to {per_min(ms)} seeds/min a core from {per_min(lower[best])} at {best}", now)
    if lost or n >= limit:
        return None
    up = min(limit, n + UP)
    if p.ceiling and up >= p.ceiling and now - p.ceiling_at < CEILING_SECONDS:
        return None
    return change(p, up, f"rate held at {per_min(ms)} seeds/min a core"
                   + (f" (best {per_min(lower[best])} at {best})" if best else ""), now)


def prune(plans: dict[str, Plan]) -> None:
    """The newest KEEP names stay."""
    for name in sorted(plans, key=lambda k: plans[k].at)[:-KEEP] if len(plans) > KEEP else []:
        del plans[name]
