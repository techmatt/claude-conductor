"""The app against conductor's checkout: it restarts itself onto a new commit (83).

A daemon restarts itself when conductor's code changes (`docs/restart.md`); before 83
the app never did, so after a commit it ran old code beside new daemons until someone
said "restart all". Now the app checks the committed version every `CHECK_SECONDS` off
the GUI thread (`code_version`, the daemon's own rule), and once it differs from the one
the app started on, the checkout is settled and the new code imports, the window restarts
the app at the first refresh with nothing mid-action: no dialog or menu open and no
unsent text in a box. Until then every project's banner says, in whole words, why not.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from PySide6.QtWidgets import QApplication, QWidget

from .. import procs
from ..daemon_restart import checkout_blockers, code_head, code_version, imports_ok
from ..session import CONDUCTOR_ROOT
from .widgets import SendBox

CHECK_SECONDS = 10
APP_IMPORT = "import conductor.cli, conductor.app.window"


@dataclass
class Probe:
    version: str                                        # the committed code on disk, not the app's
    head: str = "?"                                     # `<short sha> <subject>`
    blockers: list[str] = field(default_factory=list)   # the checkout not settled, or the import failing

    @property
    def ready(self) -> bool:
        return not self.blockers


class Watcher:
    """The newest `Probe` when the checkout moved past `app_version`, else None; each
    probe runs on its own thread, at most one at a time, every `CHECK_SECONDS`."""

    def __init__(self, app_version: str | None):
        self.app_version = app_version
        self.probe: Probe | None = None
        self._failed: dict[str, str] = {}           # version → its import error, tried once
        self._next = 0.0
        self._thread: threading.Thread | None = None

    def poll(self) -> Probe | None:
        if self.app_version and time.monotonic() >= self._next and not (self._thread and self._thread.is_alive()):
            self._next = time.monotonic() + CHECK_SECONDS
            self._thread = threading.Thread(target=self._run, name="conductor-code-check", daemon=True)
            self._thread.start()
        return self.probe

    def _run(self) -> None:
        disk = code_version()
        if not disk or disk == self.app_version:
            self.probe = None
            return
        blockers = checkout_blockers()
        if not blockers:
            if disk not in self._failed:
                self._failed[disk] = imports_ok(CONDUCTOR_ROOT, APP_IMPORT) or ""
            if self._failed[disk]:
                blockers = [f"the new code doesn't import ({self._failed[disk]})"]
        self.probe = Probe(disk, code_head(), blockers)


def busy(win: QWidget) -> list[str]:
    """What is mid-action in the app, in words: a restart now would lose it."""
    out = []
    if QApplication.activeModalWidget() is not None:
        out.append("a dialog is open")
    if QApplication.activePopupWidget() is not None:
        out.append("a menu is open")
    if any(b.toPlainText().strip() for b in win.findChildren(SendBox)):
        out.append("a box holds unsent text")
    return out


def banner(probe: Probe | None, waits: list[str], daemon_differs: bool) -> str:
    """The banner's whole sentence, plain words, no markup: what differs and what fixes it."""
    fix = "“Restart all” restarts the app and every daemon now, running items carrying on."
    if probe is not None:
        why = "; ".join(probe.blockers or waits) or "the next refresh"
        how = ("It restarts itself once nothing is mid-action, waiting for: " if probe.ready else
               "It can't restart itself yet: ")
        return f"Conductor's code changed ({probe.head}) and this app still runs the old code. {how}{why}. {fix}"
    if daemon_differs:
        return ("This project's daemon runs different code from this app, so its rejected marks and messages "
                f"may not match. A daemon restarts itself at its next safe point. {fix}")
    return ""


def restart(win, folders: list) -> str | None:
    """Hand the app over to a fresh one on the code on disk: stop serving forwarded launches
    (or the new app would forward to this one and exit), spawn, quit. The error, if the spawn
    failed (this app then keeps running and serves again)."""
    from . import single
    if getattr(win, "server", None) is not None:
        win.server.close()
    log = (folders[0] if folders else CONDUCTOR_ROOT) / ".conductor" / "app.out.log"
    try:
        procs.spawn_detached([procs.console_python(), "-m", "conductor", "app", *map(str, folders)],
                             CONDUCTOR_ROOT, log)
    except OSError as exc:
        win.server = single.listen(win.open_forwarded)
        return repr(exc)
    print(f"app: restarting onto {win.stale.probe.head if win.stale.probe else 'new code'}", flush=True)
    win.quit()
    return None
