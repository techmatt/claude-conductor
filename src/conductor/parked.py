"""Parked edits: small changes the planner parks for a repo, which conductor hands to the
next serial item launched there, so no prompt carries them by hand.

    parked/<repo>.md         the planner's, over the bridge: one `- ` line per edit;
                             `[rules]` first marks one that changes play
    parked/<repo>.done.md    conductor's: each edit handed out, with its item and event

`parked/` is runtime (it ignores itself). A serial item (not `alongside`, readonly, a
template or a resume item) carries every line, `[rules]` ones too (143: they write the
repo's `docs/rulings.md`; before, only a `rules: true` item took them). Its report answers each `parked P<n>: done | skipped
(why)`; a skipped or unanswered one, or every one of an item that went red, goes back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .items import Item
from .util import read_text, retry_io, stamp, write_text

ROOT = "parked"
TAG = "[rules]"
ANSWER_RE = re.compile(r"^\W*parked\s+P(\d+)\W*:?\s*(done|skipped)\b\s*(.*)$", re.IGNORECASE)
SECTION = """
## Parked edits (also do these)

Small edits the planner parked for this repo. Do each alongside the work above, unless it
conflicts with it or can't be done safely. They are part of this item whatever the prompt's
own file limits say ("touch no other files" included; only a declared `paths:` binds them).
Your report answers each on its own line, right
after the header: `parked P<n>: done` or `parked P<n>: skipped (why)`. A skipped one goes
back to the list for a later item. An edit that changes play (a rule, a number) may turn a
sim or loadout check red: patch the check when the red is the edit's intended effect (an
expected value moving), else leave it red and report it; its `parked P<n>:` line says which.

{lines}
"""


@dataclass(frozen=True)
class Edit:
    text: str            # the line as written, without `- `
    rules: bool


def path(control: Path, repo: str, done: bool = False) -> Path:
    return Path(control) / ROOT / f"{repo}{'.done' if done else ''}.md"


def prepare(control: Path) -> None:
    root = Path(control) / ROOT
    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".gitignore").is_file():     # ignored without a request, as backlog/ is
        write_text(root / ".gitignore", "*\n")


def _parse(line: str) -> Edit | None:
    if not line.startswith("- ") or not line[2:].strip():
        return None
    text = line[2:].strip()
    tagged = text.lower().startswith(TAG)
    return Edit(text, tagged)


def waiting(control: Path, repo: str) -> list[Edit]:
    try:
        lines = read_text(path(control, repo)).splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    return [e for ln in lines if (e := _parse(ln))]


def serial(item: Item) -> bool:
    return not (item.alongside or item.readonly or item.template or item.resume_of)


def eligible(item: Item, edits: list[Edit]) -> list[Edit]:
    """What `item` would carry of `edits`: every one when it is serial, else none."""
    return list(edits) if serial(item) else []


def take(control: Path, item: Item, event_hint: str = "") -> list[Edit]:
    """Move what `item` carries out of `parked/<repo>.md` into the done file, at launch.
    Lines it doesn't carry stay where they were, in order."""
    p = path(control, item.repo)
    try:
        lines = read_text(p).splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    edits = waiting(control, item.repo)
    taken = eligible(item, edits)
    if not taken:
        return []
    keep, gone = [], {e.text for e in taken}
    for ln in lines:
        e = _parse(ln)
        if e is not None and e.text in gone:
            gone.discard(e.text)            # a duplicate line is taken once
            continue
        keep.append(ln)
    retry_io(write_text, p, "\n".join(keep).rstrip("\n") + ("\n" if any(k.strip() for k in keep) else ""))
    _append(path(control, item.repo, done=True),
            [f"- {e.text} — {item.name} ({stamp()}{', ' + event_hint if event_hint else ''})" for e in taken])
    return taken


class Unmatched(ValueError):
    """A remove or replace whose piece names no waiting line, or several."""


OPS = ("remove", "replace")


def signal_parts(value: str) -> tuple[str, str, str, str | None]:
    """A `park:` signal's value → (repo, op, piece or line, new line): op `add`, `remove` or
    `replace` (144). ValueError for a malformed one."""
    repo, _, rest = value.strip().partition(" ")
    rest = rest.strip()
    op, _, tail = rest.partition(" ")
    if op.lower() not in OPS:
        if not rest:
            raise ValueError("park: expects a repo and a line")
        return repo, "add", rest, None
    tail = tail.strip()
    if op.lower() == "remove":
        if not tail:
            raise ValueError("park: remove expects a piece of a waiting line")
        return repo, "remove", tail, None
    piece, sep, new = tail.partition("=>")
    if not sep or not piece.strip() or not new.strip():
        raise ValueError("park: replace expects `<piece> => <new line>`")
    return repo, "replace", piece.strip(), new.strip()


