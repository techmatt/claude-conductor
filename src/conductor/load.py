"""Machine load: CPU busy and what is using it, measured beside each memory sample
(`daemon_memory.py`, `docs/memory.md`), so *Now*, the planner and a watch can see contention.

A sample is `{cpu, top: [{who, cpu, item, of?}]}`: percent of the whole box, consumers
biggest first. `who` is an item of this daemon's (all its processes together, `item` true)
or, `item` false: another project's item (`of` its project), conductor's own processes
(`who` and `of` "conductor": daemons, app, broker, ask servers; 103), or a process name
outside them all. One scan of the box per sample (~0.3s at 270 processes on this box); a
process's command line is read once, when it is first seen.

Other projects' items: each daemon writes its items' pids to `.conductor/load-items.json`
at each sample (`publish`); a sampler finds the other daemons by their command lines
(`-m conductor run <control>`) and reads theirs. Aicomplete 170: conductor's own ~3-4% and
fractal-engine.exe, another project's item, both counted as outside.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import psutil

from .util import write_text

TOP = 4                 # consumers kept per sample
MIN_PERCENT = 1.0       # a consumer under this is not named
MIN_SECONDS = 1.0       # a sample sooner than this (a rising memory level) repeats the last one
FREE_THREADS = 4        # the preamble's load budget: an item leaves this many hardware threads free
RUN_HEADROOM = 2        # threads under the budget's ceiling the preamble's default leaves for the session itself
OWN_SHARE = 25          # percent of the box an item's tree uses before `load_high` messages it (80) ...
OWN_THREADS = 3         # ... or this many threads' worth, whichever comes first


def run_threads() -> int:
    """The preamble's default thread count for a simulation or test run: the box's threads
    less the load budget and some headroom (6 on a 12-thread box), so an executor at its
    default leaves room for outside processes (aicomplete #3405: one at the 8 the budget
    allows tripped `load_high` beside them and was killed)."""
    return max(1, (psutil.cpu_count() or 1) - FREE_THREADS - RUN_HEADROOM)


ITEMS_FILE = "load-items.json"   # in a daemon's state dir: its items' pids (`publish`)


def publish(state_dir: Path, project: str, owned: dict[int, str]) -> None:
    """This daemon's items' pids, for the other daemons' samplers to name them by."""
    write_text(state_dir / ITEMS_FILE, json.dumps({"project": project, "pids": {str(k): v for k, v in owned.items()}}) + "\n")


def conductor_cmd(cmd: list[str]) -> Path | bool:
    """Is this command line conductor's own: `python -m conductor…`, `conductor.exe …` or
    `uv run conductor …`? A daemon's (`… conductor run <control>`) gives its control
    folder, any other of conductor's True, anything else False."""
    args = [a for a in cmd if a]
    rest = None
    for i, a in enumerate(args[:4]):
        nxt = args[i + 1] if i + 1 < len(args) else ""
        if a == "-m" and (nxt == "conductor" or nxt.startswith("conductor.")):
            rest = args[i + 2:] if nxt == "conductor" else []
        elif a == "run" and nxt == "conductor" and i and Path(args[0]).stem.lower() == "uv":
            rest = args[i + 2:]
        elif i < 2 and Path(a).stem.lower() == "conductor" and Path(a).suffix.lower() in ("", ".exe"):
            rest = args[i + 1:]
        if rest is not None:
            break
    if rest is None:
        return False
    return Path(rest[1]) if len(rest) > 1 and rest[0] == "run" else True


def _resolve(p: Path) -> Path:
    try:
        return p.resolve()
    except OSError:
        return p


