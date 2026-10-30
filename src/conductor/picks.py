"""Pickers: the manifest an executor writes, the choices Matt saves, and which pickers
still wait for him. Data only; the HTTP server is `picker`, the page `web/picker.html`.

    <repo>/<path>.json               the manifest, in the code repo (main checkout)
    picks/<name>.choices.json        what Matt saved for it, in the control folder

A manifest is named by its ref, `<repo>/<path relative to the repo root>`: the `pick:`
signal's value and the finish event's `detail.pick`. Choices files and `pick_open`
events carry the same ref as `manifest`, so nothing here needs the manifest itself to
say what is saved or opened.

Conductor retires a picker (deletes its manifest and images, never its choices) once a
later round of its series is saved, or on a `retire:` signal; executors never delete one.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import git
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
    many: bool = False                  # `"kind": "many"`: each option kept or dropped, any number kept
    default: tuple[str, ...] = ()       # a many row's keys kept to start with (`default`); none by default

    @property
    def keys(self) -> list[str]:
        return [o.key for o in self.options]

    def kept(self, keys) -> list[str]:
        """A many row's keys in option order, dropping any it doesn't offer."""
        got = set(keys)
        return [k for k in self.keys if k in got]


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
        kind = raw.get("kind", "one")
        if kind not in ("one", "many"):
            raise ValueError(f"{where}kind: {kind!r} must be \"one\" (pick one option) or \"many\" (keep any)")
        default = raw.get("default", [])
        if kind == "many" and (not isinstance(default, list)
                               or not all(isinstance(k, str) and k in keys for k in default)):
            raise ValueError(f"{where}default: a many row's default is a list of its own option keys")
        rows.append(Row(row_id, label, note, tuple(options), kind == "many",
                        tuple(k for k in (o.key for o in options) if kind == "many" and k in default)))
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
        note = old.get("note") if isinstance(old.get("note"), str) else ""
        if row.many:
            kept = row.kept(k for k in old.get("keys", []) if isinstance(k, str)) \
                if isinstance(old.get("keys"), list) else list(row.default)
            rows.append({"id": row.id, "keys": kept, "changed": kept != list(row.default), "note": note})
            continue
        keys = row.keys
        key = old.get("key") if old.get("key") in keys else keys[0]
        rows.append({"id": row.id, "key": key, "changed": key != keys[0], "note": note})
    return {**saved, "rows": rows}


def check_body(manifest: Manifest, body) -> list[dict]:
    """The page's save body `{"rows": [{"id", "key" | "keys", "note"}]}` as choices rows, in
    manifest order. Every row exactly once; a one row's `key` is one of its options, a many
    row's `keys` a list of them (any number, kept in option order); `changed` is worked out
    here, never taken from the page."""
    if not isinstance(body, dict) or not isinstance(body.get("rows"), list):
        raise ValueError('expected {"rows": [{"id", "key", "note"}, ...]}')
    got: dict[str, dict] = {}
    for i, r in enumerate(body["rows"]):
        if not isinstance(r, dict) or not isinstance(r.get("id"), str):
            raise ValueError(f"rows[{i}]: needs a string `id`")
        row = manifest.row(r["id"])
        if row is None:
            raise ValueError(f"rows[{i}]: no row {r['id']!r} in the manifest")
        if row.many and not (isinstance(r.get("keys"), list) and all(isinstance(k, str) for k in r["keys"])):
            raise ValueError(f"rows[{i}]: row {r['id']!r} keeps any number: needs `keys`, a list of strings")
        if not row.many and not isinstance(r.get("key"), str):
            raise ValueError(f"rows[{i}]: needs string `id` and `key`")
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
        keys = row.keys
        if row.many:
            if bad := [k for k in r["keys"] if k not in keys]:
                raise ValueError(f"row {row.id!r}: {bad} are not among its options {keys}")
            kept = row.kept(r["keys"])
            out.append({"id": row.id, "keys": kept, "changed": kept != list(row.default), "note": r["note"]})
            continue
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
    gone = {ev.detail.get("manifest"): ev.id for ev in retired(log).values()}
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
        if not ref or gone.get(ref, 0) > fin.id:
            continue                            # retired: its files are gone
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


# ---------------------------------------------------------------- retiring
SERIES_RE = re.compile(r"(.+)_r(\d+)")
RETIRED_SHOWN_HOURS = 24.0     # the inbox's *Picks* lists a retired picker this long


def series(name: str) -> tuple[str, int] | None:
    """`art_alts_r2` → (`art_alts`, 2): the name minus a trailing `_r<N>`, and N. None
    for a name with no round: only a `retire:` signal retires it."""
    m = SERIES_RE.fullmatch(name)
    return (m[1], int(m[2])) if m else None


def retired(log: EventLog) -> dict[str, Event]:
    """name → its `pick_retired` event."""
    return {ev.detail["name"]: ev for ev in log.query(kinds=["pick_retired"]) if isinstance(ev.detail.get("name"), str)}


def known(cfg: Config, log: EventLog) -> dict[str, str]:
    """name → manifest ref for every picker conductor has seen: choices files, `pick_open`
    events, and finishes' `pick:` manifests that still load. The newest ref wins."""
    out: dict[str, str] = {}
    for ev in log.query(kinds=["finish"]):
        if (ref := ev.detail.get("pick")) and ref not in out.values():
            try:
                out[load_manifest(cfg, ref).name] = ref
            except ValueError:
                pass
    for ev in log.query(kinds=["pick_open"]):
        if isinstance(ev.detail.get("name"), str) and isinstance(ev.detail.get("manifest"), str):
            out[ev.detail["name"]] = ev.detail["manifest"]
    for c in sorted(scan_choices(cfg.control).values(), key=lambda c: str(c.get("saved_at"))):
        if isinstance(c.get("name"), str):
            out[c["name"]] = c["manifest"]
    return out


