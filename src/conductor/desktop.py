"""The `open:` verbs: a fixed set of desktop actions, each one named thing done to one
checked path or URL. Not a command runner.

    folder <path>   the folder in File Explorer
    reveal <path>   Explorer at the file's folder, the file selected
    file <path>     the file in its default app (programs and scripts refused)
    text <path>     the file in Notepad, never run (a script included)
    url <url>       the default browser (https://, or http:// on localhost only)

A path must resolve, junctions and `..` followed, inside the control folder, a
configured repo, the worktree root, conductor's own folder or an `open_roots` folder.
A refusal is an `open_refused` event naming the reason; a launch is `opened`. Each
launch goes through the detached launcher, so nothing waits on the window.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import urllib.parse
from pathlib import Path

from . import procs
from .config import Config

VERBS = ("folder", "reveal", "file", "text", "url")

# Refused by `file` whatever Windows says: programs, scripts, installers, shortcuts.
PROGRAM_EXTS = {
    ".exe", ".com", ".scr", ".pif", ".cpl", ".msi", ".msp", ".msc", ".bat", ".cmd", ".ps1", ".psm1",
    ".py", ".pyw", ".js", ".jse", ".vbs", ".vbe", ".wsf", ".wsh", ".hta", ".jar", ".reg", ".inf",
    ".lnk", ".url", ".scf", ".appref-ms", ".application", ".sh",
}
LOOPBACK = {"localhost", "127.0.0.1", "::1"}

# Run detached by `spawn_detached`: the one call that shows the window, then exit.
_OPEN = """\
import json, os, subprocess, sys
a = json.loads(sys.argv[1])
if a["how"] == "startfile":
    os.startfile(a["target"])
else:
    subprocess.Popen(a["target"])   # a Windows command line (no shell), or an argv list
"""


class Refused(ValueError):
    pass


def roots(cfg: Config) -> list[Path]:
    """Every folder a path may resolve inside, as it resolves now (missing ones left out)."""
    here = Path(__file__).resolve().parents[2]          # conductor's checkout
    out = [cfg.control, *cfg.repos.values(), *([cfg.worktree_root] if cfg.worktree_root else []),
           *([here] if (here / "pyproject.toml").is_file() else []), *cfg.open_roots]
    return [p.resolve() for p in out if p.is_dir()]


def _inside(path: Path, root: Path) -> bool:
    p, r = os.path.normcase(str(path)), os.path.normcase(str(root))
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


def program_reason(path: Path) -> str | None:
    """Why `path` counts as a program or script, or None."""
    ext = path.suffix.lower()
    if ext in PROGRAM_EXTS:
        return f"{ext} is a program or script"
    if procs.WINDOWS:
        pathext = {e.strip().lower() for e in os.environ.get("PATHEXT", "").split(";") if e.strip()}
        if ext in pathext:
            return f"{ext} is executable (PATHEXT)"
        if ext and _assoc_is_dangerous(ext):
            return f"Windows treats {ext} as unsafe to open (AssocIsDangerous)"
    elif os.access(path, os.X_OK):
        return "the file is executable"
    return None


def _assoc_is_dangerous(ext: str) -> bool:
    try:
        import ctypes
        fn = ctypes.windll.shlwapi.AssocIsDangerous
        fn.argtypes, fn.restype = [ctypes.c_wchar_p], ctypes.c_int
        return bool(fn(ext))
    except (OSError, AttributeError):
        return False


def check_path(cfg: Config, verb: str, value: str) -> Path:
    """The resolved path `verb` may act on, or `Refused`."""
    raw = value.strip().strip('"')
    if not raw:
        raise Refused("no path given")
    p = Path(raw)
    try:
        p = (p if p.is_absolute() else cfg.control / p).resolve(strict=True)
    except (OSError, RuntimeError):
        raise Refused(f"{raw} does not exist") from None
    if not any(_inside(p, r) for r in roots(cfg)):
        raise Refused(f"{p} is outside the control folder, the repos, the worktree root, conductor "
                      "and every open_roots folder")
    if verb == "folder" and not p.is_dir():
        raise Refused(f"{p} is a file, not a folder (`open: reveal` shows it in its folder)")
    if verb in ("file", "text") and not p.is_file():
        raise Refused(f"{p} is a folder, not a file (`open: folder` opens it)")
    if verb == "file" and (why := program_reason(p)):
        raise Refused(f"{p.name}: {why}; `open: text` shows it without running it")
    return p


def check_url(value: str) -> str:
    url = value.strip()
    if not url or any(c.isspace() or ord(c) < 32 for c in url):
        raise Refused(f"{url!r} is not one URL")
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        parts.port                                      # a bad port raises
    except ValueError as exc:
        raise Refused(f"{url}: {exc}") from None
    if parts.username or parts.password:
        raise Refused(f"{url}: a URL carrying a user name or password is refused")
    scheme = parts.scheme.lower()
    if not (scheme == "https" and host) and not (scheme == "http" and host in LOOPBACK):
        raise Refused(f"{url}: only https:// URLs and http://localhost URLs are allowed")
    return url


def launch_spec(verb: str, target: str) -> dict:
    """How the detached helper shows `target`: `startfile`, or a command to start."""
    if procs.WINDOWS:
        match verb:
            case "folder":
                return {"how": "run", "target": f'explorer.exe "{target}"'}
            case "reveal":
                return {"how": "run", "target": f'explorer.exe /select,"{target}"'}
            case "text":
                return {"how": "run", "target": f'notepad.exe "{target}"'}
        return {"how": "startfile", "target": target}
    mac = sys.platform == "darwin"
    opener = "open" if mac else (shutil.which("xdg-open") or "xdg-open")
    match verb:
        case "reveal":
            return {"how": "run", "target": ["open", "-R", target] if mac else [opener, str(Path(target).parent)]}
        case "text":
            return {"how": "run", "target": ["open", "-t", target] if mac else [opener, target]}
    return {"how": "run", "target": [opener, target]}


class Opener:
    """Carries out `open:` verbs and logs each: `opened` or `open_refused`."""

    def __init__(self, cfg: Config, log, launch=None):
        """`launch(spec)` shows one thing (see `launch_spec`); default: the detached helper."""
        self.cfg, self.log, self.launch = cfg, log, launch or self._launch

    def _launch(self, spec: dict) -> None:
        procs.spawn_detached([sys.executable, "-c", _OPEN, json.dumps(spec)], self.cfg.control,
                             self.cfg.state_dir / "open.log")

    def open(self, verb: str, value: str, by: str | None = None, rel: str | None = None) -> bool:
        extra = {"by": by} if by else {}
        paths = [rel] if rel else []
        try:
            if verb not in VERBS:
                raise Refused(f"unknown verb {verb!r} (known: {', '.join(VERBS)})")
            target = check_url(value) if verb == "url" else str(check_path(self.cfg, verb, value))
        except Refused as exc:
            self.log.append("open_refused", paths=paths, verb=verb, target=value, reason=str(exc), **extra)
            return False
        try:
            self.launch(launch_spec(verb, target))
        except OSError as exc:
            self.log.append("warning", paths=paths, verb=verb, target=target,
                            note=f"open: {verb} {target} could not launch: {exc}", **extra)
            return False
        where = {"url": target} if verb == "url" else {"path": target}
        if verb != "url":
            paths.append(self.cfg.rel(Path(target)))
        self.log.append("opened", paths=paths, verb=verb, **where, **extra)
        return True