class Sampler:
    def __init__(self, control: Path | None = None) -> None:
        self.control = _resolve(Path(control)) if control else None   # this daemon's: its items come as `owned`
        self.times: dict[tuple[int, float], float] = {}     # (pid, created) → CPU seconds so far
        self.cmds: dict[tuple[int, float], Path | bool] = {}  # (pid, created) → `conductor_cmd`
        self.at: float | None = None
        self.last: dict | None = None

    def _cmd(self, p, key: tuple[int, float]) -> Path | bool:
        if key not in self.cmds:
            try:
                self.cmds[key] = conductor_cmd(p.cmdline() or [])
            except (psutil.Error, AttributeError, OSError):
                self.cmds[key] = False
        return self.cmds[key]

    def _others(self, daemons: set[Path]) -> dict[int, tuple[str, str]]:
        """pid → (item, project) for the other daemons' items, from their `publish`."""
        out: dict[int, tuple[str, str]] = {}
        for control in daemons:
            if self.control is not None and _resolve(control) == self.control:
                continue
            try:
                data = json.loads((control / ".conductor" / ITEMS_FILE).read_text(encoding="utf-8"))
                project = str(data.get("project") or control.name)
                for pid, item in (data.get("pids") or {}).items():
                    out[int(pid)] = (str(item), project)
            except (OSError, ValueError, TypeError, AttributeError):
                continue
        return out

    def sample(self, owned: dict[int, str]) -> dict | None:
        """The load since the last call; `owned`: pid → the item it belongs to. None the
        first time: it takes two scans to see a rate."""
        at = time.monotonic()
        if self.at is not None and at - self.at < MIN_SECONDS:
            return self.last
        busy, times = psutil.cpu_percent(None), {}
        seen: list[tuple[int, str, Path | bool, float]] = []
        for p in psutil.process_iter(["name", "cpu_times", "create_time"]):
            cpu, created = p.info.get("cpu_times"), p.info.get("create_time")
            if p.pid == 0 or cpu is None or created is None:    # 0: Windows' idle "process"
                continue
            key, spent = (p.pid, created), cpu.user + cpu.system
            times[key] = spent
            cmd = False if p.pid in owned else self._cmd(p, key)
            # A pid not seen last time started since: all its time is new.
            seen.append((p.pid, p.info.get("name") or "?", cmd, max(0.0, spent - self.times.get(key, 0.0))))
        others = self._others({c for _, _, c, _ in seen if isinstance(c, Path)})
        used: dict[tuple[str, bool, str], float] = {}
        for pid, name, cmd, spent in seen:
            if item := owned.get(pid):
                who = (item, True, "")
            elif pid in others:
                who = (others[pid][0], False, others[pid][1])
            elif cmd:
                who = ("conductor", False, "conductor")
            else:
                who = (name, False, "")
            used[who] = used.get(who, 0.0) + spent
        self.cmds = {k: v for k, v in self.cmds.items() if k in times}
        first, self.times = self.at is None, times
        elapsed, self.at = at - (self.at or at), at
        if first:
            return None
        whole = 100.0 / (elapsed * (psutil.cpu_count() or 1))
        rows = sorted(((s * whole, who) for who, s in used.items()), key=lambda r: -r[0])
        self.last = {"cpu": round(busy), "top": [{"who": who, "cpu": round(pct), "item": item} | ({"of": of} if of else {})
                                                 for pct, (who, item, of) in rows[:TOP] if pct >= MIN_PERCENT]}
        return self.last


def free_threads(load: dict) -> float:
    """Hardware threads a sample left idle."""
    return round((psutil.cpu_count() or 1) * (100 - load.get("cpu", 0)) / 100, 1)


def own_share(load: dict, item: str) -> bool:
    """Is `item`'s own tree a real share of the box's load (80): at least `OWN_SHARE` of the
    CPU, or `OWN_THREADS` threads' worth? Below that the load is someone else's to cut."""
    pct = sum(t["cpu"] for t in load.get("top") or [] if t.get("item") and t.get("who") == item)
    return pct >= OWN_SHARE or pct * (psutil.cpu_count() or 1) / 100 >= OWN_THREADS


def _tag(t: dict) -> str:
    if t.get("item") or t.get("of") == "conductor":
        return ""
    return f" ({t['of']})" if t.get("of") else " (outside)"


def text(load: dict) -> str:
    """`CPU 63%: aicomplete/034 41%, fractals/012 9% (fractals), conductor 3%, MsMpEng.exe 2% (outside)`."""
    top = [f"{t['who']} {t['cpu']}%{_tag(t)}" for t in load.get("top") or []]
    return f"CPU {load.get('cpu')}%" + (": " + ", ".join(top) if top else "")
