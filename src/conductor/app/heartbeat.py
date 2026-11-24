"""The app's watch on each project's daemon (docs/app.md, *Daemon down*). Qt-free.

A daemon that died (its process gone, `daemon.json` left: it never stopped cleanly) or that
lives but has not beaten for `STUCK_SECONDS` raises one error toast naming the project. A
death after `MIN_LIFE_SECONDS` of life is restarted by the app first (149), and the toast
says so; a younger one (a crash loop) and a hang only toast. One
per daemon: the key is its pid and start time, remembered across app restarts, so a death
toasts once however many ticks (or app launches) see it. A daemon stopped on purpose
removes `daemon.json` and stays quiet, as does a self-restart's handover (`GRACE_SECONDS`).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..state import DaemonInfo
from ..util import clock, duration, now, parse_stamp

STUCK_SECONDS = 180     # a live daemon this long without a heartbeat is not answering
GRACE_SECONDS = 10      # a death must still stand this long: a restart's new daemon replaces it first
MIN_LIFE_SECONDS = 120  # one that died younger is not restarted by the app (a crash loop): toast only
KEEP = 50               # toasted keys remembered


@dataclass
class Down:
    key: str
    title: str
    body: str
    revive: bool = False    # died after a real life: the app starts a new daemon (`revived` words the toast)


def lived(d: DaemonInfo) -> float:
    """Seconds from its start to its last heartbeat (0 when either is unknown)."""
    try:
        return (parse_stamp(d.beat) - parse_stamp(d.started)).total_seconds()
    except (TypeError, ValueError):
        return 0.0


def revived(project: str, d: DaemonInfo, running: list[str], why: str | None) -> Down:
    """The toast for a death the app restarted (149: helium-hustle's went 80 minutes unseen)."""
    beat = clock(d.beat) if d.beat else "?"
    adopts = f"; it adopts {', '.join(running)}" if running else ""
    return Down(key(d), f"{project}: the daemon died; restarted",
                f"pid {d.pid}, last heartbeat {beat}" + (f": {why}" if why else "")
                + f". A new daemon is starting{adopts}.")


def stuck(d: DaemonInfo, at=None) -> bool:
    """Alive, but no heartbeat for `STUCK_SECONDS`."""
    if not (d.alive and d.beat):
        return False
    try:
        return ((at or now()) - parse_stamp(d.beat)).total_seconds() >= STUCK_SECONDS
    except ValueError:
        return False


def key(d: DaemonInfo) -> str:
    return f"{d.pid} {d.started}"


class Heartbeats:
    def __init__(self, toasted: list[str] | None = None, mono=time.monotonic):
        self.toasted: list[str] = list(toasted or [])
        self.mono = mono
        self.seen: dict[str, float] = {}        # key → when this app first saw it down

    def check(self, project: str, d: DaemonInfo, at=None) -> Down | None:
        """The toast `d` raises now, or None (healthy, stopped on purpose, already toasted)."""
        if not (d.died or stuck(d, at)):
            return None
        k = key(d)
        if k in self.toasted:
            return None
        first = self.seen.setdefault(k, self.mono())
        if d.died and self.mono() - first < GRACE_SECONDS:
            return None
        self.toasted = [*self.toasted, k][-KEEP:]
        beat = clock(d.beat) if d.beat else "never"
        try:
            gap = duration(((at or now()) - parse_stamp(d.beat)).total_seconds()) if d.beat else "?"
        except ValueError:
            gap = "?"
        if d.died:
            young = lived(d) < MIN_LIFE_SECONDS
            return Down(k, f"{project}: the daemon died",
                        f"pid {d.pid}, last heartbeat {beat} ({gap} ago)"
                        + (f", {duration(lived(d))} after it started, so not restarted (see .conductor/daemon.out.log)"
                           if young else "")
                        + ". Nothing runs, finishes or answers until it restarts: the app's Restart daemon.",
                        revive=not young)
        return Down(k, f"{project}: the daemon is not answering",
                    f"pid {d.pid} lives but its last heartbeat was {beat} ({gap} ago). "
                    "The app's Restart daemon… kills it and starts a new one; running items are adopted.")
