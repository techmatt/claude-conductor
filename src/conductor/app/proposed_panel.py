"""The Proposed tab: items awaiting Matt's approval."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from .. import items
from . import actions, theme
from .model import Project, Snapshot
from .widgets import RED, ROW_HEIGHT, KeepColourDelegate, Panel, confirm, muted


class ProposedPanel(Panel):
    COLS = ["Item", "Repo", "Problems", "First line"]
    PROBLEMS, FIRST = 2, 3

    def __init__(self, project: Project, notify):
        super().__init__(project, notify)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 8)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        h = self.table.horizontalHeader()
        h.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        h.setStretchLastSection(False)
        for c in range(len(self.COLS)):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(self.FIRST, QHeaderView.ResizeMode.Stretch)
        self.table.setItemDelegate(KeepColourDelegate(self.table))
        self.table.cellDoubleClicked.connect(self._open)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self._bold = self.table.font()
        self._bold.setBold(True)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        self.buttons = {}
        for name, slot in (("Approve", self._approve), ("Cancel", self._cancel),
                           ("Move up", lambda: self._move(-1)), ("Move down", lambda: self._move(1)),
                           ("Open trail", lambda: self._open(self.table.currentRow(), 0))):
            b = QPushButton(name)
            if name == "Approve":
                b.setProperty("role", "approve")
            b.clicked.connect(slot)
            row.addWidget(b)
            if name in ("Cancel", "Move down"):
                row.addSpacing(12)              # approve/cancel · reorder · open
            self.buttons[name] = b
        self.buttons["Approve"].setEnabled(False)       # until a row that can be approved is selected
        row.addStretch(1)
        lay.addLayout(row)
        note = muted(QLabel("Approve, cancel and reorder are signals; the daemon (it must be running) "
                            "applies each within a few seconds and logs it."))
        lay.addWidget(note)
        self.items: list = []
        self._sig: tuple | None = None
        # A move in flight: its signal file, the name the moved item will have, and how
        # many refreshes have passed since the file went without that name showing up.
        self._pending: Path | None = None
        self._follow: str | None = None
        self._gone_for = 0

    def _settle(self, names: list[str]) -> bool:
        """Is the last move done (applied, rejected or overtaken)? Until it is, a second
        move would be built from names the daemon is about to change."""
        if self._pending is None:
            return True
        if self._follow in names or self._pending.with_name(self._pending.name + ".bad").exists():
            self._pending = None
        elif not self._pending.exists():
            self._gone_for += 1
            if self._gone_for >= 2:
                self._pending = None
        return self._pending is None

    def update_snapshot(self, snap: Snapshot) -> None:
        names = [p.name for p in snap.proposed]
        self._settle(names)
        sig = tuple((p.name, p.repo, tuple(p.errors), p.summary) for p in snap.proposed)
        if sig == self._sig:
            return
        self._sig = sig
        selected = self._selected()
        self.items = snap.proposed
        self.table.setRowCount(len(self.items))
        tint = QBrush(QColor(theme.RED_BG))
        for r, p in enumerate(self.items):
            name = f"{p.name} — invalid" if p.errors else p.name
            for c, text in enumerate((name, p.repo or "?", "; ".join(p.errors), p.summary)):
                cell = QTableWidgetItem(text)
                if p.errors:
                    # A row that cannot be approved reads as one: tinted, its name and
                    # problem in bold red.
                    cell.setBackground(tint)
                    if c in (0, self.PROBLEMS):
                        cell.setForeground(QBrush(RED))
                        cell.setFont(self._bold)
                self.table.setItem(r, c, cell)
        # A reorder renames: the selection follows the moved item to its new name.
        if self._follow in names:
            self.table.selectRow(names.index(self._follow))
            self._follow = None
        elif selected and selected.name in names:
            self.table.selectRow(names.index(selected.name))
        self._update_buttons()

    def _update_buttons(self) -> None:
        """Approve is offered only for a selected row that can be approved."""
        p = self._selected() if self.table.selectionModel().hasSelection() else None
        self.buttons["Approve"].setEnabled(p is not None and not p.errors)

    def _selected(self):
        r = self.table.currentRow()
        return self.items[r] if 0 <= r < len(self.items) else None

    def _approve(self) -> None:
        if p := self._selected():
            self.signal_sent(f"approve {p.name}", actions.approve(self.control, p.name))

    def _cancel(self) -> None:
        if (p := self._selected()) and confirm(
                self, "Cancel proposed item",
                f"Cancel {p.name}?\n\nThe daemon moves it to .conductor/cancelled/ (kept, not deleted)."):
            # `proposed/` prefix: never resolved against a queued item of the same name.
            self.signal_sent(f"cancel {p.name}", actions.cancel(self.control, f"proposed/{p.name}"))

    def _move(self, delta: int) -> None:
        r = self.table.currentRow()
        names = [p.name for p in self.items]
        if not self._settle(names):
            self.notify("the previous move is still waiting for the daemon; try again once it lands")
            return
        if path := actions.swap(self.control, "proposed", names, r, delta):
            pair = [names[r + delta], names[r]] if delta > 0 else [names[r], names[r + delta]]
            renamed = dict(zip(pair, items.renumber(pair)))
            self._pending, self._follow, self._gone_for = path, renamed[names[r]], 0
            self.signal_sent("reorder proposed", path)

    def _open(self, row: int, _col: int) -> None:
        if 0 <= row < len(self.items) and (p := self.items[row]).repo:
            self.open_item.emit(f"{p.repo}/{p.name}")
