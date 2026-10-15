"""Requests: `requests/<stamp>/` = whole replacement files + `request.md`.

The only way a tracked control file changes. Conductor copies the files in, commits
exactly those paths, logs it, and removes the request.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import git
from .config import RUNTIME, Config
from .util import FrontMatterError, read_text, split_front_matter

SETTLE_SECONDS = 2.0      # a request is applied only once its files stop changing
FAILED_SUFFIX = ".failed"


class RequestError(ValueError):
    pass


@dataclass
class Request:
    dir: Path
    message: str
    targets: list[str]


def pending(cfg: Config) -> list[Path]:
    root = cfg.path("requests")
    if not root.is_dir():
        return []
    return sorted(d for d in root.iterdir()
                  if d.is_dir() and not d.name.endswith(FAILED_SUFFIX) and (d / "request.md").is_file())


def settled(d: Path) -> bool:
    newest = max((p.stat().st_mtime for p in d.rglob("*") if p.is_file()), default=0)
    return time.time() - newest >= SETTLE_SECONDS


def _check_target(t: str) -> str:
    p = PurePosixPath(t.replace("\\", "/"))
    if p.is_absolute() or ":" in t or ".." in p.parts or not p.parts:
        raise RequestError(f"target {t!r} must be a relative path inside the control folder")
    if p.parts[0] in RUNTIME or p.parts[0] == ".git":
        raise RequestError(f"target {t!r} is runtime state or git internals, not a tracked file")
    return p.as_posix()


def parse(d: Path) -> Request:
    try:
        meta, body = split_front_matter(read_text(d / "request.md"))
    except (FrontMatterError, UnicodeDecodeError) as exc:
        raise RequestError(f"request.md: {exc}") from exc
    targets = meta.get("targets")
    if isinstance(targets, str):
        targets = [targets]
    if not isinstance(targets, list) or not targets or not all(isinstance(t, str) for t in targets):
        raise RequestError("request.md front-matter needs `targets:` (a list of paths)")
    targets = [_check_target(t) for t in targets]
    message = str(meta.get("message") or body).strip()
    if not message:
        raise RequestError("request.md needs a commit message (`message:` or the body)")
    present = {p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file()} - {"request.md"}
    missing = sorted(set(targets) - present)
    if missing:
        raise RequestError(f"targets listed but not in the request folder: {missing}")
    extra = sorted(present - set(targets))
    if extra:
        raise RequestError(f"files in the request folder not listed as targets: {extra}")
    return Request(d, message, targets)


def _normalize(data: bytes) -> bytes:
    """UTF-8 text is written with LF endings; anything else is copied byte for byte."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    return text.replace("\r\n", "\n").encode("utf-8")


def apply(cfg: Config, req: Request) -> str | None:
    """Copy and commit. Returns the new commit sha, or None when nothing changed."""
    control = cfg.control
    for t in req.targets:
        dest = control / t
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_normalize((req.dir / t).read_bytes()))
    git.run(control, "add", "--", *req.targets)
    staged = git.run(control, "diff", "--cached", "--name-only", "--", *req.targets).split()
    if not staged:
        return None
    git.run(control, "commit", "-q", "-F", "-", "--", *req.targets, input=req.message.encode("utf-8"))
    return git.head(control)
