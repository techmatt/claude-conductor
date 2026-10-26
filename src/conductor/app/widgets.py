"""What the panels share: colours, small formatters, compact buttons, and the `Panel` base."""

from __future__ import annotations

import re
from typing import Callable

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPalette
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLayout, QMessageBox,
                               QPlainTextEdit, QPushButton, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from ..events import Event
from ..inbox import describe
from ..util import clock, now, parse_stamp
from . import actions, theme
from .model import ACTIVITY_KINDS, Project, activity_text

RED = QColor(theme.RED)
AMBER = QColor(theme.AMBER)
ROW_HEIGHT = 28
_ABS_PATH = re.compile(r"(?<![\w/])(?:[A-Za-z]:[/\\]|/)[^\s`'\",;)]+")


def mono() -> object:
    return theme.mono_font()


def muted(label: QLabel) -> QLabel:
    label.setProperty("muted", True)
    return label


def short_paths(text: str) -> str:
    """Long absolute paths cut to `…/<last two parts>`, for one-line summaries (the full
    path stays in tooltips and in the trail's detail block)."""
    def cut(m: re.Match) -> str:
        parts = [p for p in re.split(r"[/\\]", m.group(0)) if p]
        return m.group(0) if len(parts) <= 3 else "…/" + "/".join(parts[-2:])
    return _ABS_PATH.sub(cut, text)


def summary(ev: Event) -> str:
    """`describe(ev)` without the leading item (or repo) the row already shows, and with
    long paths shortened."""
    text = describe(ev)
    who = ev.item or ev.repo or ""
    if who and text.startswith(who + " "):
        text = text[len(who) + 1:]
    return short_paths(text)


def plain_summary(ev: Event) -> str:
    """The Activity view's wording of an event (`summary` for kinds it does not reword)."""
    return short_paths(activity_text(ev)) if ev.kind in ACTIVITY_KINDS else summary(ev)


def finish_status(ev: Event) -> str | None:
    return ev.detail.get("status") if ev.kind == "finish" else None


class KeepColourDelegate(QStyledItemDelegate):
    """A selected cell keeps its own text colour, so selecting a red (or running, held …)
    row does not hide what kind of row it is. Each such colour passes 4.5:1 on SELECT."""

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        if option.state & QStyle.StateFlag.State_Selected:
            fg = index.data(Qt.ItemDataRole.ForegroundRole)
            if fg is not None:
                colour = fg.color() if isinstance(fg, QBrush) else QColor(fg)
                option.palette.setColor(QPalette.ColorRole.HighlightedText, colour)


def compact_button(text: str, slot, tone: str | None = None) -> QWidget:
    """A small button for a tree cell, left-aligned instead of filling the column."""
    return compact_buttons((text, slot, tone))


def compact_buttons(*specs: tuple, slots: list[str] | None = None) -> QWidget:
    """Small buttons side by side for one tree cell: each spec is (text, slot, tone).
    `holder.buttons` maps text → button; `holder.button` is the first. With `slots`, the
    buttons sit in that order and a slot this row lacks keeps its room (an invisible
    stand-in), so each kind of button lines up across rows. `holder.row` is every
    button in layout order, stand-ins included."""
    holder = QWidget()
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(4, 1, 4, 1)
    holder.buttons, holder.row = {}, []
    given = {text: (slot, tone) for text, slot, tone in specs}
    for text in slots or list(given):
        b = QPushButton(text)
        b.setProperty("role", "compact")
        lay.addWidget(b)
        holder.row.append(b)
        if text not in given:
            policy = b.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            b.setSizePolicy(policy)
            b.hide()
            continue
        slot, tone = given[text]
        if tone:
            b.setProperty("tone", tone)
        if slot is not None:
            b.clicked.connect(slot)
        holder.buttons[text] = b
    lay.addStretch(1)
    holder.button = next(iter(holder.buttons.values()))
    return holder


