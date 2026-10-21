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
          "paths", "stage", "ultracode", "audit", "template", "alongside", "resume"}
GIST_CHARS = 160


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
    alongside: str | None = None                        # what it touches: may start beside running items
    resume_of: str | None = None                        # `resume:` the finished item whose session it forks
    body: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return f"{self.repo}/{self.name}"

    def meta(self) -> dict:
        return {"locks": self.locks, "depends_on": self.depends_on, "after_red": self.after_red,
                "needs_planner": self.needs_planner, "readonly": self.readonly,
                "max_hours": self.max_hours, "paths": self.paths, "stage": self.stage,
                "ultracode": self.ultracode, "audit": self.audit, "template": self.template,
                "alongside": self.alongside, "resume_of": self.resume_of}

    def gist(self) -> str:
        """One line on what the item does, for the items started beside it: its `alongside`
        line, else its title (the body's first heading), else its template or name."""
        title = next((ln.lstrip("#").strip() for ln in self.body.splitlines() if ln.startswith("#")), "")
        text = self.alongside or title or (f"the {self.template} template" if self.template else
                                           f"questions for {self.resume_of}" if self.resume_of else self.name)
        return text if len(text) <= GIST_CHARS else text[:GIST_CHARS - 1] + "…"


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
    if amend_of(path) is not None:          # the queue would take it for an amendment
        item.errors.append("named like an amendment (.amend[.<tag>].md); rename it")
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
    if meta.get("alongside") is not None:
        value = meta["alongside"]
        if not isinstance(value, str) or not value.strip() or "\n" in value.strip():
            item.errors.append("`alongside` must be one line on what this item touches")
        elif meta.get("template") is not None:
            # A template is repo-wide: it waits its turn (the exclusive ones run alone).
            item.errors.append("`alongside` is refused on a template item: templates are repo-wide and wait their turn")
        else:
            item.alongside = value.strip()
    if meta.get("resume") is not None:
        _resume(item, meta)
    elif item.template in RESUME_TEMPLATES:
        item.errors.append(f"`template: {item.template}` needs `resume: <repo>/<NNN_name>`, the finished item it looks back on")
    if not body.strip() and item.template is None:          # a template is a whole prompt
        item.errors.append("the prompt body is empty")
    return item


RESUME_TEMPLATES = ("self_audit",)     # the templates a resume item may use, and only it


def _resume(item: Item, meta: dict) -> None:
    """`resume: <repo>/<NNN_name>`: always readonly, in its own repo. Whether that item has
    finished with a session is the queue's to say (`resume.problem`, from `state.scan`)."""
    value = meta["resume"]
    repo, slash, name = str(value).partition("/") if isinstance(value, str) else ("", "", "")
    if not slash or not NAME_RE.match(name):
        item.errors.append("`resume` must name a finished item as <repo>/<NNN_name>")
        return
    if repo != item.repo:
        item.errors.append(f"`resume`: {value} is in another repo; queue the item in `{repo}`")
        return
    if meta.get("readonly") is False:
        item.errors.append("`readonly: false` is refused on a resume item: it is always readonly")
    if item.template is not None and item.template not in RESUME_TEMPLATES:
        item.errors.append(f"`template: {item.template}` is refused on a resume item (only "
                           f"{', '.join(RESUME_TEMPLATES)})")
    item.readonly = True
    item.resume_of = f"{repo}/{name}"


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


# An amendment to an item: `NNN_name.amend.md`, or `NNN_name.amend.<anything>.md` (`.amend.2.md`,
# a timestamp, ...) beside it in `queue/<repo>/`, applied in name order. Never an item file itself.
AMEND_RE = re.compile(r"^(.+?)\.amend(?:\.(.+))?$")


def amend_of(path: Path) -> tuple[str, int | str] | None:
    """(item name, order) for an amend file's path, or None for anything else: the order
    is 1 for `.amend.md`, N for `.amend.N.md`, else the free-form part."""
    m = AMEND_RE.match(path.stem) if path.suffix == ".md" else None
    if not m:
        return None
    tag = m.group(2) or "1"
    return m.group(1), int(tag) if tag.isdigit() else tag


def amend_key(order: int | str) -> tuple:
    """Name order of amend files, numbers compared as numbers: `.amend.md` < `.amend.2.md` <
    `.amend.10.md` < `.amend.20261020-1530.md`."""
    return tuple((0, int(c), "") if c.isdigit() else (1, 0, c) for c in re.split(r"(\d+)", str(order)) if c)


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
