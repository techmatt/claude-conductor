"""The farm tree (130): a pinned copy of the code a job's key names, where every step of a
farm runs: `native_build`, the fingerprint, the native shards (main box and local-remote
workers) and the wasm build. The item's own working tree is read once, at the farm's start.

Why: each step used to read the working tree while the executor kept editing it.
aicomplete's bundle `32fccf1` (263, 6:09pm Nov 17) compiled configs that had
`city_cut_at` with code that did not (the commit had neither), so Node and the browser both
failed it; a parameter added mid-farm failed a whole farm's native shards (263); and a build
beside a farmed read failed with "Access is denied" on the `target/release` exe the shards
held (167, 260). A tree's build output (`target/`) stays in the tree, so a build there is
incremental and the item's own exe is never locked by a farm.

Layout, under `.conductor/remote/trees/`: `<n>/` is a copy of the repo's files (tracked,
plus untracked and unignored: the set `client.repo_key` hashes); `<n>.json` its key, the
files it copied (`rel -> [size, mtime_ns, sha256]` of the source) and its holders (pid ->
create time). A tree is shared by every holder of its key; a tree no live process holds is
synced to a new key (only changed files are copied, with a fresh mtime, so a build sees
them); none free: a new one. `trees.lock` serialises taking and releasing."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

import psutil

TREES = "trees"
LOCK = "trees.lock"
LOCK_STALE_SECONDS = 600        # a lock this old was left by a dead process
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


class TreeError(OSError):
    pass


def _alive(pid: int, created: float | None) -> bool:
    try:
        p = psutil.Process(int(pid))
        return created is None or abs(p.create_time() - float(created)) < 1.0
    except (psutil.Error, ValueError, TypeError):
        return False


def _created(pid: int) -> float | None:
    try:
        return psutil.Process(pid).create_time()
    except psutil.Error:
        return None


class _Lock:
    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > LOCK_STALE_SECONDS:
                        self.path.unlink(missing_ok=True)
                        continue
                except OSError:
                    continue
                time.sleep(0.1)

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def source_files(root: Path) -> list[str]:
    """The repo's files as `repo_key` sees them: tracked, plus untracked and unignored."""
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"], capture_output=True,
                         check=True, **NO_WINDOW).stdout.decode("utf-8", errors="replace")
    return sorted({f for f in out.split("\0") if f and (root / f).is_file()})


def _read(meta: Path) -> dict:
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(meta: Path, data: dict) -> None:
    tmp = meta.with_name(meta.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, meta)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def sync(root: Path, tree: Path, files: dict, listing: list[str] | None = None) -> tuple[dict, int]:
    """Make `tree` hold `root`'s files as they are now (`listing`, else `source_files`). `files`
    is what the tree copied last time; returns the new map and how many files were copied or removed."""
    new, changed = {}, 0
    for rel in (source_files(root) if listing is None else listing):
        src, dst = root / rel, tree / rel
        try:
            st = src.stat()
        except OSError:
            continue
        old = files.get(rel)
        # An extracted commit's mtimes are its commit time (`git archive`), so a listing is hashed.
        if listing is None and old and old[0] == st.st_size and old[1] == st.st_mtime_ns and dst.is_file():
            new[rel] = old
            continue
        sha = _sha(src)
        if not (old and old[2] == sha and dst.is_file()):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)            # a fresh mtime, never the source's: a build must see it changed
            changed += 1
        new[rel] = [st.st_size, st.st_mtime_ns, sha]
    for rel in set(files) - set(new):
        try:
            (tree / rel).unlink()
            changed += 1
        except OSError:
            pass
    return new, changed


def extract(root: Path, rev: str, dest: Path) -> list[str]:
    """`rev`'s committed files (`git archive`) unpacked under `dest`; returns their paths.
    `farm-seeds --at` (133): a paired read's older side farms from its own commit."""
    data = subprocess.run(["git", "-C", str(root), "archive", "--format=tar", rev], capture_output=True,
                          check=True, **NO_WINDOW).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter="data")
        return sorted(m.name for m in tar.getmembers() if m.isfile())


def take(state: Path, root: Path, key: str, pid: int | None = None, rev: str | None = None) -> tuple[Path, int]:
    """A tree holding `key`'s code, held for `pid` (this process by default) until `release`.
    Returns the tree (the repo root's copy) and the files copied into it now. With `rev`, the
    code is that commit's, not the working tree's."""
    if rev:
        state.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix="rev-", dir=state))
        try:
            try:
                listing = extract(root, rev, tmp)
            except (OSError, subprocess.CalledProcessError, tarfile.TarError) as exc:
                raise TreeError(f"commit {rev} could not be read: {exc}") from exc
            return _take(state, tmp, key, pid, listing)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return _take(state, root, key, pid, None)


def _take(state: Path, root: Path, key: str, pid: int | None, listing: list[str] | None) -> tuple[Path, int]:
    pid = pid or os.getpid()
    trees = state / TREES
    with _Lock(state / LOCK):
        metas = sorted((p for p in trees.glob("*.json") if p.stem.isdigit()), key=lambda p: int(p.stem))
        held, free = None, None
        for meta in metas:
            data = _read(meta)
            live = {k: v for k, v in (data.get("holds") or {}).items() if _alive(int(k), v)}
            if data.get("key") == key and (live or (trees / meta.stem).is_dir()):
                held = (meta, data)
                break
            if not live and free is None:
                free = (meta, data)
        if held:
            meta, data = held
        elif free:
            meta, data = free
        else:
            n = int(metas[-1].stem) + 1 if metas else 0
            meta, data = trees / f"{n}.json", {}
        tree = trees / meta.stem
        tree.mkdir(parents=True, exist_ok=True)
        try:
            files, changed = sync(root, tree, data.get("files") or {}, listing)   # the same key: a stat per file, no copy
        except (OSError, subprocess.CalledProcessError) as exc:
            raise TreeError(f"the farm tree {tree} could not be synced: {exc}") from exc
        holds = {k: v for k, v in (data.get("holds") or {}).items() if _alive(int(k), v)}
        holds[str(pid)] = _created(pid)
        _write(meta, {"key": key, "files": files, "holds": holds})
    return tree, changed


def add_args(src: Path, dst: Path, args: list[str], missing_only: bool = False) -> None:
    """An argument naming a file the tree lacks (a spec in a git-ignored folder, say) is copied
    in at the same place, so the native command finds it from the tree as from the repo.
    `missing_only` (`--at`): a file the commit has keeps the commit's version."""
    for a in args:
        try:
            p = (src / a).resolve()
            rel = p.relative_to(src.resolve())
            if missing_only and (dst / rel).exists():
                continue
            if p.is_file() and not ((dst / rel).is_file() and (dst / rel).read_bytes() == p.read_bytes()):
                (dst / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, dst / rel)
        except (OSError, ValueError):
            continue


def hold(tree: Path, pid: int, created: float | None = None) -> None:
    """Another process (a detached bundle build) holds `tree` too."""
    meta = tree.with_name(tree.name + ".json")
    with _Lock(tree.parent.parent / LOCK):
        data = _read(meta)
        data.setdefault("holds", {})[str(pid)] = created if created is not None else _created(pid)
        _write(meta, data)


def release(tree: Path, pid: int | None = None) -> None:
    meta = tree.with_name(tree.name + ".json")
    with _Lock(tree.parent.parent / LOCK):
        data = _read(meta)
        if data:
            (data.get("holds") or {}).pop(str(pid or os.getpid()), None)
            _write(meta, data)
