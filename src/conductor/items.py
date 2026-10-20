"""Queue items: `queue/<repo>/NNN_name.md` with YAML front-matter."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import globs
from .audits import TEMPLATES
from .util import FrontMatterError, read_text, split_front_matter

NAME_RE = re.compile(r"^(\d{3,})_([A-Za-z0-9][A-Za-z0-9_.-]*)$")
FIELDS = {"repo", "locks", "depends_on", "after_red", "needs_planner", "readonly", "max_hours",
          "paths", "stage", "ultracode", "audit", "template"}


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
    ultracode: bool = False                             # launch with Claude Code's ultracode on
    audit: bool = False                                 # keep audit notes beside the report
    template: str | None = None                         # a conductor template (`audits.TEMPLATES`)
    body: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return f"{self.repo}/{self.name}"

    def meta(self) -> dict:
        return {"locks": self.locks, "depends_on": self.depends_on, "after_red": self.after_red,
                "needs_planner": self.needs_planner, "readonly": self.readonly,
                "max_hours": self.max_hours, "paths": self.paths, "stage": self.stage,
                "ultracode": self.ultracode, "audit": self.audit, "template": self.template}


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
    for flag in ("needs_planner", "readonly", "ultracode", "audit"):
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
    if meta.get("template") is not None:
        if meta["template"] not in TEMPLATES:
            item.errors.append(f"`template` must be one of {', '.join(TEMPLATES)}")
        else:
            item.template = meta["template"]
    if not body.strip() and item.template is None:          # a template is a whole prompt
        item.errors.append("the prompt body is empty")
    return item


def front_repo(path: Path) -> str | None:
    """The `repo:` front-matter field of an item file (e.g. one in `proposed/`), or None."""
    try:
        meta, _ = split_front_matter(read_text(path))
    except (FrontMatterError, OSError, UnicodeDecodeError):
        return None
    repo = (meta or {}).get("repo")
    return str(repo) if repo is not None else None


def number_matches(name: str, ref: str) -> bool:
    """`ref` (`NNN_name` or `NNN`, numerically) names the item `name`."""
    num = name.split("_", 1)[0]
    return name == ref or num == ref or (ref.isdigit() and num.isdigit() and int(num) == int(ref))


# An amendment to an item: `NNN_name.amend.md`, then `NNN_name.amend.2.md`, ... beside
# it in `queue/<repo>/`, applied in that order. Never an item file itself.
AMEND_RE = re.compile(r"^(.+)\.amend(?:\.(\d+))?$")


def amend_of(path: Path) -> tuple[str, int] | None:
    """(item name, order) for an amend file's path, or None for anything else."""
    m = AMEND_RE.match(path.stem) if path.suffix == ".md" else None
    return (m.group(1), int(m.group(2) or 1)) if m else None


def item_files(folder: Path) -> list[Path]:
    """The item files in `folder`: every `*.md` but an amend file."""
    return [p for p in sorted(folder.glob("*.md")) if amend_of(p) is None] if folder.is_dir() else []


def find(folder: Path, ref: str) -> list[Path]:
    """The item files in `folder` that `ref` names."""
    return [p for p in item_files(folder) if number_matches(p.stem, ref)]


# A reorder renames `NNN_name.md` to `NNN_name<REORDER_TEMP><new name>` first, then to
# `<new name>.md`: the temp name is not `*.md`, so a crash never leaves a half-renamed
# file that looks like an item, and it names its own target for recovery.
REORDER_TEMP = ".reorder~"


def renumber(names: list[str]) -> list[str]:
    """New names for `names`, given in their new order: they take the sorted numbers they
    already hold (each number string keeps its own zero padding); the rest of each name stays."""
    nums = sorted((n.split("_", 1)[0] for n in names), key=int)
    return [f"{num}_{n.split('_', 1)[1]}" for num, n in zip(nums, names)]


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
