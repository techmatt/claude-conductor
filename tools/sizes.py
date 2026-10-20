"""Print every doc and code file against its size budget; exit 1 on a breach.

    uv run python tools/sizes.py           # breaches and the largest files
    uv run python tools/sizes.py --all     # every file

The budgets are the table under the *Size budgets* heading in `CLEANUP.md`, their one home: each row
is a glob, a budget (`20 KB`, or `900 lines`) and a reason. A file takes the first row
whose glob matches it, so specific rows come before general ones.
"""

from __future__ import annotations

import re
import subprocess
import sys
from fnmatch import fnmatchcase
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*(\d+)\s*(KB|lines)\s*\|")
TOP = 12


def budgets() -> list[tuple[str, int, str]]:
    text = (ROOT / "CLEANUP.md").read_text(encoding="utf-8")
    head = re.search(r"^## .*Size budgets.*$", text, re.M)
    section = text[head.end():].split("\n## ", 1)[0] if head else ""
    rows = [(m[1], int(m[2]), m[3]) for ln in section.splitlines() if (m := ROW.match(ln))]
    if not rows:
        sys.exit("tools/sizes.py: no budget rows under a '## … Size budgets' heading in CLEANUP.md")
    return rows


def tracked() -> list[str]:
    """Tracked files plus new ones not ignored, so a cleanup measures what it is about to commit."""
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.md", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [p for p in out.stdout.split() if (ROOT / p).is_file()]     # not one deleted but uncommitted


def measure(path: str, unit: str) -> int:
    data = (ROOT / path).read_bytes()
    return data.count(b"\n") if unit == "lines" else len(data)


def main(argv: list[str]) -> int:
    rows, found = budgets(), []
    for path in tracked():
        for glob, limit, unit in rows:
            if fnmatchcase(path, glob):
                size = measure(path, unit)
                cap = limit * 1000 if unit == "KB" else limit
                found.append((size / cap, path, size, cap, unit))
                break
    found.sort(reverse=True)
    over = [f for f in found if f[0] > 1]
    shown = found if "--all" in argv else found[:max(TOP, len(over))]
    print(f"{'file':<44} {'size':>10} {'budget':>10}  use")
    for share, path, size, cap, unit in shown:
        fmt = (lambda n: f"{n / 1000:.1f} KB") if unit == "KB" else (lambda n: f"{n} lines")
        flag = "  OVER" if share > 1 else ""
        print(f"{path:<44} {fmt(size):>10} {fmt(cap):>10}  {share:4.0%}{flag}")
    print(f"\n{len(found)} files measured, {len(over)} over budget" + ("" if "--all" in argv else "; --all lists every file"))
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
