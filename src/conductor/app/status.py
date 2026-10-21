"""The window title and tray state: the most urgent thing across every open project.

Qt-free. *Needs Matt* (a blocking question, a red hold, a picker waiting, a daemon down
with work queued or running) ranks above *running* (items executing, or a stage still
staging), which ranks above *idle*. Unreviewed reports never count: the planner handles
those.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import Snapshot

APP = "Conductor"
GLYPHS = "◐◓◑◒"          # the running title's glyph, one step a tick (about a second)
NEED = "●"


@dataclass
class Status:
    kind: str                   # need | running | idle
    parts: list[str]            # what needs Matt (`1 question`, `red`, `pick`), or `2 running`

    def title(self, tick: int = 0) -> str:
        if self.kind == "idle":
            return APP
        glyph = NEED if self.kind == "need" else GLYPHS[tick % len(GLYPHS)]
        return f"{glyph} {APP} · {' · '.join(self.parts)}"


def _needs(snap: Snapshot) -> tuple[int, int, int, int]:
    """(blocking questions, red holds, pickers waiting, daemon down with work) in one project."""
    down = not snap.daemon.alive and any(r.running or r.staged or r.queued for r in snap.repos)
    return (sum(1 for q in snap.open_questions if q.block), sum(1 for r in snap.repos if r.red), snap.to_pick,
            int(down))


def _running(snap: Snapshot) -> int:
    """Items executing (setup, merge and concluding included), plus stages still staging:
    each has a live session. A staged item waiting with its plan does not count."""
    return sum(len(r.running) + sum(a.phase == "staging" for a in r.staged) for r in snap.repos)


def summarize(snaps: list[Snapshot]) -> Status:
    asks, reds, picks, down = (sum(c) for c in zip((0, 0, 0, 0), *map(_needs, snaps)))
    if asks or reds or picks or down:
        parts = [f"{asks} question{'s' * (asks != 1)}"] * bool(asks)
        parts += [("red" if reds == 1 else f"{reds} red")] * bool(reds)
        parts += [("pick" if picks == 1 else f"{picks} picks")] * bool(picks)
        parts += ["daemon down"] * bool(down)
        return Status("need", parts)
    if n := sum(map(_running, snaps)):
        return Status("running", [f"{n} running"])
    return Status("idle", [])
