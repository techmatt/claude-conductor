"""Queue items: `queue/<repo>/NNN_name.md` with YAML front-matter."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import globs
from .util import FrontMatterError, read_text, split_front_matter

NAME_RE = re.compile(r"^(\d{3,})_([A-Za-z0-9][A-Za-z0-9_.-]*)$")
FIELDS = {"repo", "locks", "depends_on", "after_red", "needs_planner", "readonly", "max_hours",
          "paths", "stage"}


@dataclass
class Item:
    repo: str
    name: str                    # `001_hello` (file stem)
    path: Path
    num: int = 0
    locks: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    after_red: str = "stop"
    needs_planner: bool = False
    readonly: bool = False
    max_hours: float | None = None
    paths: list[str] = field(default_factory=list)     # globs the item may change; [] = undeclared
    stage: bool | None = None                           # None: the repo's `stage` setting
    body: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return f"{self.repo}/{self.name}"

    def meta(self) -> dict:
        return {"locks": self.locks, "depends_on": self.depends_on, "after_red": self.after_red,
                "needs_planner": self.needs_planner, "readonly": self.readonly,
                "max_hours": self.max_hours, "paths": self.paths, "stage": self.stage}


def _list(value, what: str, errors: list[str]) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, int)):
        return [str(value)]
    if isinstance(value, list) and all(isinstance(x, (str, int)) for x in value):
        return [str(x) for x in value]
    errors.append(f"`{what}` must be a name or a list of names")
    return []


def parse(path: Path, repo: str) -> Item:
    """Parse an item file. Problems are collected in `errors`, never raised."""
    item = Item(repo=repo, name=path.stem, path=path)
    m = NAME_RE.match(path.stem)
    if not m:
        item.errors.append("file name must be NNN_name.md")
    else:
        item.num = int(m.group(1))
    try:
        meta, body = split_front_matter(read_text(path))
    except (FrontMatterError, OSError, UnicodeDecodeError) as exc:
        item.errors.append(str(exc))
        return item
    item.body = body.strip() + "\n"
    if not meta:
        item.errors.append("front-matter with at least `repo:` is required")
        return item
    unknown = set(meta) - FIELDS
    if unknown:
        item.errors.append(f"unknown front-matter fields: {sorted(unknown)}")
    if meta.get("repo") != repo:
        item.errors.append(f"`repo: {meta.get('repo')}` does not match queue folder `{repo}`")
    item.locks = _list(meta.get("locks"), "locks", item.errors)
    item.depends_on = _list(meta.get("depends_on"), "depends_on", item.errors)
    item.after_red = str(meta.get("after_red", "stop"))
    if item.after_red not in ("stop", "continue"):
        item.errors.append("`after_red` must be stop or continue")
    for flag in ("needs_planner", "readonly"):
        value = meta.get(flag, False)
        if not isinstance(value, bool):
            item.errors.append(f"`{flag}` must be true or false")
        setattr(item, flag, bool(value))
    if meta.get("max_hours") is not None:
        try:
            item.max_hours = float(meta["max_hours"])
            if item.max_hours <= 0:
                raise ValueError
        except (TypeError, ValueError):
            item.errors.append("`max_hours` must be a positive number")
    item.paths = _list(meta.get("paths"), "paths", item.errors)
    for glob in item.paths:
        if globs.invalid(glob):
            item.errors.append(f"`paths`: {glob!r} must be a relative glob inside the repo")
    if meta.get("stage") is not None:
        if not isinstance(meta["stage"], bool):
            item.errors.append("`stage` must be true or false")
        else:
            item.stage = meta["stage"]
    if not body.strip():
        item.errors.append("the prompt body is empty")
    return item


def resolve_ref(ref: str, repo: str, known: list[str]) -> str | None:
    """`001_x`, `001`, `repo/001_x` or `repo/001` → a known item id, or None.

    A bare number matches numerically: YAML reads an unquoted `001` as the integer 1.
    """
    ref_repo, _, rest = ref.rpartition("/")
    ref_repo = ref_repo or repo
    for item_id in known:
        r, _, name = item_id.partition("/")
        num = name.split("_", 1)[0]
        if r == ref_repo and (name == rest or num == rest
                              or (rest.isdigit() and num.isdigit() and int(num) == int(rest))):
            return item_id
    return None
