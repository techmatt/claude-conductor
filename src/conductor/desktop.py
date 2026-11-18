"""The `open:` verbs: a fixed set of desktop actions, each one named thing done to one
checked path or URL. Not a command runner.

    folder <path>   the folder in File Explorer
    reveal <path>   Explorer at the file's folder, the file selected
    file <path>     the file in its default app (programs and scripts refused); with a
                    `?query` or `#fragment` after it, the page as a file:/// URL in the browser
    text <path>     the file in Notepad, never run (a script included)
    url <url>       the default browser (https://, http:// on localhost, or a file:/// URL
                    whose file passes `file`'s checks)

A path must resolve, junctions and `..` followed, inside the control folder, a
configured repo, the worktree root, conductor's own folder or an `open_roots` folder.
A refusal is an `open_refused` event naming the reason and ending with the nearest form
that would be accepted (129); a launch is `opened`. Each launch goes through the detached
launcher, so nothing waits on the window.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import urllib.parse
import urllib.request
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
elif a["how"] == "browser":
    import webbrowser
    webbrowser.open(a["target"])
else:
    subprocess.Popen(a["target"])   # a Windows command line (no shell), or an argv list
"""


class Refused(ValueError):
    pass


CHECKOUT = Path(__file__).resolve().parents[2]          # conductor's checkout (tests point it away: 103)


def roots(cfg: Config) -> list[Path]:
    """Every folder a path may resolve inside, as it resolves now (missing ones left out)."""
    here = CHECKOUT
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


def _resolve(cfg: Config, raw: str) -> Path | None:
    p = Path(raw)
    try:
        return (p if p.is_absolute() else cfg.control / p).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None


def _nearest_folder(cfg: Config, raw: str) -> Path | None:
    """The deepest existing folder on `raw`'s way, for a refusal's hint."""
    p = Path(raw) if Path(raw).is_absolute() else cfg.control / raw
    for q in p.parents:
        try:
            if q.is_dir():
                return q.resolve()
        except OSError:
            continue
    return None


def split_query(cfg: Config, raw: str) -> tuple[str, str]:
    """`page.html?x=1#top` → (`page.html`, `?x=1#top`) when the whole names no file and the
    part before the first `?` (else `#`) does; otherwise (raw, "")."""
    if _resolve(cfg, raw) is not None:
        return raw, ""
    for mark in "?#":
        head, sep, tail = raw.partition(mark)
        if sep and head and _resolve(cfg, head) is not None:
            return head, sep + tail
    return raw, ""


def check_path(cfg: Config, verb: str, value: str) -> Path:
    """The resolved path `verb` may act on, or `Refused` ending with the nearest form that
    would be accepted (129)."""
    raw = value.strip().strip('"')
    if not raw:
        raise Refused("no path given; use `open: <verb> <path>`")
    p = _resolve(cfg, raw)
    if p is None:
        near = _nearest_folder(cfg, raw)
        hint = f"; `open: folder {near}` shows the nearest folder that exists" if near else ""
        raise Refused(f"{raw} does not exist{hint}")
    if not any(_inside(p, r) for r in roots(cfg)):
        raise Refused(f"{p} is outside the control folder, the repos, the worktree root, conductor "
                      "and every open_roots folder; adding its folder to `open_roots` in conductor.toml "
                      "(by request) makes it openable")
    if verb == "folder" and not p.is_dir():
        raise Refused(f"{p} is a file, not a folder (`open: reveal` shows it in its folder)")
    if verb in ("file", "text") and not p.is_file():
        raise Refused(f"{p} is a folder, not a file (`open: folder` opens it)")
    if verb == "file" and (why := program_reason(p)):
        raise Refused(f"{p.name}: {why}; `open: text` shows it without running it")
    return p


def check_url(value: str, cfg: Config | None = None) -> str:
    """The URL `url` may open, or `Refused`. A file:/// URL (with `cfg`) is `open: file`'s
    page: its file must pass `check_path(cfg, "file", ...)`."""
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
    if scheme == "file" and cfg is not None and not host:
        page = check_path(cfg, "file", urllib.request.url2pathname(parts.path))
        return page.as_uri() + (f"?{parts.query}" if parts.query else "") + (
            f"#{parts.fragment}" if parts.fragment else "")
    if not (scheme == "https" and host) and not (scheme == "http" and host in LOOPBACK):
        raise Refused(f"{url}: only https:// URLs and http://localhost URLs are allowed; a local page "
                      "is `open: file <path>` (`?<query>` or `#<fragment>` after it if needed), a local "
                      "server `run: <target>`")
    return url


def file_target(cfg: Config, value: str) -> tuple[Path, str]:
    """`open: file`'s checked path, and the `?query`/`#fragment` written after it (129), which
    makes the target its file:/// URL in the browser."""
    raw, query = split_query(cfg, value.strip().strip('"'))
    return check_path(cfg, "file", raw), query


def browser_command(url: str) -> str | None:
    """The default browser's command line for `url`, from Windows' https association, or
    None. `os.startfile` on a file:/// URL drops its query (it opens the file), so a page
    with one goes to the browser itself."""
    if not procs.WINDOWS:
        return None
    try:
        import winreg
        key = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            progid = winreg.QueryValueEx(k, "ProgId")[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{progid}\shell\open\command") as k:
            cmd = winreg.QueryValueEx(k, "")[0]
    except (OSError, ImportError):
        return None
    return cmd.replace("%1", url) if "%1" in cmd else None


def launch_spec(verb: str, target: str) -> dict:
    """How the detached helper shows `target`: `startfile`, a command to start, or (verb
    `page`: a file:/// URL carrying a query or fragment) the browser."""
    if verb == "page":
        cmd = browser_command(target)
        return {"how": "run", "target": cmd} if cmd else {"how": "browser", "target": target}
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
        local: Path | None = None           # the file a page URL names
        try:
            if verb not in VERBS:
                raise Refused(f"unknown verb {verb!r} (known: {', '.join(VERBS)}); "
                              "use `open: <verb> <path or URL>`")
            if verb == "url":
                target = check_url(value, self.cfg)
                if target.startswith("file:"):
                    local = Path(urllib.request.url2pathname(urllib.parse.urlsplit(target).path))
            elif verb == "file":
                local, query = file_target(self.cfg, value)
                target = local.as_uri() + query if query else str(local)
            else:
                target = str(check_path(self.cfg, verb, value))
        except Refused as exc:
            self.log.append("open_refused", paths=paths, verb=verb, target=value, reason=str(exc), **extra)
            return False
        page = target.startswith("file:")   # a local page in the browser, its query kept
        try:
            self.launch(launch_spec("page" if page else verb, target))
        except OSError as exc:
            self.log.append("warning", paths=paths, verb=verb, target=target,
                            note=f"open: {verb} {target} could not launch: {exc}", **extra)
            return False
        where = {"url": target} if verb == "url" or page else {"path": target}
        if local is not None:
            paths.append(self.cfg.rel(local))
        elif verb != "url":
            paths.append(self.cfg.rel(Path(target)))
        self.log.append("opened", paths=paths, verb=verb, **where, **extra)
        return True
