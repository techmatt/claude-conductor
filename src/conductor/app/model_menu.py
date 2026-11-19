"""The model toggle (docs/app.md, *Model*): which Claude model every project's next new
session runs on, `models.py`'s machine-wide setting. One button on the window's toolbar and
one submenu in the tray, kept in step. The app writes the setting itself, not a signal: it is
no project's, and it takes effect with every daemon down too. A `model:` signal from a planner
changes the same file; each refresh shows what it holds now."""

from __future__ import annotations

from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu, QToolButton, QWidget

from .. import models

TIP = ("Claude model for new sessions in every project: items, watches, retros, merge executors. "
       "Running items, staged items when they resume, and ask forks keep theirs.")


class ModelMenu:
    def __init__(self, parent: QWidget, notify):
        self.notify = notify
        self.button = QToolButton(parent)
        self.button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.button.setToolTip(TIP)
        self.actions: list[tuple[str, QAction]] = []
        self.button.setMenu(self._menu(parent))
        self.shown: str | None = None
        self.sync()

    def _menu(self, parent: QWidget, title: str | None = None) -> QMenu:
        menu = QMenu(title, parent) if title else QMenu(parent)
        group = QActionGroup(menu)
        for name in models.MODELS:
            a = menu.addAction(models.describe(name))
            a.setCheckable(True)
            group.addAction(a)
            a.triggered.connect(lambda _=False, n=name: self.choose(n))
            self.actions.append((name, a))
        menu.setToolTipsVisible(True)
        menu.setToolTip(TIP)
        return menu

    def tray_menu(self, parent: QWidget) -> QMenu:
        """The same choice as a submenu, for the tray."""
        menu = self._menu(parent, "Model")
        self.sync(force=True)
        return menu

    def choose(self, name: str) -> None:
        try:
            was = models.set_model(name, "app")
        except (OSError, ValueError) as exc:
            self.notify(f"model: could not save the setting ({exc})")
            self.sync(force=True)
            return
        self.sync()
        if was != name:
            self.notify(f"model: new sessions in every project now run on {models.describe(name)}; "
                        "running and staged items keep theirs")

    def sync(self, force: bool = False) -> None:
        """Show the setting the file holds (a `model:` signal may have changed it)."""
        name = models.current()
        if name == self.shown and not force:
            return
        self.shown = name
        self.button.setText(f"Model: {models.LABELS.get(name, name)}")
        for n, a in self.actions:
            a.setChecked(n == name)
