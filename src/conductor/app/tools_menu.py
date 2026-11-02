"""The header's Tools menu: one row per `[tools.<name>]`, each a signal the daemon carries out."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QWidget, QWidgetAction

from ..events import Event
from ..util import now, parse_stamp
from . import actions, theme
from .model import Project, Snapshot

FAILURE_HOURS = 1       # older than this, met at the app's first refresh, it stays in Events


class ToolRow(QWidget):
    """`● <label>` (start if needed, then open) with Stop beside it while the tool runs."""

    def __init__(self, menu: QMenu, name: str, run, stop):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 1, 4, 1)
        self.open, self.stop = QPushButton(), QPushButton("Stop")
        self.open.setFlat(True)
        self.open.setStyleSheet("text-align: left;")
        self.stop.setToolTip(f"Stop {name}: kills its process tree (a `tool: {name} stop` signal; "
                             "the daemon must be running)")
        for b, fn in ((self.open, run), (self.stop, stop)):
            b.clicked.connect(menu.close)
            b.clicked.connect(lambda _=False, fn=fn, n=name: fn(n))
        lay.addWidget(self.open, 1)
        lay.addWidget(self.stop)


class ToolsMenu:
    """`button` and, beside it, `failure`: a failed start's error and its log's last line."""

    def __init__(self, project: Project, notify):
        self.project, self.notify = project, notify
        self.button = QPushButton("Tools")
        self.menu = QMenu(self.button)
        self.button.setMenu(self.menu)
        self.button.hide()
        self.rows: dict[str, ToolRow] = {}
        self.failure = QLabel()
        self.failure.setStyleSheet(f"color: {theme.RED};")
        self.failure.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.failure.hide()

    def update_snapshot(self, snap: Snapshot, new: list[Event] = ()) -> None:
        if list(snap.tools) != list(self.rows):
            self.menu.clear()
            self.rows = {}
            for name in snap.tools:
                row = self.rows[name] = ToolRow(self.menu, name, self.run, self.stop)
                action = QWidgetAction(self.menu)
                action.setDefaultWidget(row)
                self.menu.addAction(action)
            self.button.setVisible(bool(snap.tools))
        for name, st in snap.tools.items():
            t, row, running = self.project.cfg.tools.get(name), self.rows[name], st == "running"
            text = f"● {t.label if t else name}" if running else f"    {t.label if t else name}"
            if row.open.text() != text:
                row.open.setText(text)
                row.open.setStyleSheet(f"text-align: left; color: {theme.GREEN if running else theme.TEXT};")
            row.open.setToolTip(f"{'Open' if running else 'Start, then open'} {t.url if t else ''} (a `tool:` signal; "
                                "the daemon must be running, and stopping it stops the tool)")
            row.stop.setVisible(running)
        for e in new:
            if e.detail.get("tool"):
                self._note(e)

    def _note(self, e: Event) -> None:
        if e.kind == "run_failed" and (now() - parse_stamp(e.ts)).total_seconds() < FAILURE_HOURS * 3600:
            tail = str(e.detail.get("log_tail", ""))
            self.failure.setText(f"{e.detail.get('target')}: {e.detail.get('error')}"
                                 + (f" — {tail.splitlines()[-1]}" if tail else ""))
            self.failure.setToolTip(tail or str(e.detail.get("error")))
            self.failure.show()
        elif e.kind in ("run_start", "run_open", "run_stop"):
            self.failure.clear()
            self.failure.setToolTip("")
            self.failure.hide()

    def run(self, name: str) -> None:
        path = actions.tool(self.project.control, name)
        self.notify(f"tool {name}: wrote signals/{path.name}; the daemon starts it if needed and opens it")

    def stop(self, name: str) -> None:
        path = actions.tool(self.project.control, name, stop=True)
        self.notify(f"stop tool {name}: wrote signals/{path.name}; the daemon stops it")