def superseded(cfg: Config, log: EventLog) -> list[tuple[str, str, str]]:
    """(name, ref, later name) for each live picker a later round of its series has
    saved choices for: `art_r1` once `art_r2` (or r3) is saved."""
    saved: dict[str, tuple[int, str]] = {}       # series → its highest saved round, and that name
    for c in scan_choices(cfg.control).values():
        if isinstance(c.get("name"), str) and (s := series(c["name"])) and s[1] > saved.get(s[0], (-1, ""))[0]:
            saved[s[0]] = (s[1], c["name"])
    if not saved:
        return []
    gone = retired(log)
    out = []
    for name, ref in sorted(known(cfg, log).items()):
        s = series(name)
        if name not in gone and s and s[0] in saved and s[1] < saved[s[0]][0]:
            out.append((name, ref, saved[s[0]][1]))
    return out


def retire(cfg: Config, log: EventLog, name: str, ref: str) -> tuple[list[str], list[str]]:
    """Delete a picker's manifest and images: (deleted, kept), repo-relative. Kept: a
    file git tracks (deleting it would leave the main checkout dirty) and an image another
    live picker still shows. A manifest already gone, or one its path now holds under
    another name (a later round written over it), deletes nothing."""
    try:
        manifest = load_manifest(cfg, ref)
    except ValueError:
        return [], []
    if manifest.name != name:
        return [], []
    repo = ref.partition("/")[0]
    root = _absolute(cfg.repos[repo])
    gone = retired(log)
    in_use: set[Path] = set()
    for other, other_ref in known(cfg, log).items():
        if other != name and other not in gone and other_ref != ref:
            try:
                in_use |= {o.image for r in load_manifest(cfg, other_ref).rows for o in r.options}
            except ValueError:
                pass
    files = list(dict.fromkeys([manifest.path, *(o.image for r in manifest.rows for o in r.options)]))
    try:
        tracked = {_absolute(root / p) for p in git.ls_files(root)}
    except (OSError, RuntimeError):
        tracked = set(files)                    # unknown: delete nothing
    deleted, kept = [], []
    for f in files:
        rel = f.relative_to(root).as_posix()
        if f in tracked or f in in_use:
            kept.append(rel)
            continue
        try:
            f.unlink(missing_ok=True)
            deleted.append(rel)
        except OSError:
            kept.append(rel)
    return deleted, kept


def _after(saved_at, ts: str) -> bool:
    try:
        return parse_stamp(str(saved_at)) >= parse_stamp(ts)
    except (TypeError, ValueError):
        return False
