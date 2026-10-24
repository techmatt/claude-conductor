"""Pickers: the manifest an executor writes, the choices Matt saves, and which pickers
still wait for him. Data only; the HTTP server is `picker`, the page `web/picker.html`.

    <repo>/<path>.json               the manifest, in the code repo (main checkout)
    picks/<name>.choices.json        what Matt saved for it, in the control folder

A manifest is named by its ref, `<repo>/<path relative to the repo root>`: the `pick:`
signal's value and the finish event's `detail.pick`. Choices files and `pick_open`
events carry the same ref as `manifest`, so nothing here needs the manifest itself to
say what is saved or opened.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .config import Config
from .events import Event, EventLog
from .state import View
from .util import parse_stamp, read_text, stamp, write_text

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_NOTE_CHARS = 4000
NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
# The image types the page can show, and what the server sends them as.
IMAGE_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".avif": "image/avif", ".svg": "image/svg+xml",
}


@dataclass(frozen=True)
class Option:
    key: str
    label: str
    image: Path          # absolute, normalised (links not followed), inside the repo


@dataclass(frozen=True)
class Row:
    id: str
    label: str
    note: str
    options: tuple[Option, ...]


@dataclass(frozen=True)
class Manifest:
    ref: str             # <repo>/<path>
    path: Path
    name: str
    title: str
    intro: str
    rows: tuple[Row, ...]

    def row(self, row_id: str) -> Row | None:
        return next((r for r in self.rows if r.id == row_id), None)


@dataclass
class PickInfo:
    """A finished item whose report named a manifest, and where Matt is with it."""
    item: str
    ref: str
    name: str | None         # known once opened or saved
    state: str               # saved | opened | waiting
    saved_at: str | None = None
    changed: int | None = None
    notes: int | None = None
    rows: int | None = None
    opened_at: str | None = None

    @property
    def choices(self) -> str | None:
        """The choices file, relative to the control folder."""
        return f"picks/{self.name}.choices.json" if self.name else None


# ---------------------------------------------------------------- the manifest
def normal(rel: str) -> str:
    """A manifest path as refs carry it: `/` separators, no `.` or doubled separators.
    A `..` or a root stays, for `load_manifest` to refuse."""
    return PurePosixPath(rel.strip().replace("\\", "/")).as_posix()


def split_ref(cfg: Config, ref: str) -> tuple[str, str]:
    repo, sep, rel = ref.strip().partition("/")
    if not sep or not rel.strip():
        raise ValueError(f"pick: expects `<repo>/<manifest path>`, got {ref!r}")
    if repo not in cfg.repos:
        raise ValueError(f"pick: {repo!r} is not a repo in conductor.toml")
    return repo, normal(rel)


def _inside(path: Path, root: Path) -> bool:
    """Containment by the path as written, never resolved: a `shared` folder is a
    junction out of the main checkout, and what lies behind it is still the repo's."""
    return path == root or path.is_relative_to(root)


def _absolute(path: Path) -> Path:
    """Absolute, `.` and `..` collapsed, links not followed."""
    return Path(os.path.normpath(os.path.abspath(path)))


def _relative(text: str) -> bool:
    """A relative path that climbs nowhere: no drive, no root, no `..`."""
    p = Path(text)
    return not (p.anchor or p.is_absolute() or ".." in p.parts or text.startswith(("/", "\\")))


def _text(obj: dict, key: str, where: str, required: bool) -> str:
    value = obj.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f"{where}{key}: {'a non-empty string is required' if required else 'must be a string'}")
    return value


def load_manifest(cfg: Config, ref: str) -> Manifest:
    """Read and validate the manifest `ref` names, resolved in the repo's main checkout.
    Raises ValueError with the reason, naming the place (`rows[2].options[1].image: ...`)."""
    repo, rel = split_ref(cfg, ref)
    ref = f"{repo}/{rel}"
    if not _relative(rel):
        raise ValueError(f"pick {ref}: the manifest path must be relative to the repo root, inside it")
    root = _absolute(cfg.repos[repo])
    path = _absolute(root / rel)
    if not _inside(path, root):
        raise ValueError(f"pick {ref}: the manifest path leaves the repo")
    if not path.is_file():
        raise ValueError(f"pick {ref}: no manifest file at {path}")
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError(f"pick {ref}: the manifest is over {MAX_MANIFEST_BYTES // 1024} KB")
    try:
        data = json.loads(read_text(path))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"pick {ref}: the manifest is not valid JSON: {exc}") from None
    try:
        return _manifest(data, ref, path, root)
    except ValueError as exc:
        raise ValueError(f"pick {ref}: {exc}") from None


