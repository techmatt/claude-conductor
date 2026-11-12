"""Conductor's own prompts: numbered when run, not when written (CLAUDE.md, *Run next prompt*).

Planner sessions write `prompts/inbox/<YYYYMMDD-HHMMSS>-<project>-<slug>.md`; nothing
picks a number until a Claude Code session here runs one. Then this tool gives it the next
free number, moves it to `prompts/<NN>_<slug>.md` and appends the ledger's `start` line.

    uv run python tools/prompts.py list                  # waiting, oldest first; and any open start
    uv run python tools/prompts.py next [--anyway]       # claim the oldest
    uv run python tools/prompts.py run <slug|file> [--anyway]   # claim one by name, out of order
    uv run python tools/prompts.py done <NN_slug> <report path> | stopped
    uv run python tools/prompts.py migrate               # unrun numbered prompts back to the inbox

A claim prints `Running <NN_slug>` and the prompt's path. It refuses (exit 2) while the
ledger has a `start` with no `done`/`stopped` after it, naming that run and its start;
`--anyway` (Matt says it's stale) records `<time> stale <name> (run anyway)` and goes on.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAMP = re.compile(r"^(\d{8}-\d{6})-([A-Za-z0-9_]+)-(.+)\.md$")
NUMBERED = re.compile(r"^(\d+)([a-z]*)_(.+)$")
LINE = re.compile(r"^(\S+) (start|done|stopped|stale) (\S+)")
CLOSES = {"done", "stopped", "stale"}


@dataclass
class Waiting:
    path: Path
    written: datetime
    project: str
    slug: str

    def line(self) -> str:
        return f"{self.written:%Y-%m-%d} {clock(self.written)}  {self.project:<14} {self.slug}"


def clock(dt: datetime | None = None) -> str:
    dt = dt or datetime.now()
    return f"{dt.hour % 12 or 12}:{dt.minute:02d}{'am' if dt.hour < 12 else 'pm'}"


def slug_of(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower() or "prompt"


def inbox(root: Path) -> list[Waiting]:
    """Waiting prompts, oldest first (by the stamp in the name)."""
    out = []
    for p in (root / "prompts" / "inbox").glob("*.md"):
        if m := STAMP.match(p.name):
            out.append(Waiting(p, datetime.strptime(m[1], "%Y%m%d-%H%M%S"), m[2], slug_of(m[3])))
    return sorted(out, key=lambda w: (w.written, w.path.name))


def misnamed(root: Path) -> list[str]:
    """Inbox `.md` files `inbox` skips, each with the name it wants (stamped with its write
    time). 106: a prompt saved as `106_<slug>.md` sat unseen behind "Nothing waiting"."""
    out = []
    for p in sorted((root / "prompts" / "inbox").glob("*.md")):
        if STAMP.match(p.name):
            continue
        m = NUMBERED.match(p.stem)
        slug = (m[3] if m else p.stem).replace("_", "-").strip("-") or "prompt"
        want = f"{datetime.fromtimestamp(p.stat().st_mtime):%Y%m%d-%H%M%S}-<project>-{slug}.md"
        out.append(f"Skipped, misnamed: inbox/{p.name} (rename it to {want} to queue it)")
    return out


def ledger(root: Path) -> list[tuple[str, str, str]]:
    """(time, word, name) per ledger line the tool reads."""
    try:
        text = (root / "prompts" / "ledger.md").read_text(encoding="utf-8")
    except OSError:
        return []
    return [(m[1], m[2], m[3]) for ln in text.splitlines() if (m := LINE.match(ln))]


def open_runs(root: Path) -> list[tuple[str, str]]:
    """(name, start time) of each run whose latest `start` has nothing closing it after."""
    state: dict[str, str | None] = {}
    for at, word, name in ledger(root):
        if word == "start":
            state[name] = at
        elif word in CLOSES:
            state[name] = None
    return [(n, at) for n, at in state.items() if at]


def append(root: Path, text: str) -> None:
    path = root / "prompts" / "ledger.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(f"{clock()} {text}\n")


def next_number(root: Path) -> int:
    """One past the highest number in `prompts/` or the ledger (duplicates are history)."""
    names = [p.stem for p in (root / "prompts").glob("*.md")] + [n for _, _, n in ledger(root)]
    return 1 + max((int(m[1]) for n in names if (m := NUMBERED.match(n))), default=0)


def guard(root: Path, anyway: bool) -> str | None:
    """None to go on; else why not."""
    runs = open_runs(root)
    if not runs:
        return None
    if anyway:
        for name, _ in runs:
            append(root, f"stale {name} (run anyway)")
        return None
    return "; ".join(f"{n} started at {at} and has no done line" for n, at in runs) + \
        ". One prompt at a time: if it's stale, say 'run anyway'."


def claim(root: Path, w: Waiting) -> str:
    n = next_number(root)
    name = f"{n:02d}_{w.slug}"
    target = root / "prompts" / f"{name}.md"
    os.replace(w.path, target)
    append(root, f"start {name}")
    return name


def find(root: Path, want: str) -> Waiting | Path | None:
    """An inbox prompt by slug, file name or stem; else an unrun numbered `prompts/` file."""
    key = Path(want).name.removesuffix(".md")
    for w in inbox(root):
        if key == w.path.stem or w.slug == slug_of(key):
            return w
    p = root / "prompts" / f"{key}.md"
    if NUMBERED.match(key) and p.is_file():
        return p
    hits = [q for q in (root / "prompts").glob("*.md") if (m := NUMBERED.match(q.stem)) and m[3] == key]
    return hits[0] if len(hits) == 1 else None


def migrate(root: Path) -> list[str]:
    """Numbered prompts with no ledger line go to the inbox, stamped with their write time."""
    named = {n for _, _, n in ledger(root)}
    moved = []
    for p in sorted((root / "prompts").glob("*.md")):
        m = NUMBERED.match(p.stem)
        if not m or p.stem in named or (root / "reports" / f"{p.stem}_report.md").exists():
            continue
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(p.stat().st_mtime))
        dest = root / "prompts" / "inbox" / f"{stamp}-conductor-{m[3].replace('_', '-')}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(p, dest)
        moved.append(f"{p.name} -> inbox/{dest.name}")
    return moved


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    ap = argparse.ArgumentParser(prog="prompts.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    nx = sub.add_parser("next")
    nx.add_argument("--anyway", action="store_true")
    rn = sub.add_parser("run")
    rn.add_argument("name")
    rn.add_argument("--anyway", action="store_true")
    dn = sub.add_parser("done")
    dn.add_argument("name")
    dn.add_argument("report")
    sub.add_parser("migrate")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for name, at in open_runs(root):
            print(f"open: {name} (started {at}, no done line)")
        waiting = inbox(root)
        print("\n".join(w.line() for w in waiting) if waiting else "Nothing waiting in prompts/inbox/.")
        for line in misnamed(root):
            print(line)
        return 0
    if a.cmd == "done":
        append(root, f"stopped {a.name}" if a.report == "stopped" else f"done {a.name} → {a.report}")
        return 0
    if a.cmd == "migrate":
        print("\n".join(migrate(root)) or "Nothing to migrate: every numbered prompt has a ledger line or a report.")
        return 0
    if (why := guard(root, a.anyway)) is not None:
        print(f"Refused: {why}")
        return 2
    if a.cmd == "next":
        waiting = inbox(root)
        if not waiting:
            print("\n".join(["Nothing waiting in prompts/inbox/.", *misnamed(root)]))
            return 1
        print("Waiting, oldest first:\n" + "\n".join("  " + w.line() for w in waiting))
        name = claim(root, waiting[0])
        left = len(waiting) - 1
    else:
        hit = find(root, a.name)
        if hit is None:
            print(f"No waiting or unrun prompt matches {a.name!r}.")
            return 1
        if isinstance(hit, Waiting):
            name = claim(root, hit)
        else:
            name = hit.stem
            append(root, f"start {name}")
        left = len(inbox(root))
    print(f"Running {name}\n{root / 'prompts' / (name + '.md')}\n{left} more waiting")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
