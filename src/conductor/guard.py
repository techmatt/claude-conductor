"""Control-folder guard: snapshot tracked files and refs, detect changes, revert them.

The snapshot lives in `.conductor/snapshot/` so it survives a daemon restart: an item
adopted after a restart is checked against the same snapshot it was launched under.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import git


@dataclass
class Snapshot:
    head: str | None
    branch: str | None          # symbolic HEAD (refs/heads/main), None when detached
    refs: dict[str, str]
    tree: str | None            # `git write-tree` of the index
    files: dict[str, str]       # tracked path → sha256 of working-tree bytes ("" = missing)
    blobs: dict[str, str]       # tracked path → copy name under files/
    taken: str

    def to_json(self) -> dict:
        return self.__dict__.copy()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dir(state_dir: Path) -> Path:
    return state_dir / "snapshot"


def take(control: Path, state_dir: Path, taken: str) -> Snapshot:
    """Snapshot the control folder's tracked files and refs; persist it atomically."""
    new = state_dir / "snapshot.new"
    shutil.rmtree(new, ignore_errors=True)
    (new / "files").mkdir(parents=True)
    files, blobs = {}, {}
    for i, rel in enumerate(git.ls_files(control)):
        p = control / rel
        if p.is_file():
            data = p.read_bytes()
            files[rel] = _sha(data)
            blobs[rel] = str(i)
            (new / "files" / str(i)).write_bytes(data)
        else:
            files[rel] = ""
    snap = Snapshot(
        head=git.head(control), branch=git.symbolic_head(control), refs=git.refs(control),
        tree=git.write_tree(control), files=files, blobs=blobs, taken=taken,
    )
    (new / "meta.json").write_text(json.dumps(snap.to_json(), indent=1), encoding="utf-8", newline="\n")
    cur, old = _dir(state_dir), state_dir / "snapshot.old"
    shutil.rmtree(old, ignore_errors=True)
    if cur.exists():
        os.replace(cur, old)
    os.replace(new, cur)
    shutil.rmtree(old, ignore_errors=True)
    return snap


def load(state_dir: Path) -> Snapshot | None:
    """The persisted snapshot; finishes a `take` interrupted between its two renames."""
    cur = _dir(state_dir)
    if not (cur / "meta.json").is_file():
        # meta.json is written last, so a snapshot.new holding it is complete and newest.
        for name in ("snapshot.new", "snapshot.old"):
            cand = state_dir / name
            if (cand / "meta.json").is_file():
                shutil.rmtree(cur, ignore_errors=True)
                os.replace(cand, cur)
                break
        else:
            return None
    return Snapshot(**json.loads((cur / "meta.json").read_text(encoding="utf-8")))


def diff(control: Path, snap: Snapshot) -> list[str]:
    """Human-readable list of differences between the control folder and the snapshot."""
    changes = []
    if git.symbolic_head(control) != snap.branch:
        changes.append(f"HEAD moved to {git.symbolic_head(control) or 'detached'} (was {snap.branch})")
    if git.head(control) != snap.head:
        changes.append(f"new commit: HEAD {(git.head(control) or '-')[:10]} (was {(snap.head or '-')[:10]})")
    now_refs = git.refs(control)
    for ref in sorted(set(now_refs) | set(snap.refs)):
        if now_refs.get(ref) != snap.refs.get(ref) and ref != snap.branch:
            changes.append(f"ref {ref} changed")
    tracked = git.ls_files(control)
    for rel in sorted(set(tracked) - set(snap.files)):
        changes.append(f"{rel}: newly tracked")
    for rel, sha in snap.files.items():
        p = control / rel
        cur = _sha(p.read_bytes()) if p.is_file() else ""
        if cur != sha:
            changes.append(f"{rel}: {'deleted' if not cur else 'modified'}")
    # A staged-only change shows up nowhere else; with any other change it is noise.
    if not changes and git.write_tree(control) != snap.tree:
        changes.append("index changed")
    return changes


def changed_paths(changes: list[str]) -> list[str]:
    return [c.split(":", 1)[0] for c in changes if ":" in c and not c.startswith(("new commit", "HEAD"))]


def named(touched: list[str], changes: list[str]) -> bool:
    """Do these control-folder paths a tool call named (`talk.touch_paths`: relative,
    lower-case, "" the folder itself) cover one of `changes`? A changed file is covered by
    itself or a folder holding it; a change with no path (a commit, a ref, HEAD, the
    index) by the folder itself or `.git`. A bare folder mention never covers a file:
    `cd <control> && …` proves nothing about which file."""
    files = [p.lower() for p in changed_paths(changes)]
    pathless = len(files) < len(changes)
    for t in touched:
        if t in ("", ".git") or t.startswith(".git/"):
            if pathless:
                return True
        elif any(f == t or f.startswith(t + "/") for f in files):
            return True
    return False


def restore(control: Path, state_dir: Path, snap: Snapshot) -> None:
    """Put refs, HEAD, index and every tracked file back exactly as snapshotted."""
    now_refs = git.refs(control)
    for ref, sha in snap.refs.items():
        if now_refs.get(ref) != sha:
            git.run(control, "update-ref", ref, sha)
    for ref in set(now_refs) - set(snap.refs):
        git.run(control, "update-ref", "-d", ref)
    if snap.branch:
        git.run(control, "symbolic-ref", "HEAD", snap.branch)
    elif snap.head:
        git.run(control, "update-ref", "--no-deref", "HEAD", snap.head)
    if snap.head:
        git.run(control, "reset", "-q", "--hard", snap.head)
    if snap.tree:
        git.run(control, "read-tree", snap.tree)
    files_dir = _dir(state_dir) / "files"
    for rel, sha in snap.files.items():
        p = control / rel
        if not sha:
            if p.exists():
                p.unlink()
            continue
        data = (files_dir / snap.blobs[rel]).read_bytes()
        if not p.is_file() or _sha(p.read_bytes()) != sha:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
