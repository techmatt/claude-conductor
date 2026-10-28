"""`conductor rename-repo`: give a repo a new key everywhere conductor keys by it, and
optionally move its checkout, in one step with the daemon stopped (docs/control-folder.md,
*Renaming a repo*).

Renamed: the `queue/ backlog/ running/ done/` folders and `receipts/` folders named for
the repo; every file named `<old>__…` or `…__<old>__…` (reports, questions, picks and the
`.conductor/` journals) and `.conductor/push/<old>.log`. Rewritten: run records, receipts,
questions, journals, the generated digests, items' `repo:` front matter, every event's
repo, item, paths and detail, `conductor.toml` and `state.md` (committed together). An
item's body and an executor's own files (report, log, prompt) are history and keep
their text. events.db is append-only; this is the one writer that edits rows, and it
logs `repo_renamed` after.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

from . import config as config_mod
from . import git, guard, state
from .config import CONFIG_NAME, ConfigError
from .events import EventLog
from .util import read_text, retry_io, stamp, write_text

ITEM_DIRS = ("queue", "backlog", "running", "done")
TEXT_SUFFIXES = {".json", ".jsonl", ".md"}
DIGESTS = ("inbox.md", "session.md", "review.md")


class RenameError(ValueError):
    pass


def _path_forms(p: Path) -> list[str]:
    """How a path is spelled in conductor's files: native, JSON-escaped, and with `/`."""
    s = str(p)
    return list(dict.fromkeys([s, s.replace("\\", "\\\\"), s.replace("\\", "/")]))


class Rekey:
    """The string rewrites for one rename: absolute paths of the checkout and of the
    repo's worktree folder, the repo's path as written in conductor.toml, then the repo
    key wherever conductor writes it as one (`<old>/NNN_x`, `<old>__`, `__<old>__`,
    `queue/<old>`, `` `<old>` ``, `` `<old>/ ``). A bare word in prose is left alone."""

    def __init__(self, old: str, new: str, paths: list[tuple[str, str]]):
        self.old, self.new = old, new
        end = r"(?![\w-])"
        self.literal = [(re.compile(re.escape(a) + end, re.IGNORECASE), b) for a, b in paths if a != b]
        o, sep = re.escape(old), r"(?:/|\\\\|\\)"
        self.key = [
            re.compile(rf"(?<![\w.-]){o}(?={sep}(?:\d{{3,}}|W\d+)_)"),     # an item id, or its file under a folder
            re.compile(rf"(?<![\w-]){o}(?=__)"),                            # a file named for one of its items
            re.compile(rf"(?<=__){o}(?=__)"),                               # a cancelled item's file
            re.compile(rf"(?<=`){o}(?=[`/])"),                              # `old` and `old/...` in the digests
            re.compile(rf"(?<![\w.-])((?:queue|running|backlog|done|proposed|receipts){sep}){o}(?![\w-])"),
        ]

    def text(self, s: str) -> str:
        for rx, b in self.literal:
            s = rx.sub(b.replace("\\", "\\\\"), s)
        for rx in self.key:
            s = rx.sub(lambda m: (m.group(1) if rx.groups else "") + self.new, s)
        return s

    def value(self, v):
        """A JSON value, walked, keys too: a string equal to the key is the key."""
        if isinstance(v, str):
            return self.new if v == self.old else self.text(v)
        if isinstance(v, list):
            return [self.value(x) for x in v]
        if isinstance(v, dict):
            return {self.value(k): self.value(x) for k, x in v.items()}
        return v

    def name(self, n: str) -> str:
        if n.startswith(self.old + "__"):
            return self.new + n[len(self.old):]
        return n.replace(f"__{self.old}__", f"__{self.new}__")


def busy(cfg, old: str) -> list[str]:
    """Why the repo can't be renamed now: queued, staged or running work, or worktrees."""
    out = []
    for where in ("queue", "running"):
        d = cfg.path(where, old)
        if d.is_dir() and (names := sorted(p.name for p in d.iterdir())):
            out.append(f"{where}/{old} holds {', '.join(names)}")
    wt = cfg.worktree_root / old
    if wt.is_dir() and (names := sorted(p.name for p in wt.iterdir())):
        out.append(f"worktrees under {wt}: {', '.join(names)} (land or discard them first)")
    return out


def _toml_path(control: Path, target: Path) -> str:
    try:
        return Path(os.path.relpath(target, control)).as_posix()
    except ValueError:                       # another drive
        return target.as_posix()


