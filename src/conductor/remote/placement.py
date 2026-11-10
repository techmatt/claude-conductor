"""Who runs a free shard: the worker expected to finish it soonest (`docs/remote-placement.md`).
Pure: `Board` gives it each worker's slots (when each is free next) and its time a shard,
and asks how many of a job's free shards fall to a worker's free slots.

Aicomplete 168 (Nov 10): placement was first-come, and the main box's 6 slots took a
4-shard job whole while the laptop, faster a core, sat idle; two jobs at once ran 12 main
processes, each shard 160 s there against the laptop's 85 s.

The rule: the free shards are dealt out one at a time, each to the slot that would finish it
first (its free time plus the worker's time a shard). A worker asking for work (a browser
core's poll, a main-box slot's claim) gets a shard only if that deal gives one to a slot of
its that is free now; else it waits, and the shard stays for the faster worker. A tie goes to
a remote worker, so an unmeasured one (timed at the main box's rate) gets work and a rate.
"""

from __future__ import annotations

import heapq


def deal(free: int, slots: dict[str, list[float]], shard_ms: dict[str, float], now: float,
         asking: str = "", main: str = "main") -> dict[str, int]:
    """How many of `free` shards go to each worker's slots that are free at `now`.
    `slots`: worker -> when each of its slots is free next (seconds, `now` for a free one).
    `shard_ms`: worker -> its expected time for one shard, in ms. A tie goes to a remote
    worker over `main`, and to the remote worker `asking` over the others."""
    heap = []
    for wid, times in slots.items():
        d = max(shard_ms.get(wid, 0.0), 1.0) / 1000
        rank = 2 if wid == main else 0 if wid == asking else 1
        for i, t in enumerate(times):
            ready = max(t, now)
            heap.append((ready + d, rank, wid, i, ready, d))
    heapq.heapify(heap)
    got: dict[str, int] = {}
    for _ in range(free):
        if not heap:
            break
        finish, tb, wid, i, ready, d = heapq.heappop(heap)
        if ready <= now:
            got[wid] = got.get(wid, 0) + 1
        heapq.heappush(heap, (finish + d, tb, wid, i, finish, d))
    return got


def busy_until(at: float, ms: float, now: float) -> float:
    """When a slot running a shard since `at` (expected `ms`) is free: an overdue one is
    taken as needing a whole shard more, so a stalled worker isn't waited for."""
    end = at + ms / 1000
    return end if end > now else now + ms / 1000


def slots_of(ends: list[float], count: int, now: float, asking: bool = False) -> list[float]:
    """A worker's `count` slots: the busy ones' ends (earliest first), the rest free now. One
    asking for work has a free slot whatever the counts say (its poll or claim proves it)."""
    busy = sorted(ends)[:max(count, 0)]
    out = busy + [now] * max(0, count - len(busy))
    if asking and not any(t <= now for t in out):
        out = [now, *out[:-1]] if out else [now]
    return out
