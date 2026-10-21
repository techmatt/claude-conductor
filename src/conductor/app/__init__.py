"""The desktop app: a PySide6 tray app that watches one or more control folders.

`conductor app [<control folder> …]` or the `conductor-app` gui script. Qt is imported
only here, lazily, so the daemon and the CLI never need it. `model` and `actions` are
Qt-free; `window`, one `*_panel` module per tab, `trail` and `widgets` are the widgets.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    folders = [Path(a) for a in (sys.argv[1:] if argv is None else argv)]
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:          # pragma: no cover - PySide6 is a dependency
        print(f"conductor app: PySide6 is not available ({exc})", file=sys.stderr)
        return 1
    from .. import procs
    from . import icon, single, theme
    from .window import MainWindow

    procs.set_app_id()                        # the taskbar shows conductor's icon, not Python's
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Conductor")
    app.setWindowIcon(icon.app_icon())
    folders = [f.resolve() for f in folders]   # the running app has its own cwd
    if single.forward(folders):
        return 0                              # an app is already running: it has them now
    theme.apply(app)                          # light, whatever the system theme says
    app.setQuitOnLastWindowClosed(False)      # closing the window hides it to the tray
    win = MainWindow(folders)
    win.server = single.listen(win.open_forwarded)
    win.show()
    if not win.has_tray():
        app.setQuitOnLastWindowClosed(True)
    return app.exec()