def _rewrite_toml(text: str, old: str, new: str, old_value: str | None, new_value: str | None) -> str:
    o = re.escape(old)
    text = re.sub(rf"^(\s*)\[repos\.(?:{o}|\"{o}\")\]", rf"\1[repos.{new}]", text, flags=re.MULTILINE)
    text = re.sub(rf"^(\s*)(?:{o}|\"{o}\")(\s*=)", rf"\g<1>{new}\2", text, flags=re.MULTILINE)
    text = re.sub(rf"(\brepo\s*=\s*)\"{o}\"", rf'\1"{new}"', text)
    if old_value and new_value and old_value != new_value:
        text = re.sub(rf"([\"']){re.escape(old_value)}\1", lambda m: f"{m[1]}{new_value}{m[1]}", text)
    return text


def _front_matter(text: str, old: str, new: str) -> str:
    """An item file's `repo:` line, inside its front matter only."""
    if not text.startswith("---\n") or (end := text.find("\n---", 4)) < 0:
        return text
    head = re.sub(rf"^repo:\s*([\"']?){re.escape(old)}\1\s*$", f"repo: {new}", text[:end], flags=re.MULTILINE)
    return head + text[end:]


def _json_text(rk: Rekey, text: str, indent: int | None) -> str:
    """A JSON file or line with the repo rekeyed; byte for byte as it was when nothing changes."""
    try:
        data = json.loads(text)
    except ValueError:
        return rk.text(text)
    if (out := rk.value(data)) == data:
        return text
    end = "\n" if text.endswith("\n") else ""
    return json.dumps(out, indent=indent, ensure_ascii=False) + end


def _files(root: Path):
    return (p for p in root.rglob("*") if p.is_file())


