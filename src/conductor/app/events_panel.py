"""The Events tab: the event log as a filtered table, with the selected item's trail."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QAbstractTableModel, QDateTime, QModelIndex, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateTimeEdit, QHBoxLayout, QLabel, QLineEdit,
                               QSplitter, QTableView, QVBoxLayout)

from ..events import RED_KINDS, Event
from ..inbox import describe
from ..util import clock
from . import theme
from .model import ACTIVITY_KINDS, ALWAYS_KINDS, Project
from .trail import TrailView
from .widgets import ROW_HEIGHT, KeepColourDelegate, Panel, finish_status, muted, plain_summary, summary


class EventModel(QAbstractTableModel):
    HEAD = ["#", "Time", "Kind", "Item / repo", "What"]

    KIND = 2

    def __init__(self):
        super().__init__()
        self.rows: list[Event] = []
        self._looks: dict[str, tuple] = {}
        self._bold = None                   # the view's font, bold; set by the panel
        self.plain = True                   # Activity wording; False (Advanced): `describe`

    def _look(self, kind: str, status: str | None = None) -> tuple:
        """(foreground brush or None, background brush or None, bold, whole row) per kind
        (and, for a finish, per outcome)."""
        key = (kind, status)
        if key not in self._looks:
            fg, bg, bold = theme.kind_style(kind, RED_KINDS, status)
            # Reds, holds and routine kinds colour the whole row; the others only the Kind.
            whole = bool(bg) or fg == theme.MUTED
            self._looks[key] = (QBrush(QColor(fg)) if fg else None, QBrush(QColor(bg)) if bg else None,
                                bold or (fg is not None and fg != theme.MUTED), whole)
        return self._looks[key]

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.HEAD)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEAD[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        ev = self.rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == len(self.HEAD) - 1:
                return plain_summary(ev) if self.plain else summary(ev)
            return (str(ev.id), clock(ev.ts), ev.kind, ev.item or ev.repo or "")[col]
        if role in (Qt.ItemDataRole.ForegroundRole, Qt.ItemDataRole.BackgroundRole, Qt.ItemDataRole.FontRole):
            fg, bg, bold, whole = self._look(ev.kind, finish_status(ev))
            if role == Qt.ItemDataRole.BackgroundRole:
                return bg
            if not (whole or col == self.KIND):
                return None
            if role == Qt.ItemDataRole.ForegroundRole:
                return fg
            if bold and (col == self.KIND or bg is not None):
                return self._bold
            return None
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"#{ev.id} {ev.ts}\n{describe(ev)}"
        return None

    def reset(self, rows: list[Event]) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def extend(self, rows: list[Event]) -> None:
        if rows:
            n = len(self.rows)
            self.beginInsertRows(QModelIndex(), n, n + len(rows) - 1)
            self.rows += rows
            self.endInsertRows()


class EventsPanel(Panel):
    def __init__(self, project: Project, notify):
        super().__init__(project, notify)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        bar = QHBoxLayout()
        self.repo = QComboBox()
        self.repo.addItem("all repos", None)
        for r in project.cfg.repos:
            self.repo.addItem(r, r)
        self.item = QLineEdit()
        self.item.setPlaceholderText("item #  (Ctrl+F)")
        self.item.setToolTip("Filter by item: 001, 001_x or repo/001_x (Ctrl+F)")
        self.item.setMinimumWidth(150)
        self.item.setClearButtonEnabled(True)
        self.kind = QComboBox()
        self.kind.addItem("all kinds", None)
        self.since_on = QCheckBox("from")
        self.since = QDateTimeEdit(QDateTime.currentDateTime().addDays(-1))
        self.until_on = QCheckBox("to")
        self.until = QDateTimeEdit(QDateTime.currentDateTime())
        for w in (self.since, self.until):
            w.setCalendarPopup(True)
            w.setDisplayFormat("MMM d h:mmap")
        self.reds = QCheckBox("reds only (Ctrl+R)")
        self.advanced = QCheckBox("Advanced")
        self.advanced.setToolTip("Every event kind (worktree, stage, merge, commit, hold, enqueue, read, ...), "
                                 "worded as the log has it. Off: Activity, the workflow-level lines only.")
        self.current = QCheckBox("since checkpoint")
        self.current.setChecked(True)
        self.current.setToolTip("Only events from the latest checkpoint on (a session boundary). "
                                "Off: every event in the log.")
        for w in (QLabel("Repo"), self.repo, self.item, QLabel("Kind"), self.kind,
                  self.since_on, self.since, self.until_on, self.until, self.reds, self.current, self.advanced):
            bar.addWidget(w)
        bar.addStretch(1)
        lay.addLayout(bar)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.model = EventModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setWordWrap(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        bold = self.table.font()
        bold.setBold(True)
        self.model._bold = bold
        self.table.setItemDelegate(KeepColourDelegate(self.table))
        head = self.table.horizontalHeader()
        head.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        # #, Time, Kind and Item are fixed and narrow; What (the last) takes the rest and
        # never drops below 260px.
        widths = (52, 84, 118, 165)
        for c, w in enumerate(widths):
            self.table.setColumnWidth(c, w)
        self.table.setMinimumWidth(sum(widths) + 260 + 20)
        split.addWidget(self.table)
        self.trail = TrailView(project)
        self.trail.setMinimumWidth(300)
        split.addWidget(self.trail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([700, 470])
        lay.addWidget(split, 1)
        self.count = muted(QLabel())
        lay.addWidget(self.count)
        # Moving through the list re-renders the trail after a short pause, not per keypress.
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(120)
        self._debounce.timeout.connect(self._show_selected)
        self.table.selectionModel().currentRowChanged.connect(lambda *_: self._debounce.start())
        for sig in (self.repo.currentIndexChanged, self.kind.currentIndexChanged, self.item.textChanged,
                    self.since_on.toggled, self.until_on.toggled, self.since.dateTimeChanged,
                    self.until.dateTimeChanged, self.reds.toggled, self.current.toggled):
            sig.connect(self.refilter)
        self.advanced.toggled.connect(self._set_advanced)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: (self.item.setFocus(), self.item.selectAll()))
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self.reds.toggle)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.table.setFocus)
        for i in range(len(TrailView.TABS)):
            QShortcut(QKeySequence(f"Alt+{i + 1}"), self,
                      activated=lambda i=i: self.trail.tabs.setCurrentIndex(i))
        self.item.returnPressed.connect(self.table.setFocus)
        self._kinds: list[str] = []
        self._generation = project.generation
        self._checkpoint = project.checkpoint
        self._label_checkpoint()
        self.refilter()

    def _label_checkpoint(self) -> None:
        cp = self.project.checkpoint
        self.current.setText(f"since checkpoint #{cp.id}" if cp else "since checkpoint (none yet)")

    def _filters(self) -> dict:
        def dt(edit) -> datetime:
            return edit.dateTime().toPython().astimezone()
        cp = self.project.checkpoint
        return dict(from_id=cp.id if cp and self.current.isChecked() else None,
                    repo=self.repo.currentData(), item=self.item.text().strip() or None,
                    kinds=[self.kind.currentData()] if self.kind.currentData() else None,
                    since=dt(self.since) if self.since_on.isChecked() else None,
                    until=dt(self.until) if self.until_on.isChecked() else None,
                    reds=self.reds.isChecked(), advanced=self.advanced.isChecked())

    def refilter(self, *_) -> None:
        current = self._current_event()
        rows = self.project.filter_events(**self._filters())
        self.model.reset(rows)
        self._update_count()
        if current:
            for i, ev in enumerate(rows):
                if ev.id == current.id:
                    self.table.selectRow(i)
                    break
        elif rows:
            self.table.scrollToBottom()

    def _set_advanced(self, on: bool) -> None:
        self.model.plain = not on
        self._kinds = []                    # the Kind list differs per mode
        self._fill_kinds()
        self.refilter()

    def _fill_kinds(self) -> None:
        kinds = self.project.kinds()
        if not self.advanced.isChecked():
            kinds = [k for k in kinds if k in ACTIVITY_KINDS or k in ALWAYS_KINDS]
        if kinds == self._kinds:
            return
        self._kinds = kinds
        cur = self.kind.currentData()
        self.kind.blockSignals(True)
        self.kind.clear()
        self.kind.addItem("all kinds", None)
        for k in kinds:
            self.kind.addItem(k, k)
        self.kind.setCurrentIndex(max(self.kind.findData(cur), 0) if cur else 0)
        self.kind.blockSignals(False)

    def _update_count(self) -> None:
        what = "event(s)" if self.advanced.isChecked() else "event(s) shown as Activity (Advanced: all)"
        if self.current.isChecked() and self.project.checkpoint:
            what += f" since checkpoint #{self.project.checkpoint.id}"
        self.count.setText(f"{len(self.model.rows)} of {len(self.project.events)} {what} · "
                           "↑/↓ PgUp/PgDn Home/End move · Ctrl+F item · Ctrl+R reds · Alt+1…6 trail tabs")

    def add_events(self, new: list[Event]) -> None:
        self._fill_kinds()
        if self.project.generation != self._generation:
            # The log was replaced: rows already shown belong to the old one.
            self._generation = self.project.generation
            self._checkpoint = self.project.checkpoint
            self._label_checkpoint()
            self.refilter()
            return
        if not new:
            return
        if self.project.checkpoint is not self._checkpoint:
            # A new session boundary: the rows before it drop out of the default view.
            self._checkpoint = self.project.checkpoint
            self._label_checkpoint()
            self.refilter()
            return
        sb = self.table.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 2
        keep = {e.id for e in self._match(new)}
        self.model.extend([e for e in new if e.id in keep])
        self._update_count()
        if at_bottom:
            self.table.scrollToBottom()
        if self.trail.item and any(e.item == self.trail.item for e in new):
            self.trail.refresh_item()

    def _match(self, events: list[Event]) -> list[Event]:
        return self.project.filter_events(**self._filters(), events=events)

    def _current_event(self) -> Event | None:
        idx = self.table.currentIndex()
        return self.model.rows[idx.row()] if idx.isValid() and idx.row() < len(self.model.rows) else None

    def _show_selected(self) -> None:
        if ev := self._current_event():
            self.trail.show_event(ev)

    def show_item(self, item_id: str) -> None:
        self.trail.show_item(item_id)