def _manifest(data, ref: str, path: Path, root: Path) -> Manifest:
    if not isinstance(data, dict):
        raise ValueError("the manifest must be a JSON object")
    name = data.get("name")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ValueError(f"name: {name!r} must be a slug (lowercase letters, digits, _ or -; up to 64)")
    title = _text(data, "title", "", True)
    intro = _text(data, "intro", "", False)
    raw_rows = data.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError("rows: a non-empty list is required")
    rows, ids = [], set()
    for r, raw in enumerate(raw_rows):
        where = f"rows[{r}]."
        if not isinstance(raw, dict):
            raise ValueError(f"rows[{r}]: must be an object")
        row_id = _text(raw, "id", where, True)
        if row_id in ids:
            raise ValueError(f"{where}id: {row_id!r} is used by an earlier row")
        ids.add(row_id)
        label = _text(raw, "label", where, True)
        note = _text(raw, "note", where, False)
        raw_opts = raw.get("options")
        if not isinstance(raw_opts, list) or not raw_opts:
            raise ValueError(f"{where}options: a non-empty list is required")
        options, keys = [], set()
        for o, opt in enumerate(raw_opts):
            at = f"{where}options[{o}]."
            if not isinstance(opt, dict):
                raise ValueError(f"{where}options[{o}]: must be an object")
            key = _text(opt, "key", at, True)
            if any(c.isspace() for c in key):
                raise ValueError(f"{at}key: {key!r} may not hold whitespace")
            if key in keys:
                raise ValueError(f"{at}key: {key!r} is used by an earlier option in this row")
            keys.add(key)
            options.append(Option(key, _text(opt, "label", at, False), _image(opt, at, path, root)))
        rows.append(Row(row_id, label, note, tuple(options)))
    return Manifest(ref, path, name, title, intro, tuple(rows))


def _image(opt: dict, at: str, manifest: Path, root: Path) -> Path:
    img = _text(opt, "image", at, True)
    if not _relative_up(img):
        raise ValueError(f"{at}image: {img!r} must be a path relative to the manifest")
    image = _absolute(manifest.parent / img)
    if not _inside(image, root):
        raise ValueError(f"{at}image: {img!r} leaves the repo")
    if image.suffix.lower() not in IMAGE_TYPES:
        raise ValueError(f"{at}image: {img!r} is not one of {', '.join(sorted(IMAGE_TYPES))}")
    if not image.is_file():
        raise ValueError(f"{at}image: missing file {image.relative_to(root).as_posix()}")
    return image


def _relative_up(text: str) -> bool:
    """Relative to the manifest: `..` may climb (the repo-root check comes after), but
    nothing may be rooted or name a drive."""
    p = Path(text)
    return not (p.anchor or p.is_absolute() or text.startswith(("/", "\\")))


# ---------------------------------------------------------------- choices
def choices_path(control: Path, name: str) -> Path:
    return Path(control) / "picks" / f"{name}.choices.json"


def load_choices(control: Path, name: str) -> dict | None:
    return _read_choices(choices_path(control, name))


def _read_choices(path: Path) -> dict | None:
    try:
        data = json.loads(read_text(path))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("manifest"), str) or not isinstance(data.get("rows"), list):
        return None
    # A hand-edited stamp must not reach a clock() or a comparison in the daemon's tick.
    try:
        if parse_stamp(data["saved_at"]).tzinfo is None:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return data


def scan_choices(control: Path) -> dict[str, dict]:
    """Every readable choices file, keyed by its manifest ref (the newest save wins)."""
    pdir = Path(control) / "picks"
    if not pdir.is_dir():
        return {}
    out: dict[str, dict] = {}
    for f in sorted(pdir.glob("*.choices.json")):
        data = _read_choices(f)
        if data is None:
            continue
        old = out.get(data["manifest"])
        if old is None or str(data.get("saved_at", "")) >= str(old.get("saved_at", "")):
            out[data["manifest"]] = data
    return out


def applied(manifest: Manifest, saved: dict | None) -> dict | None:
    """The saved choices as they apply to the manifest now: rows matched by id, a row or
    key no longer there falling back to the first option (its note kept)."""
    if not saved:
        return None
    by_id = {r.get("id"): r for r in saved.get("rows", []) if isinstance(r, dict)}
    rows = []
    for row in manifest.rows:
        old = by_id.get(row.id)
        if old is None:
            continue
        keys = [o.key for o in row.options]
        key = old.get("key") if old.get("key") in keys else keys[0]
        note = old.get("note") if isinstance(old.get("note"), str) else ""
        rows.append({"id": row.id, "key": key, "changed": key != keys[0], "note": note})
    return {**saved, "rows": rows}