def rename(control: Path, old: str, new: str, path: str | None = None) -> list[str]:
    """Do the rename; returns what was done, one line each. RenameError when it can't."""
    cfg = config_mod.load(control)
    control = cfg.control
    if old not in cfg.repos:
        raise RenameError(f"no repo {old!r} in {CONFIG_NAME} (repos: {', '.join(sorted(cfg.repos))})")
    if new == old:
        raise RenameError("the new name is the old one")
    try:
        config_mod.check_repo_name(new)
    except ConfigError as exc:
        raise RenameError(str(exc)) from None
    if new in cfg.repos or new in cfg.locks:
        raise RenameError(f"{new!r} is already a repo or lock name")
    if state.daemon_info(cfg).alive:
        raise RenameError("the daemon is running; stop it first (the app's Stop daemon, or `stop: daemon`)")
    if why := busy(cfg, old):
        raise RenameError("; ".join(why))
    for d in ITEM_DIRS + ("receipts",):
        if cfg.path(d, new).exists():
            raise RenameError(f"{d}/{new} already exists")
    done: list[str] = []

    # The checkout: moved when it is still at the old place, and its worktrees repaired.
    old_path, new_path = cfg.repos[old], cfg.repos[old]
    if path:
        new_path = (Path(path) if Path(path).is_absolute() else Path.cwd() / path).resolve()
        if new_path != old_path:
            if old_path.exists() and new_path.exists():
                raise RenameError(f"both {old_path} and {new_path} exist; move the checkout by hand")
            if old_path.exists():
                retry_io(os.rename, old_path, new_path)
                done.append(f"moved {old_path} -> {new_path}")
            elif not new_path.exists():
                raise RenameError(f"neither {old_path} nor {new_path} exists")
    if (new_path / ".git").exists():
        git.run(new_path, "worktree", "repair", check=False)
        git.run(new_path, "worktree", "prune")
    wt_old, wt_new = cfg.worktree_root / old, cfg.worktree_root / new
    if wt_old.is_dir():
        if wt_new.exists():
            wt_old.rmdir()
        else:
            retry_io(os.rename, wt_old, wt_new)

    raw = read_text(control / CONFIG_NAME)
    table = re.search(rf"^\s*(?:{re.escape(old)}|\"{re.escape(old)}\")\s*=\s*\"([^\"]+)\"", raw, re.MULTILINE) or \
        re.search(rf"\[repos\.{re.escape(old)}\][^\[]*?^\s*path\s*=\s*\"([^\"]+)\"", raw, re.MULTILINE | re.DOTALL)
    old_value = table[1] if table else None
    new_value = _toml_path(control, new_path) if path else old_value
    pairs = [(f, t) for a, b in ((old_path, new_path), (wt_old, wt_new)) for f, t in zip(_path_forms(a), _path_forms(b))]
    if old_value and new_value:
        pairs.append((old_value, new_value))
    rk = Rekey(old, new, pairs)

    # Folders and files named for the repo.
    for d in ITEM_DIRS:
        if (src := cfg.path(d, old)).is_dir():
            retry_io(os.rename, src, cfg.path(d, new))
    for src in sorted((p for p in cfg.path("receipts").rglob(old) if p.is_dir()), key=lambda p: -len(p.parts)):
        retry_io(os.rename, src, src.with_name(new))
    renamed = 0
    for root in ("reports", "questions", "picks", ".conductor"):
        for p in list(_files(cfg.path(root))):
            if root == ".conductor" and "snapshot" in p.relative_to(cfg.state_dir).parts:
                continue
            if (n := rk.name(p.name)) != p.name or (p.parent.name == "push" and p.name == f"{old}.log"):
                retry_io(os.rename, p, p.with_name(n if n != p.name else f"{new}.log"))
                renamed += 1
        for p in sorted((q for q in cfg.path(root).rglob("*") if q.is_dir()), key=lambda q: -len(q.parts)):
            if (n := rk.name(p.name)) != p.name:
                retry_io(os.rename, p, p.with_name(n))
                renamed += 1
    done.append(f"renamed {renamed} files and folders named for {old}")

    # What conductor wrote about it.
    rewritten = 0
    for root in ITEM_DIRS + ("proposed", "questions", "picks", "receipts", ".conductor"):
        for p in _files(cfg.path(root)):
            if p.suffix not in TEXT_SUFFIXES or (root == ".conductor" and "snapshot" in p.relative_to(cfg.state_dir).parts):
                continue
            text = read_text(p)
            if root in ITEM_DIRS + ("proposed",) and p.suffix == ".md":
                out = _front_matter(text, old, new)
            elif p.suffix == ".json":
                out = _json_text(rk, text, indent=1)
            elif p.suffix == ".jsonl":
                out = "".join(_json_text(rk, line, indent=None) for line in text.splitlines(keepends=True))
            else:
                out = rk.text(text)
            if out != text:
                write_text(p, out)
                rewritten += 1
    for name in DIGESTS:
        if (p := control / name).is_file() and (out := rk.text(t := read_text(p))) != t:
            write_text(p, out)
            rewritten += 1
    done.append(f"rewrote {rewritten} files")
    done.append(f"rewrote {_rekey_events(cfg.events_db, rk)} events")

    # The tracked files, committed by conductor, and a fresh guard snapshot so the next
    # start doesn't log its own commit as drift.
    toml = _rewrite_toml(raw, old, new, old_value, new_value)
    write_text(control / CONFIG_NAME, toml)
    try:
        config_mod.load(control)
    except ConfigError as exc:
        write_text(control / CONFIG_NAME, raw)
        raise RenameError(f"the rewritten {CONFIG_NAME} does not load ({exc}); left as it was") from None
    tracked = [CONFIG_NAME]
    if (sm := control / "state.md").is_file() and (out := rk.text(t := read_text(sm))) != t:
        write_text(sm, out)
        tracked.append("state.md")
    git.run(control, "add", "--", *tracked)
    git.run(control, "commit", "-q", "-m", f"rename repo {old} -> {new}", "--", *tracked)
    sha = git.head(control)
    done.append(f"committed {', '.join(tracked)} ({sha[:10]})")
    if guard.load(cfg.state_dir) is not None:
        guard.take(control, cfg.state_dir, stamp())
    log = EventLog(cfg.events_db)
    try:
        log.append("repo_renamed", repo=new, paths=tracked, old=old, new=new, path=str(new_path),
                   moved=new_path != old_path, sha=sha, by="cli")
    finally:
        log.close()
    return done


def _rekey_events(db: Path, rk: Rekey) -> int:
    """Rewrite every row naming the repo, in one transaction, past the append-only
    triggers (dropped and restored inside it)."""
    conn = sqlite3.connect(str(db), isolation_level=None, timeout=30)
    try:
        triggers = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'events'").fetchall()
        changes = []
        for id_, item, repo, paths, detail in conn.execute("SELECT id, item, repo, paths, detail FROM events"):
            row = (rk.value(item) if item else item, rk.new if repo == rk.old else repo,
                   json.dumps(rk.value(json.loads(paths))), json.dumps(rk.value(json.loads(detail)), default=str))
            if row[0] != item or row[1] != repo or row[2] != json.dumps(json.loads(paths)) \
                    or row[3] != json.dumps(json.loads(detail), default=str):
                changes.append((*row, id_))
        if changes:
            conn.execute("BEGIN IMMEDIATE")
            for (sql,) in triggers:
                name = re.search(r"TRIGGER\s+(?:IF NOT EXISTS\s+)?(\w+)", sql, re.IGNORECASE)[1]
                conn.execute(f"DROP TRIGGER {name}")
            conn.executemany("UPDATE events SET item = ?, repo = ?, paths = ?, detail = ? WHERE id = ?", changes)
            for (sql,) in triggers:
                conn.execute(sql)
            conn.execute("COMMIT")
        return len(changes)
    finally:
        conn.close()
