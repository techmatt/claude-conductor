"""`conductor autostart on | off | status`: start the app at Windows logon (docs/app.md, *Daemon down*).

A shut-down, restart or sign-out ends every process of the session, daemons and executors
included (149: Nov 23 6:15pm, five daemons and the app, unseen for 38 minutes, 80 for the
item). With this on, the app starts at logon on its remembered projects, sees each daemon
that died and restarts it (`app/heartbeat.py`); running items are adopted and a cut
session resumes in place (`docs/restart.md`). Windows only: a `.cmd` in the user's
Startup folder.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

NAME = "conductor-app.cmd"
ROOT = Path(__file__).resolve().parents[2]       # this checkout


def startup_dir() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def entry() -> Path:
    return startup_dir() / NAME


def script(root: Path = ROOT) -> str:
    """The `.cmd`: the venv's windowless Python running `conductor app` from this checkout."""
    pyw = root / ".venv" / "Scripts" / "pythonw.exe"
    return f'@echo off\r\nrem written by `conductor autostart on` ({root})\r\nstart "" /d "{root}" "{pyw}" -m conductor app\r\n'


def set_on(on: bool) -> str:
    if sys.platform != "win32":
        raise OSError("autostart is implemented for Windows only")
    path = entry()
    if on:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(script().encode("utf-8"))          # CRLF: cmd.exe's own
        return f"on: {path} starts the app at logon"
    path.unlink(missing_ok=True)
    return f"off: {path} removed"


def status() -> str:
    return f"on: {entry()}" if entry().is_file() else "off"
