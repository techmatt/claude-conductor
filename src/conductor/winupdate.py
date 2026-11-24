"""What Windows says about restarts: an update waiting for one, its active hours, and why
the machine last restarted. Read-only, Windows only (None or empty elsewhere), and never
raises: callers only warn from it (`docs/restart.md`, *Machine restarts*)."""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime, timezone

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


# What ends every process of the session (149: Nov 23 6:15pm the power button shut the PC down,
# Fast Startup style: Winlogon's logoff 7002, then a hibernate logged as sleep 42; all five
# daemons, their executors and the app died with no event of their own).
_ENDS = {7002: "Windows signed you out (a shut-down, restart or sign-out ends every process)",
         1074: "Windows was asked to shut down or restart", 6008: "Windows stopped unexpectedly",
         41: "Windows lost power or crashed"}
_EVENT_RE = re.compile(r"<EventID[^>]*>(\d+)</EventID>.*?SystemTime='([^']+)'", re.S)


def why_down(since: datetime, until: datetime) -> str | None:
    """What Windows logged that ends user processes between `since` and `until`: "Windows
    signed you out (…) at 6:15pm", or None (nothing found, or not Windows)."""
    if not WINDOWS:
        return None
    def z(t: datetime) -> str:
        return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    ids = " or ".join(f"EventID={i}" for i in _ENDS)
    q = f"*[System[({ids}) and TimeCreated[@SystemTime>='{z(since)}' and @SystemTime<='{z(until)}']]]"
    try:
        out = subprocess.run(["wevtutil", "qe", "System", f"/q:{q}", "/f:xml"], capture_output=True, text=True,
                             timeout=15, creationflags=subprocess.CREATE_NO_WINDOW).stdout
    except Exception:                            # noqa: BLE001
        return None
    return describe_ends(out)


def describe_ends(xml: str) -> str | None:
    """The first session-ending event in `wevtutil … /f:xml` output, in words with its local time."""
    for eid, at in _EVENT_RE.findall(xml):
        if int(eid) in _ENDS:
            try:
                t = datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone()
            except ValueError:
                continue
            return f"{_ENDS[int(eid)]} at {t.strftime('%I:%M%p').lstrip('0').lower()}"
    return None


def hours_text(active: tuple[int, int] | None) -> str:
    """`8am-5pm`, or "" when unknown."""
    if not active:
        return ""
    def h(n: int) -> str:
        n %= 24
        return f"{12 if n % 12 == 0 else n % 12}{'am' if n < 12 else 'pm'}"
    return f"{h(active[0])}-{h(active[1])}"