def apply(control: Path, repo: str, op: str, piece: str, new: str | None) -> tuple[str | None, str | None, list[Edit]]:
    """One `park:` line: (the line as it was, the line now, what waits after). Unmatched
    for a remove or replace whose piece isn't in exactly one waiting line."""
    if op != "add":
        was, after = change(control, repo, piece, new)
        return was, new, after
    line = " ".join(piece.split()).removeprefix("- ")
    p = path(control, repo)
    try:
        old = read_text(p)
    except (OSError, UnicodeDecodeError):
        old = ""
    retry_io(write_text, p, old + ("" if not old or old.endswith("\n") else "\n") + f"- {line}\n")
    return None, line, waiting(control, repo)


def change(control: Path, repo: str, piece: str, new: str | None = None) -> tuple[str, list[Edit]]:
    """Remove (`new` None) or replace the one waiting line that holds `piece` (144: aicomplete's
    7-10 rounds line, superseded by a ruling, would have reverted it had it ridden). Returns
    (the line as it was, what waits after). A piece in none or several lines is refused,
    naming them; one already handed out names its item, since only a message reaches it now."""
    piece = " ".join(piece.split()).removeprefix("- ")
    if not piece:
        raise Unmatched("the piece is empty: give a few words found in one waiting line")
    p = path(control, repo)
    try:
        lines = read_text(p).splitlines()
    except (OSError, UnicodeDecodeError):
        lines = []
    hits = [n for n, ln in enumerate(lines) if (e := _parse(ln)) and piece.lower() in " ".join(e.text.split()).lower()]
    if len(hits) != 1:
        waiting_now = [e.text for e in waiting(control, repo)]
        if hits:
            raise Unmatched(f"{len(hits)} waiting lines hold {piece!r}: give a longer piece; they are: "
                            + " | ".join(lines[n][2:].strip()[:120] for n in hits))
        gone = [ln for ln in _done_lines(control, repo) if piece.lower() in " ".join(ln.split()).lower()]
        raise Unmatched(f"no waiting line holds {piece!r}" + (
            f"; it was already handed out ({gone[-1][2:].strip()[:200]}): tell that item, or park its undo"
            if gone else "") + "; waiting: " + (" | ".join(t[:120] for t in waiting_now) or "none"))
    n = hits[0]
    was = lines[n][2:].strip()
    if new is None:
        del lines[n]
    else:
        new = " ".join(new.split()).removeprefix("- ")
        if not new:
            raise Unmatched("the replacement line is empty: remove the line instead")
        lines[n] = f"- {new}"
    retry_io(write_text, p, "\n".join(lines).rstrip("\n") + ("\n" if any(ln.strip() for ln in lines) else ""))
    return was, waiting(control, repo)


def _done_lines(control: Path, repo: str) -> list[str]:
    try:
        return [ln for ln in read_text(path(control, repo, done=True)).splitlines() if ln.startswith("- ")]
    except (OSError, UnicodeDecodeError):
        return []


def listing(edits: list[Edit], width: int = 160) -> str:
    """What waits, for a receipt: `P1. … | P2. …`, each cut at `width`."""
    return " | ".join(f"P{n}. {e.text if len(e.text) <= width else e.text[:width - 1] + '…'}"
                      for n, e in enumerate(edits, 1)) or "none"


def put_back(control: Path, repo: str, texts: list[str]) -> None:
    """Edits an item skipped, left unanswered or lost to a red go back, ahead of the rest."""
    if not texts:
        return
    p = path(control, repo)
    try:
        old = read_text(p)
    except (OSError, UnicodeDecodeError):
        old = ""
    head = "".join(f"- {t}\n" for t in texts)
    retry_io(write_text, p, head + old)


def _append(p: Path, lines: list[str]) -> None:
    try:
        old = read_text(p)
    except (OSError, UnicodeDecodeError):
        old = ""
    retry_io(write_text, p, old + ("" if not old or old.endswith("\n") else "\n") + "".join(f"{ln}\n" for ln in lines))


def section(edits: list[dict]) -> str:
    """The prompt's section for the edits a launch took (`[{n, text}]`)."""
    if not edits:
        return ""
    return SECTION.format(lines="\n".join(f"- P{e['n']}. {e['text']}" for e in edits))


def answers(report_text: str) -> dict[int, tuple[str, str]]:
    """`parked P<n>: done | skipped (why)` lines anywhere in the report: n → (verdict, rest)."""
    out = {}
    for ln in report_text.splitlines():
        if m := ANSWER_RE.match(ln.strip()):
            out.setdefault(int(m[1]), (m[2].lower(), m[3].strip()))
    return out


def settle(edits: list[dict], report_text: str, landed: bool) -> tuple[list[dict], list[dict]]:
    """(done, back): with the item's work landed, what its report answered `done` stays
    done and the rest goes back; with nothing landed (red), all of it goes back."""
    said = answers(report_text) if landed else {}
    done = [e for e in edits if said.get(e["n"], ("",))[0] == "done"]
    return done, [e for e in edits if e not in done]


def counts(control: Path, repo: str) -> tuple[int, int]:
    """(waiting, of them `[rules]`) for the inbox and digest."""
    edits = waiting(control, repo)
    return len(edits), sum(e.rules for e in edits)
