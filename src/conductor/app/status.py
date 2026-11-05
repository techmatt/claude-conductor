"""The window title and tray state: the most urgent thing across every open project.

Qt-free. *Needs Matt* (a blocking question, a red hold, a picker waiting, a daemon down
with work queued or running) ranks above *running* (items executing, or a stage still
staging), which ranks above *idle*. Across all three, *waiting* counts what waits for
Matt's review (81): an unreviewed report, or any open question (a blocking one counts
here too). It colours the marks orange: the taskbar icon's badge, the tray's hub, the
title's dot when nothing runs, a project tab's hub and label. Pausing never hides it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Snapshot

APP = "Conductor"
GLYPHS = "◐◓◑◒"          # the running title's glyph, one step a tick (about a second)
NEED = "●"
WAIT = "🟠"              # nothing running, something waiting (`docs/app.md`, *Waiting*)


@dataclass
class Status:
    kind: str                   # need | running | idle
    parts: list[str]            # what needs Matt (`1 question`, `red`, `pick`), or `2 running`
    waiting: int = 0            # unreviewed reports plus open questions
    wait_parts: list[str] = field(default_factory=list)     # `2 to review`, `1 open question`

    def title(self, tick: int = 0) -> str:
        parts = self.parts + self.wait_parts
        if self.kind == "idle":
            return f"{WAIT} {APP} · {' · '.join(parts)}" if self.waiting else APP
        glyph = NEED if self.kind == "need" else GLYPHS[tick % len(GLYPHS)]
        return f"{glyph} {APP} · {' · '.join(parts)}"

    @property
    def badge(self) -> str | None:
        """The taskbar icon's mark: `need` (red), `wait` (orange, still), `turn` (orange,
        turning: running and waiting) or None (running alone, or quiet: as before 81)."""
        if self.kind == "need":
            return "need"
        if self.waiting:
            return "turn" if self.kind == "running" else "wait"
        return None


def _needs(snap: Snapshot) -> tuple[int, int, int, int]:
    """(blocking questions, red holds, pickers waiting, daemon down with work) in one project."""
    down = not snap.daemon.alive and any(r.running or r.staged or r.queued for r in snap.repos)
    return (sum(1 for q in snap.open_questions if q.block), sum(1 for r in snap.repos if r.red), snap.to_pick,
            int(down))


def _waits(snap: Snapshot) -> tuple[int, int]:
    """(unreviewed reports, open questions not blocking) in one project, paused repos included."""
    return sum(r.unreviewed for r in snap.repos), sum(1 for q in snap.open_questions if not q.block)


def _running(snap: Snapshot, skip_paused: bool = False) -> int:
    """Items executing (setup, merge and concluding included), plus stages still staging:
    each has a live session. A staged item waiting with its plan does not count."""
    return sum(len(r.running) + sum(a.phase == "staging" for a in r.staged)
               for r in snap.repos if not (skip_paused and r.name in snap.paused))


def moving(snap: Snapshot) -> int:
    """What turns a project's tab glyph: `_running` less the repos paused (their items
    wait, clocks stopped)."""
    return _running(snap, skip_paused=True)


def tab_text(name: str, snap: Snapshot | None, tick: int = 0) -> str:
    """A project's tab label: its name, led by the title's turning glyph while it moves."""
    return f"{GLYPHS[tick % len(GLYPHS)]} {name}" if snap is not None and moving(snap) else name


def tab_kind(snap: Snapshot) -> str:
    """A project tab's hub: need (red) over waiting (orange) over running (gold) over idle."""
    st = summarize([snap])
    return "waiting" if st.kind != "need" and st.waiting else st.kind


def summarize(snaps: list[Snapshot]) -> Status:
    asks, reds, picks, down = (sum(c) for c in zip((0, 0, 0, 0), *map(_needs, snaps)))
    reviews, held = (sum(c) for c in zip((0, 0), *map(_waits, snaps)))
    wait = dict(waiting=reviews + held + asks,
                wait_parts=[f"{reviews} to review"] * bool(reviews)
                + [f"{held} open question{'s' * (held != 1)}"] * bool(held))
    if asks or reds or picks or down:
        parts = [f"{asks} question{'s' * (asks != 1)}"] * bool(asks)
        parts += [("red" if reds == 1 else f"{reds} red")] * bool(reds)
        parts += [("pick" if picks == 1 else f"{picks} picks")] * bool(picks)
        parts += ["daemon down"] * bool(down)
        return Status("need", parts, **wait)
    if n := sum(map(_running, snaps)):
        return Status("running", [f"{n} running"], **wait)
    return Status("idle", [], **wait)
