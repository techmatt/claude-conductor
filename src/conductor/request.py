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
REQUEST_WAIT_SECONDS = 60.0  # how long after request.md appears its missing targets are waited for
FAILED_SUFFIX = ".failed"


def is_closeout(message: str) -> bool:
    """A planner session's closeout (`message: "closeout: …"`): what it saw is read."""
    return message.strip().lower().startswith("closeout:")


class RequestError(ValueError):
    pass


class MissingTargets(RequestError):
    """Targets named in request.md not in the folder yet: waited for, then a failure."""

    def __init__(self, missing: list[str], waited: float | None = None):
        self.missing = missing
        after = f" after waiting {waited:.0f}s" if waited is not None else ""
        super().__init__(f"targets listed but not in the request folder{after}: {missing}")


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


def _read(d: Path) -> tuple[dict, str, list[str]]:
    try:
        meta, body = split_front_matter(read_text(d / "request.md"))
    except (FrontMatterError, UnicodeDecodeError) as exc:
        raise RequestError(f"request.md: {exc}") from exc
    targets = meta.get("targets")
    if isinstance(targets, str):
        targets = [targets]
    if not isinstance(targets, list) or not targets or not all(isinstance(t, str) for t in targets):
        raise RequestError("request.md front-matter needs `targets:` (a list of paths)")
    return meta, body, [_check_target(t) for t in targets]


def listed_targets(d: Path) -> set[str]:
    """The targets request.md names, without checking the folder; empty when it does not parse."""
    try:
        return set(_read(d)[2])
    except (RequestError, OSError):
        return set()


def parse(d: Path) -> Request:
    meta, body, targets = _read(d)
    message = str(meta.get("message") or body).strip()
    if not message:
        raise RequestError("request.md needs a commit message (`message:` or the body)")
    present = {p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file()} - {"request.md"}
    missing = sorted(set(targets) - present)
    if missing:
        raise MissingTargets(missing)
    extra = sorted(present - set(targets))
    if extra:
        raise RequestError(f"files in the request folder not listed as targets: {extra}")
    return Request(d, message, targets)


def ready(d: Path) -> Request | None:
    """The request once settled with every target present; None while it is still being written.

    Missing targets are waited for until REQUEST_WAIT_SECONDS after request.md's mtime (so the
    wait survives a restart), then fail. Any other parse error fails at once.
    """
    if not settled(d):
        return None
    try:
        req = parse(d)
    except MissingTargets as exc:
        waited = time.time() - (d / "request.md").stat().st_mtime
        if waited < REQUEST_WAIT_SECONDS:
            return None
        raise MissingTargets(exc.missing, waited) from None
    # A late target that turned up between the settle check and parse's scan may be half written:
    # its fresh mtime unsettles the folder again, and the request waits for a later tick.
    return req if settled(d) else None


def waiting_for(d: Path) -> str:
    """What a request `ready` returned None for is waiting for, in words (its pending receipt)."""
    try:
        parse(d)
    except MissingTargets as exc:
        return (f"its targets {exc.missing} (until {REQUEST_WAIT_SECONDS:g}s after request.md was "
                "written; then it fails)")
    except (RequestError, OSError):
        pass
    return "its files to settle (still being written)"


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
