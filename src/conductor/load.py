"""Machine load: CPU busy and what is using it, measured beside each memory sample
(`daemon_memory.py`, `docs/memory.md`), so *Now*, the planner and a watch can see contention.

A sample is `{cpu, top: [{who, cpu, item}]}`: percent of the whole box, consumers biggest
first, `who` an item (all its processes together) or, `item` false, a process name outside
conductor's items. One scan of the box per sample (~0.3s at 270 processes on this box).
"""

from __future__ import annotations

import time

import psutil

TOP = 4                 # consumers kept per sample
MIN_PERCENT = 1.0       # a consumer under this is not named
MIN_SECONDS = 1.0       # a sample sooner than this (a rising memory level) repeats the last one


class Sampler:
    def __init__(self) -> None:
        self.times: dict[tuple[int, float], float] = {}     # (pid, created) → CPU seconds so far
        self.at: float | None = None
        self.last: dict | None = None

    def sample(self, owned: dict[int, str]) -> dict | None:
        """The load since the last call; `owned`: pid → the item it belongs to. None the
        first time: it takes two scans to see a rate."""
        at = time.monotonic()
        if self.at is not None and at - self.at < MIN_SECONDS:
            return self.last
        busy, times = psutil.cpu_percent(None), {}
        used: dict[tuple[str, bool], float] = {}
        for p in psutil.process_iter(["name", "cpu_times", "create_time"]):
            cpu, created = p.info.get("cpu_times"), p.info.get("create_time")
            if p.pid == 0 or cpu is None or created is None:    # 0: Windows' idle "process"
                continue
            key, spent = (p.pid, created), cpu.user + cpu.system
            times[key] = spent
            item = owned.get(p.pid)
            who = (item, True) if item else (p.info.get("name") or "?", False)
            # A pid not seen last time started since: all its time is new.
            used[who] = used.get(who, 0.0) + max(0.0, spent - self.times.get(key, 0.0))
        first, self.times = self.at is None, times
        elapsed, self.at = at - (self.at or at), at
        if first:
            return None
        whole = 100.0 / (elapsed * (psutil.cpu_count() or 1))
        rows = sorted(((s * whole, who) for who, s in used.items()), key=lambda r: -r[0])
        self.last = {"cpu": round(busy), "top": [{"who": who, "cpu": round(pct), "item": item}
                                                 for pct, (who, item) in rows[:TOP] if pct >= MIN_PERCENT]}
        return self.last


def text(load: dict) -> str:
    """`CPU 63%: aicomplete/034 41%, python.exe 20% (outside)`."""
    top = [f"{t['who']} {t['cpu']}%" + ("" if t.get("item") else " (outside)") for t in load.get("top") or []]
    return f"CPU {load.get('cpu')}%" + (": " + ", ".join(top) if top else "")
