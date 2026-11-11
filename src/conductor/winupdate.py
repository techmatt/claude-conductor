"""What Windows says about restarts: an update waiting for one, its active hours, and why
the machine last restarted. Read-only, Windows only (None or empty elsewhere), and never
raises: callers only warn from it (`docs/restart.md`, *Machine restarts*)."""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime

import psutil

WINDOWS = sys.platform == "win32"
# A key that exists while a restart is owed: Windows Update's, and servicing's (CBS).
PENDING_KEYS = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending")
UX_KEY = r"SOFTWARE\Microsoft\WindowsUpdate\UX\Settings"      # ActiveHoursStart / ActiveHoursEnd (hours)
REASON_RE = re.compile(r"for the following reason:\s*(.+)")


def pending() -> dict | None:
    """`{"active": (start, end) or None}` while an update waits for a restart, else None.
    Outside active hours Windows may restart by itself: overnight, as for workbench 009."""
    if not WINDOWS:
        return None
    try:
        import winreg
        found = False
        for key in PENDING_KEYS:
            try:
                winreg.CloseKey(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key))
                found = True
            except OSError:
                pass
        if not found:
            return None
        active = None
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, UX_KEY) as k:
                active = (int(winreg.QueryValueEx(k, "ActiveHoursStart")[0]),
                          int(winreg.QueryValueEx(k, "ActiveHoursEnd")[0]))
        except (OSError, ValueError):
            pass
        return {"active": active}
    except Exception:                            # noqa: BLE001 - a warning source only
        return None


def boot_time() -> datetime | None:
    try:
        return datetime.fromtimestamp(psutil.boot_time()).astimezone()
    except Exception:                            # noqa: BLE001
        return None


def restart_reason() -> str | None:
    """The reason Windows logged for the last restart it was asked for (System event 1074),
    e.g. "Operating System: Upgrade (Planned)", or None."""
    if not WINDOWS:
        return None
    try:
        out = subprocess.run(["wevtutil", "qe", "System", "/q:*[System[(EventID=1074)]]", "/c:1", "/rd:true",
                              "/f:text"], capture_output=True, text=True, timeout=15,
                             creationflags=subprocess.CREATE_NO_WINDOW).stdout
        m = REASON_RE.search(out)
        return m.group(1).strip() if m else None
    except Exception:                            # noqa: BLE001
        return None


def hours_text(active: tuple[int, int] | None) -> str:
    """`8am-5pm`, or "" when unknown."""
    if not active:
        return ""
    def h(n: int) -> str:
        n %= 24
        return f"{12 if n % 12 == 0 else n % 12}{'am' if n < 12 else 'pm'}"
    return f"{h(active[0])}-{h(active[1])}"