def when(value: str | None) -> str:
    """`clock()` for a stamp read from a file someone may have written by hand: a value
    that is not ISO shows as it is instead of breaking the refresh."""
    if not value:
        return ""
    try:
        return clock(value)
    except (TypeError, ValueError):
        return str(value)


def pick_note(p) -> str:
    """Where Matt is with a finished item's picker (a `picks.PickInfo`, or None)."""
    if p is None or p.state == "waiting":
        return "waiting for Matt"
    if p.state == "opened":
        return "opened, not saved"
    notes = p.notes or 0
    return f"saved {when(p.saved_at)} ({p.changed} changed, {notes} note{'' if notes == 1 else 's'})"


def countdown(deadline: str | None) -> str:
    if not deadline:
        return ""
    try:
        left = int((parse_stamp(deadline) - now()).total_seconds())
    except (TypeError, ValueError):
        return "?"
    if left <= 0:
        return "due now"
    m, s = divmod(left, 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def confirm(parent: QWidget, title: str, text: str) -> bool:
    return QMessageBox.question(parent, title, text,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes


SEND_HINT = "Enter sends · Shift+Enter for a new line"


class SendBox(QPlainTextEdit):
    """A free-text box: word-wrapped, never scrolling sideways; Enter emits `submitted`,
    Shift+Enter inserts a newline."""
    submitted = Signal()

    def __init__(self, lines: int = 6):
        super().__init__()
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumHeight(self.fontMetrics().lineSpacing() * lines + 12)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submitted.emit()
            return
        super().keyPressEvent(event)


class FlowLayout(QLayout):
    """Widgets left to right, wrapping onto a new row at the parent's width (Qt has no
    stock one): a row of buttons that never pushes its parent wider than the window."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 6):
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._place(QRect(0, 0, width, 0), move=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._place(rect, move=True)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _place(self, rect: QRect, move: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, row = area.x(), area.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x > area.x() and x + hint.width() > area.right() + 1:
                x, y, row = area.x(), y + row + self._spacing, 0
            if move:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            row = max(row, hint.height())
        return y + row - rect.y() + m.bottom()


class MessageDialog(QDialog):
    """Tell…, Amend… and the like: a prompt, a `SendBox`, the hint, OK and Cancel."""

    def __init__(self, parent: QWidget | None, title: str, label: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        text = QLabel(label)
        text.setWordWrap(True)
        lay.addWidget(text)
        self.box = SendBox()
        self.box.submitted.connect(self.accept)
        lay.addWidget(self.box, 1)
        lay.addWidget(muted(QLabel(SEND_HINT)))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)


def ask_text(parent: QWidget | None, title: str, label: str) -> tuple[str, bool]:
    """`QInputDialog.getMultiLineText`'s shape, with a `MessageDialog`."""
    d = MessageDialog(parent, title, label)
    ok = bool(d.exec())                 # Accepted is 1, Rejected 0
    return d.box.toPlainText(), ok


def open_session(parent: QWidget, project: Project, item_id: str, session_id: str | None) -> None:
    """Open in terminal: `claude --resume` of a finished item, in a new console."""
    if not session_id:
        QMessageBox.warning(parent, "Open in terminal", f"{item_id}'s finish event names no session.")
        return
    try:
        cwd, _ = actions.open_session(project.cfg, item_id, session_id)
    except OSError as exc:
        QMessageBox.warning(parent, "Open in terminal", str(exc))
        return
    if hasattr(parent, "notify"):
        parent.notify(f"opened {item_id}'s session ({session_id}) in a new console, in {cwd}")


class Panel(QWidget):
    """Base: knows its project and how to report what an action did."""
    open_item = Signal(str)

    def __init__(self, project: Project, notify: Callable[[str], None]):
        super().__init__()
        self.project = project
        self.notify = notify

    @property
    def control(self):
        return self.project.control

    def signal_sent(self, what: str, path) -> None:
        self.notify(f"{what}: wrote signals/{path.name}; the daemon applies it and logs an event")