def check_body(manifest: Manifest, body) -> list[dict]:
    """The page's save body `{"rows": [{"id", "key", "note"}]}` as choices rows, in
    manifest order. Every row exactly once, every key one of its row's options; `changed`
    is worked out here, never taken from the page."""
    if not isinstance(body, dict) or not isinstance(body.get("rows"), list):
        raise ValueError('expected {"rows": [{"id", "key", "note"}, ...]}')
    got: dict[str, dict] = {}
    for i, r in enumerate(body["rows"]):
        if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not isinstance(r.get("key"), str):
            raise ValueError(f"rows[{i}]: needs string `id` and `key`")
        if manifest.row(r["id"]) is None:
            raise ValueError(f"rows[{i}]: no row {r['id']!r} in the manifest")
        if r["id"] in got:
            raise ValueError(f"rows[{i}]: row {r['id']!r} given twice")
        note = r.get("note", "")
        if note is None:
            note = ""
        if not isinstance(note, str) or len(note) > MAX_NOTE_CHARS:
            raise ValueError(f"rows[{i}]: note must be a string of at most {MAX_NOTE_CHARS} characters")
        got[r["id"]] = {**r, "note": note.strip()}
    out = []
    for row in manifest.rows:
        r = got.get(row.id)
        if r is None:
            raise ValueError(f"row {row.id!r} is missing")
        keys = [o.key for o in row.options]
        if r["key"] not in keys:
            raise ValueError(f"row {row.id!r}: {r['key']!r} is not one of its options {keys}")
        out.append({"id": row.id, "key": r["key"], "changed": r["key"] != keys[0], "note": r["note"]})
    return out


def save_choices(control: Path, manifest: Manifest, body) -> dict:
    """Validate the page's body and write `picks/<name>.choices.json` (overwriting)."""
    rows = check_body(manifest, body)
    data = {
        "name": manifest.name, "title": manifest.title, "manifest": manifest.ref, "saved_at": stamp(),
        "changed": sum(r["changed"] for r in rows), "notes": sum(bool(r["note"]) for r in rows),
        "rows": rows,
    }
    write_text(choices_path(control, manifest.name), json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return data


# ---------------------------------------------------------------- what waits for Matt
def pending(cfg: Config, view: View, log: EventLog) -> list[PickInfo]:
    """One PickInfo per finish whose report named a manifest, oldest first: every one in
    the current view, and one settled by a checkpoint while its picker is still unsaved
    (and no later finish names the same manifest), since that still waits for Matt. A
    save or open counts only when it came after that finish, so a manifest path reused by
    a later item starts again at `waiting`."""
    choices = scan_choices(cfg.control)
    opens: dict[str, Event] = {}
    for ev in log.query(kinds=["pick_open"]):
        if isinstance(ev.detail.get("manifest"), str):
            opens[ev.detail["manifest"]] = ev
    latest: dict[str, int] = {}
    for fin in view.finishes.values():
        if ref := fin.detail.get("pick"):
            latest[ref] = max(latest.get(ref, 0), fin.id)
    out = []
    for item_id, fin in sorted(view.finishes.items(), key=lambda kv: kv[1].id):
        ref = fin.detail.get("pick")
        if not ref:
            continue
        settled = view.settled(item_id)
        if settled and latest[ref] != fin.id:
            continue
        info = PickInfo(item_id, ref, None, "waiting")
        op = opens.get(ref)
        if op is not None and op.id > fin.id:
            info.state, info.name, info.opened_at = "opened", op.detail.get("name"), op.ts
        saved = choices.get(ref)
        if saved and _after(saved.get("saved_at"), fin.ts):
            info.state, info.name, info.saved_at = "saved", saved.get("name") or info.name, saved["saved_at"]
            info.changed, info.notes = saved.get("changed"), saved.get("notes")
            info.rows = len(saved.get("rows", []))
        if settled and info.state == "saved":
            continue
        out.append(info)
    return out


def saved_at(cfg: Config, view: View) -> dict[str, str]:
    """item id → when its picker was saved, for each finish whose `pick:` manifest has a
    choices file saved after it (the digests' "picks saved <time>")."""
    choices = scan_choices(cfg.control)
    return {item_id: c["saved_at"] for item_id, fin in view.finishes.items()
            if (ref := fin.detail.get("pick")) and (c := choices.get(ref)) and _after(c.get("saved_at"), fin.ts)}


def _after(saved_at, ts: str) -> bool:
    try:
        return parse_stamp(str(saved_at)) >= parse_stamp(ts)
    except (TypeError, ValueError):
        return False
