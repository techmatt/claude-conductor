"""The `quiet` lock (143; docs/scheduling.md, *Quiet*): an item with `locks: [quiet]` holds the
whole box for a timing bar. While it is set up or executing, no other item of any project
starts (a watch still does: it only reads), and it waits for another project's quiet item
to clear. fractals 060 lost a 30-minute reading to another project's item.

The holder is one machine-wide file, `~/.conductor/quiet.json` (`$CONDUCTOR_QUIET_FILE` for
tests): `{item, project, control, since}`, written at its launch and removed at its finish.
It counts only while that control folder's run record for the item is in setup or exec, so a
daemon that died or an item that finished never holds the box. `quiet` needs no
`conductor.toml` declaration.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .util import clock, parse_stamp, stamp, write_text

LOCK = "quiet"
ENV = "CONDUCTOR_QUIET_FILE"


def path() -> Path:
    return Path(os.environ.get(ENV) or Path.home() / ".conductor" / "quiet.json")


def _same(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def holder() -> dict | None:
    """The live holder `{item, project, control, since}`, or None."""
    from .talk import run_record
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
        item, control = str(data["item"]), Path(data["control"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    rec = run_record(control, item)
    if rec is None or rec.get("phase", "exec") not in ("setup", "exec"):
        return None
    return data


def blocks(control: Path, item_id: str, template: str | None = None) -> str | None:
    """Why `item_id` of `control` may not start while another item holds `quiet`, or None."""
    if template == "watch" or (h := holder()) is None:
        return None
    if h["item"] == item_id and _same(h["control"], control):
        return None
    since = f" since {clock(parse_stamp(h['since']))}" if h.get("since") else ""
    return f"quiet: {h.get('project')}'s {h['item']} holds the box for a timing bar{since}"


def claim(control: Path, project: str, item_id: str) -> None:
    write_text(path(), json.dumps({"item": item_id, "project": project, "control": str(control),
                                   "since": stamp()}) + "\n")


def release(control: Path, item_id: str) -> None:
    """At `item_id`'s finish: the file goes, if it is still this item's."""
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if data.get("item") == item_id and _same(data.get("control", ""), control):
        path().unlink(missing_ok=True)
