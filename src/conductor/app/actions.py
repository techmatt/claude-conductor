"""Everything the app writes. No Qt.

The app changes nothing in the queue itself: approve, cancel, reorder, clear, reviewed, tell, checkpoint, run, pick and
stop are `signals/` files the daemon carries out and logs. Besides those it writes only an
answer file (the same function `conductor answer` uses) and an amend file (as `conductor
amend` does), starts the daemon, and opens a console on a finished item's session.
"""

from __future__ import annotations

import itertools
import os
from datetime import datetime
from pathlib import Path

from .. import items, procs, questions

BY = "app"
_counter = itertools.count(1)


def write_signal(control: Path, *lines: str, by: str = BY) -> Path:
    """`signals/<by>-<YYYYmmdd-HHMMSS>-<nnnnnn>.signal`, written as `.tmp` then renamed, so
    the daemon (which skips `.tmp`) never reads half a file. Every event it produces
    carries `by: <by>` (`app`; the CLI passes `cli`). The daemon applies files in name
    order, so the counter is zero-padded: two clicks in one second stay in click order."""
    sdir = Path(control) / "signals"
    sdir.mkdir(parents=True, exist_ok=True)
    text = "".join(f"{ln}\n" for ln in (f"by: {by}", *lines))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    while True:
        base = f"{by}-{stamp}-{next(_counter):06d}"
        final = sdir / f"{base}.signal"
        tmp = sdir / f"{base}.tmp"
        if not final.exists() and not tmp.exists():
            break
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, final)
    return final


def approve(control: Path, name: str) -> Path:
    return write_signal(control, f"approve: {name}")


def cancel(control: Path, ref: str) -> Path:
    """`ref`: a proposed item's `proposed/NNN_name`, or a queued item's `repo/NNN_name`."""
    return write_signal(control, f"cancel: {ref}")


def reorder(control: Path, where: str, names: list[str]) -> Path:
    """`where`: a repo or `proposed`; `names` take their own sorted NNNs in this order."""
    return write_signal(control, f"reorder: {where} {' '.join(names)}")


def swap(control: Path, where: str, names: list[str], index: int, delta: int) -> Path | None:
    """Move `names[index]` up (-1) or down (+1) by swapping it with its neighbour."""
    j = index + delta
    if not (0 <= index < len(names) and 0 <= j < len(names)):
        return None
    return reorder(control, where, [names[j], names[index]] if delta > 0 else [names[index], names[j]])


def reviewed(control: Path, item_id: str, by: str = BY) -> Path:
    """`item_id`: a finished item's `repo/NNN_name`."""
    return write_signal(control, f"reviewed: {item_id}", by=by)


def tell(control: Path, item_id: str, text: str, by: str = BY) -> Path:
    """A message for running `item_id`. A signal line is one line: line breaks become
    ` / `."""
    one = " / ".join(ln.strip() for ln in text.strip().splitlines() if ln.strip())
    if not one:
        raise ValueError("the message is empty")
    return write_signal(control, f"tell: {item_id} {one}", by=by)


def outline(control: Path, item_id: str, by: str = BY) -> Path:
    """Ask running `item_id` for an updated outline: a tell of the fixed request."""
    return write_signal(control, f"outline: {item_id}", by=by)


def amend(control: Path, item_id: str, text: str, by: str = BY) -> Path:
    """An addition to queued, staged or running `item_id`'s instructions, any length:
    `queue/<repo>/NNN_name.amend[.N].md` (after any already waiting), written as `.tmp`
    then renamed. The daemon logs `amended` and delivers it by the item's state."""
    text = text.strip()
    if not text:
        raise ValueError("the amendment is empty")
    repo, _, name = item_id.partition("/")
    qdir = Path(control) / "queue" / repo
    qdir.mkdir(parents=True, exist_ok=True)
    taken = [a[1] for p in qdir.glob(f"{name}.amend*.md")
             if (a := items.amend_of(p)) and a[0] == name and isinstance(a[1], int)]
    n = max(taken, default=0) + 1
    final = qdir / (f"{name}.amend.md" if n == 1 else f"{name}.amend.{n}.md")
    tmp = final.with_name(final.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"---\nby: {by}\n---\n\n{text}\n")
    os.replace(tmp, final)
    return final


def checkpoint(control: Path, label: str = "", by: str = BY) -> Path:
    """A new checkpoint (a session boundary). The label is one line."""
    one = " ".join(label.split())
    return write_signal(control, f"checkpoint: {one}".rstrip(), by=by)


def session(control: Path, label: str = "start", by: str = BY) -> Path:
    """A planner session's start: a checkpoint plus the `session.md` digest, read mark moved."""
    one = " ".join(label.split())
    return write_signal(control, f"session: {one}".rstrip(), by=by)


def open_session(cfg, item_id: str, session_id: str, spawn=None):
    """A new console running `<executor command> --resume <session id>` in the item's
    worktree when it still exists, else the repo's main checkout. Returns (cwd, argv)."""
    repo, _, name = item_id.partition("/")
    wt = cfg.worktree(repo, name)
    cwd = wt if wt.is_dir() else cfg.repos[repo]
    argv = [procs.resolve(cfg.command[0]), *cfg.command[1:], "--resume", session_id]
    (spawn or procs.open_console)(argv, cwd)
    return cwd, argv


def run_target(control: Path, name: str, stop: bool = False, by: str = BY) -> Path:
    return write_signal(control, f"run: {name}" + (" stop" if stop else ""), by=by)


def pick(control: Path, ref: str, by: str = BY) -> Path:
    """`ref`: a manifest's `<repo>/<path>`, as a finish event's `detail.pick` names it. The
    daemon validates it, serves the picker and opens it in the browser (`pick_open`)."""
    return write_signal(control, f"pick: {ref}", by=by)


def clear(control: Path, repo: str) -> Path:
    return write_signal(control, f"clear: {repo}")


def stop_daemon(control: Path) -> Path:
    return write_signal(control, "stop: daemon")


def start_daemon(control: Path):
    """Spawn the daemon detached; its output goes to `.conductor/daemon.out.log`."""
    control = Path(control).resolve()
    return procs.spawn_daemon(control, control / ".conductor" / "daemon.out.log")


class AnswerError(ValueError):
    pass


def answer(control: Path, qid: str, text: str) -> questions.Question:
    """Write `questions/<id>.answer.md` as `conductor answer` does, plus `by: app`
    front-matter (dropped before the executor sees it; put on the `answer` event)."""
    if not text.strip():
        raise AnswerError("the answer is empty")
    q = questions.find(Path(control), qid)      # loaded now: `is_open` is current
    if q is None:
        raise AnswerError(f"no single question matches {qid!r}")
    if q.answer_path.exists():
        raise AnswerError(f"{q.id} already has an answer file; not overwriting it")
    questions.write_answer(q, text, by=BY)
    return questions.load(q.path) or q          # resolved meanwhile? then it is late


def answer_note(q: questions.Question) -> str:
    """What answering `q` did, worded as `conductor answer` words it."""
    if q.is_open:
        return f"answered {q.id}; the executor gets it within a few seconds"
    return f"{q.id} was already {q.result.get('outcome')}; the answer is logged as late and changes nothing"
