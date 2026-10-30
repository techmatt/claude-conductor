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

REQUEST_WAIT_SECONDS = 60.0  # how long after request.md appears its missing targets are waited for
# A request whose every target is identical to HEAD waits this long after request.md for a
# changed copy: the bridge can land a target's content after the call that wrote it returned.
SAME_WAIT_SECONDS = 10.0
FRESH_SECONDS = 2.0       # a request.md this young that does not parse may still be being written
FAILED_SUFFIX = ".failed"
STALE_SECONDS = 3600.0    # a request folder with no request.md, untouched this long, moves to requests/stale/
STALE_DIR = "stale"


def is_closeout(message: str) -> bool:
    """A planner session's closeout (`message: "closeout: …"`): what it saw is read."""
    return message.strip().lower().startswith("closeout:")


class RequestError(ValueError):
    pass


class MissingTargets(RequestError):
    """Targets named in request.md not in the folder yet: waited for, then a failure."""

    def __init__(self, missing: list[str], waited: float | None = None):
        self.missing = missing
        after = f" (waited {waited:.0f}s)" if waited is not None else ""
        super().__init__(f"targets missing: {', '.join(missing)}{after}; nothing was applied")


@dataclass
class Request:
    dir: Path
    message: str
    targets: list[str]
    identical: bool = False     # every target matched HEAD when it was applied


@dataclass
class Waiting:
    """A request not ready yet: what it waits for (a pending receipt says it) and how long
    `request.md` has waited."""
    why: str
    waited: float


def _written(d: Path) -> float:
    try:
        return (d / "request.md").stat().st_mtime
    except OSError:
        return float("inf")


def pending(cfg: Config) -> list[Path]:
    """Waiting requests in write order (`request.md`'s mtime, then name): a stamp is the
    writer's guess at the clock, and one written ahead of it must not jump a later one."""
    root = cfg.path("requests")
    if not root.is_dir():
        return []
    found = [d for d in root.iterdir()
             if d.is_dir() and not d.name.endswith(FAILED_SUFFIX) and (d / "request.md").is_file()]
    return sorted(found, key=lambda d: (_written(d), d.name))


def move_stale(cfg: Config) -> list[tuple[str, Path, list[str]]]:
    """Move each request folder that has no request.md and nothing in it touched for
    STALE_SECONDS to `requests/stale/` (the planner can't delete over the bridge); for
    each, (its name, where it went, its files)."""
    root = cfg.path("requests")
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name == STALE_DIR or d.name.endswith(FAILED_SUFFIX) or (d / "request.md").exists():
            continue
        try:
            files = [p for p in d.rglob("*") if p.is_file()]
            if time.time() - max(p.stat().st_mtime for p in [d, *files]) < STALE_SECONDS:
                continue
            dest = root / STALE_DIR / d.name
            n = 1
            while dest.exists():
                n += 1
                dest = root / STALE_DIR / f"{d.name}-{n}"
            dest.parent.mkdir(exist_ok=True)
            d.rename(dest)
        except OSError:
            continue                    # held open: the next tick tries again
        out.append((d.name, dest, sorted(p.relative_to(d).as_posix() for p in files)))
    return out


def readable(d: Path, names: list[str]) -> bool:
    """Every file opens and reads: on Windows a file still held open by its writer does not."""
    try:
        for n in names:
            (d / n).read_bytes()
    except OSError:
        return False
    return True


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


def ready(d: Path, control: Path | None = None) -> Request | Waiting:
    """The request once request.md and every target it lists are present and readable, else
    what it still waits for. No settle timer: the planner writes request.md last.

    Missing targets are waited for until REQUEST_WAIT_SECONDS after request.md's mtime (so the
    wait survives a restart), then fail (`targets missing: …`). With `control`, a request whose
    every target is identical to HEAD there waits until SAME_WAIT_SECONDS for a changed copy
    to land, then is ready with `identical` set. A request.md that does not parse is waited
    for while it is younger than FRESH_SECONDS (half written); any other parse error fails at once.
    """
    waited = time.time() - _written(d)
    try:
        if not readable(d, ["request.md"]):
            return Waiting("request.md to be readable", waited)
        req = parse(d)
    except MissingTargets as exc:
        if waited < REQUEST_WAIT_SECONDS:
            return Waiting(f"targets missing: {', '.join(exc.missing)}", waited)
        raise MissingTargets(exc.missing, waited) from None
    except RequestError:
        if waited < FRESH_SECONDS:
            return Waiting("request.md to be complete", waited)
        raise
    if not readable(d, req.targets):
        return Waiting("its targets to be readable", waited)
    if control is not None and identical(control, req):
        if waited < SAME_WAIT_SECONDS:
            return Waiting(f"a changed copy: every target is identical to HEAD so far ({', '.join(req.targets)})", waited)
        req.identical = True
    return req


def identical(control: Path, req: Request) -> bool:
    """Every target, as `apply` would write it, matches the control folder's copy: HEAD's,
    since the tamper guard keeps tracked files level with it. No git call: this runs on
    every waiting request each tick."""
    try:
        return all(_normalize((req.dir / t).read_bytes()) == _normalize((control / t).read_bytes())
                   for t in req.targets)
    except OSError:
        return False            # a new file, or one unreadable: not identical


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
