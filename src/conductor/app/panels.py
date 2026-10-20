"""The four panels of a project: Repos, Questions, Events, Proposed.

Each panel is fed a `model.Snapshot` (and the Events panel new events) by the window's
timer, and rebuilds its widgets only when the structure changes, so selections, typed
answers and scroll positions survive the refresh.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QAbstractTableModel, QDateTime, QEvent, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetricsF, QKeySequence, QPalette, QShortcut, QSyntaxHighlighter,
    QTextCharFormat, QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDateTimeEdit, QGroupBox, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSplitter, QStyle, QStyledItemDelegate, QTableView, QTableWidget, QTableWidgetItem, QTabWidget,
    QTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import audits, items
from ..events import RED_KINDS, Event
from ..inbox import describe
from ..util import clock, duration, now, parse_stamp
from . import actions, theme
from .model import ACTIVITY_KINDS, ALWAYS_KINDS, Project, Snapshot, activity_text, git_trail

RED = QColor(theme.RED)
AMBER = QColor(theme.AMBER)
_GIT_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="conductor-git")
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


def compact_buttons(*specs: tuple) -> QWidget:
    """Small buttons side by side for one tree cell: each spec is (text, slot, tone).
    `holder.buttons` maps text → button; `holder.button` is the first."""
    holder = QWidget()
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(4, 1, 4, 1)
    holder.buttons = {}
    for text, slot, tone in specs:
        b = QPushButton(text)
        b.setProperty("role", "compact")
        if tone:
            b.setProperty("tone", tone)
        if slot is not None:
            b.clicked.connect(slot)
        lay.addWidget(b)
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


# ===================================================================== Repos
class ReposPanel(Panel):
    COLS = ["Repo / item", "State", "Elapsed", "Note", ""]

    def __init__(self, project: Project, notify):
        super().__init__(project, notify)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(self.COLS)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.itemDoubleClicked.connect(self._double)
        h = self.tree.header()
        # The Note column takes the spare width; the button column stays button-sized.
        h.setStretchLastSection(False)
        for col in (0, 1, 2):
            h.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        h.resizeSection(4, 310)            # room for Mark reviewed, Pick… and Open in terminal
        self.tree.setItemDelegate(KeepColourDelegate(self.tree))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 8)
        hint = muted(QLabel("Double-click an item to open its trail. Clear, Cancel, Mark reviewed and Pick… are signals "
                            "the daemon carries out."))
        lay.addWidget(hint)
        lay.addWidget(self.tree)
        self._sig: tuple | None = None
        self._rows: dict[tuple, QTreeWidgetItem] = {}
        self._sessions: dict[str, str | None] = {}      # finished item → its session id
        self._picks: dict[str, str] = {}                # finished item → its manifest ref
        self._open: dict[tuple, bool] = {}              # repo row / group key → expanded, once clicked
        self.tree.itemExpanded.connect(lambda w: self._expanded(w, True))
        self.tree.itemCollapsed.connect(lambda w: self._expanded(w, False))

    # Which groups start open; a click on one is remembered from then on.
    GROUP_OPEN = {"flight": True, "finished": True, "earlier": False}

    def _plan(self, snap: Snapshot) -> list[tuple[str, str, str, list[tuple[tuple, list[str], str | None, str, tuple | None]]]]:
        """Per repo: (repo, summary, path, rows of (key, texts, item id, look, parent key)).
        A parent of None is the repo row; groups are `("group", repo, which)` rows, and
        each item row hangs under its group. The summary counts each item once, in the
        words its State cell uses."""
        at = now()
        out = []
        for rs in snap.repos:
            rows, counts = [], {}
            def count(state: str) -> None:
                word = state.split(" · ", 1)[0]
                counts[word] = counts.get(word, 0) + 1
            # A hold comes first: it is the cause of everything queued below it.
            if rs.red:
                rows.append((("red", rs.name), ["hold", "held after red", "",
                                                f"red {rs.red.item} (#{rs.red.id}, {clock(rs.red.ts)}): "
                                                f"{'; '.join(rs.red.detail.get('reasons', []))}"], rs.red.item, "red", None))
            elif rs.action == "hold":
                rows.append((("hold", rs.name), ["hold", "held", "", rs.reason], None, "hold", None))
            held = bool(rs.red) or rs.action == "hold"
            flight = ("group", rs.name, "flight")
            items = []
            for a in rs.running:
                running = a.phase == "running"
                note = " · ".join(x for x in (a.progress, f"on {a.detail}" if a.detail else "",
                                              f"port {a.port}" if a.port and running else "") if x)
                # A readonly item runs beside the others; the summary still counts it as running.
                phase = f"{a.phase} · readonly" if a.readonly and running else a.phase
                # Only an executing item takes a message: merging and concluding ones have exited.
                items.append((("run", a.item), [a.name, phase, duration(a.elapsed(at)), note], a.item,
                              "run" if a.phase == "running" else "run:after", flight))
            # A staged item stays in queue/ (it is the next to run): it gets one row, its
            # queue row, whose state carries the stage too.
            queued_ids = {it.id for it in rs.queued}
            stages = {a.item: a for a in rs.staged}
            for a in rs.staged:
                if a.item not in queued_ids:        # a stage whose item left the queue
                    staging = a.phase == "staging"
                    items.append((("stage", a.item), [a.name, a.phase, duration(a.elapsed(at)) if staging else "",
                                                      "" if staging else "plan ready; resumes when it can run"],
                                  a.item, "stage", flight))
            for i, it in enumerate(rs.queued):
                note, state, look, elapsed = "", "queued", "queued", ""
                if a := stages.get(it.id):
                    staging = a.phase == "staging"
                    state, look = a.phase, "stage"
                    elapsed = duration(a.elapsed(at)) if staging else ""
                    note = "" if staging else "plan ready; resumes when it can run"
                if i == 0 and held:
                    # the hold row above says why
                    state, look = (f"{state} · blocked by hold" if it.id in stages else "blocked by hold"), "blocked"
                elif i == 0:
                    state += " · next"
                    if rs.action in ("wait", "stage", "run", "resume"):
                        note = rs.action + (f": {rs.reason}" if rs.reason else "")
                if it.errors:
                    note = "invalid: " + "; ".join(it.errors)
                items.append((("queued", it.id), [it.name, state, elapsed, note], it.id, look, flight))
            if items:
                rows.append((flight, [f"In flight ({len(items)})", "", "", ""], None, "group", None))
                rows += items
                for _, texts, *_ in items:
                    count(texts[1])
            if rs.finished:
                n, k = rs.finished_total, rs.unreviewed
                since = "Finished since checkpoint" if snap.checkpoint else "Finished"
                label = f"{since} ({k} unreviewed)" if k == n else f"{since} ({n}, {k} unreviewed)" if k else f"{since} ({n})"
                group = ("group", rs.name, "finished")
                tip = "newest first" + (f"; the {len(rs.finished)} newest and every unreviewed one shown"
                                        if n > len(rs.finished) else "")
                rows.append((group, [label, "", "", ""], None, "group|" + tip, None))
            for f in rs.finished:
                mark = "reviewed" if f.reviewed else "unreviewed"
                note = f"finished {when(f.ts)}" + (f" · carry: {f.carry}" if f.carry not in (None, "none") else "")
                look = f"done:{f.status}:{mark}"
                if f.pick:
                    # The pick state is part of the look: a save changes the button's tone.
                    p = snap.picks.get(f.item)
                    note += " · pick: " + pick_note(p)
                    look += f"|pick:{p.state if p else 'waiting'}"
                rows.append((("done", f.item), [f.name, f"{f.status} · {mark}", "", note], f.item, look,
                             ("group", rs.name, "finished")))
            if rs.unreviewed:
                counts["unreviewed"] = rs.unreviewed
            if rs.earlier:
                # Settled by a checkpoint: finished and reviewed before it.
                cp = f"checkpoint #{snap.checkpoint.id}" if snap.checkpoint else "the checkpoint"
                more = f"; the newest {len(rs.earlier)} shown" if rs.earlier_total > len(rs.earlier) else ""
                rows.append((("group", rs.name, "earlier"), [f"Earlier checkpoints ({rs.earlier_total})", "", "", ""],
                             None, f"group|finished and reviewed before {cp}{more}", None))
            for f in rs.earlier:
                note = f"finished {when(f.ts)}" + (f" · carry: {f.carry}" if f.carry not in (None, "none") else "")
                look = f"done:{f.status}:reviewed"
                if f.pick:                          # settled: no longer waits, but reopens
                    note += f" · pick: {f.pick}"
                    look += "|pick:settled"
                rows.append((("old", f.item), [f.name, f"{f.status} · reviewed", "", note], f.item, look,
                             ("group", rs.name, "earlier")))
            parts = (["held after red"] if rs.red else ["held"] if rs.action == "hold" else [])
            parts += [f"{n} {word}" for word, n in counts.items()] or ([] if parts else ["idle"])
            if waiting := sum(1 for p in snap.picks.values() if p.item.split("/", 1)[0] == rs.name and p.state != "saved"):
                parts.append(f"{waiting} to pick")
            # The audit marker last, so it reads as its own: time for a cleanup when convenient.
            head = " · ".join(parts + ([audits.pending(rs.audit_notes)] if rs.audit_notes else []))
            out.append((rs.name, head, str(rs.path), rows))
        return out

    def update_snapshot(self, snap: Snapshot) -> None:
        self._sessions = {f.item: f.session_id for rs in snap.repos for f in (*rs.finished, *rs.earlier)}
        self._picks = {f.item: f.pick for rs in snap.repos for f in (*rs.finished, *rs.earlier) if f.pick}
        plan = self._plan(snap)
        sig = tuple((repo, tuple((k, look) for k, _, _, look, _ in rows)) for repo, _, _, rows in plan)
        if sig != self._sig:
            self._rebuild(plan)
            self._sig = sig
        # Elapsed is empty unless something runs or stages: hide it then.
        self.tree.setColumnHidden(2, not any(rs.running or rs.staged for rs in snap.repos))
        for repo, note, _, rows in plan:
            top = self._rows[("repo", repo)]
            top.setText(3, note)
            # An audit pending marker draws the header note amber; it clears with the notes.
            top.setForeground(3, QBrush(QColor(theme.AMBER if "audit pending" in note else theme.MUTED)))
            for key, texts, _, _, _ in rows:
                w = self._rows[key]
                for col, t in enumerate(texts):
                    if w.text(col) != t:
                        w.setText(col, t)

    def _rebuild(self, plan) -> None:
        self.tree.clear()
        self._rows = {}
        bold = self.tree.font()
        bold.setBold(True)
        band = QBrush(QColor(theme.BAND))
        # Per row look: (state-column colour, whole-row colour, row background, bold).
        looks = {"run": (theme.BLUE, None, None, True), "run:after": (theme.BLUE, None, None, True), "stage": (theme.TEAL, None, None, True),
                 "queued": (theme.MUTED, None, None, False), "idle": (theme.MUTED, None, None, False),
                 "blocked": (theme.AMBER, None, None, True),
                 "red": (None, theme.RED, theme.RED_BG, True), "hold": (None, theme.AMBER, theme.AMBER_BG, True)}
        status_fg = {"done": theme.GREEN, "partial": theme.AMBER, "red": theme.RED}
        for status, fg in status_fg.items():
            # A finished item: its state coloured by outcome, bold until reviewed; a
            # reviewed one reads as history (muted).
            looks[f"done:{status}:unreviewed"] = (fg, None, None, True)
            looks[f"done:{status}:reviewed"] = (None, theme.MUTED, None, False)
        for repo, note, path, rows in plan:
            top = QTreeWidgetItem([repo, "", "", note])
            for c in range(len(self.COLS)):
                top.setBackground(c, band)
                top.setToolTip(c, path)
            top.setFont(0, bold)
            top.setForeground(3, QBrush(QColor(theme.MUTED)))
            self.tree.addTopLevelItem(top)
            self._rows[("repo", repo)] = top
            for key, texts, item_id, look, parent in rows:
                w = QTreeWidgetItem(texts)
                w.setData(0, Qt.ItemDataRole.UserRole, item_id)
                (self._rows[parent] if parent else top).addChild(w)
                self._rows[key] = w
                if key[0] == "group":
                    # A group is a node, not an item: muted bold label, nothing else.
                    w.setToolTip(0, look.partition("|")[2])
                    w.setFont(0, bold)
                    w.setForeground(0, QBrush(QColor(theme.MUTED)))
                    continue
                look, _, pick_state = look.partition("|pick:")
                state, row, bg, strong = looks.get(look, (None, None, None, False))
                for c in range(len(self.COLS)):
                    if row:
                        w.setForeground(c, QBrush(QColor(row)))
                    if bg:
                        w.setBackground(c, QBrush(QColor(bg)))
                if state:
                    w.setForeground(1, QBrush(QColor(state)))
                if strong:
                    w.setFont(1, bold)
                if key[0] == "red":
                    # The one action that releases the hold: drawn as the row's main action.
                    self.tree.setItemWidget(w, 4, compact_button("Clear hold", lambda _=False, r=repo: self._clear(r),
                                                                 tone="danger"))
                elif key[0] == "queued":
                    self.tree.setItemWidget(w, 4, compact_buttons(
                        ("Cancel", lambda _=False, i=key[1]: self._cancel(i), None),
                        ("Amend…", lambda _=False, i=key[1]: self._amend(i), None)))
                elif key[0] == "run" and look == "run":
                    self.tree.setItemWidget(w, 4, compact_buttons(
                        ("Tell…", lambda _=False, i=key[1]: self._tell(i), None),
                        ("Amend…", lambda _=False, i=key[1]: self._amend(i), None)))
                elif key[0] in ("done", "old"):
                    specs = [("Open in terminal", lambda _=False, i=key[1]: self._open_session(i), None)]
                    if pick_state and (ref := self._picks.get(key[1])):
                        # Amber until saved: the picker waits for Matt.
                        specs.insert(0, ("Pick…", lambda _=False, r=ref: self._pick(r),
                                         None if pick_state in ("saved", "settled") else "default"))
                    if look.endswith(":unreviewed"):
                        specs.insert(0, ("Mark reviewed", None, None))
                    holder = compact_buttons(*specs)
                    if b := holder.buttons.get("Mark reviewed"):
                        b.clicked.connect(lambda _=False, i=key[1], b=b: self._reviewed(i, b))
                    holder.buttons["Open in terminal"].setEnabled(bool(self._sessions.get(key[1])))
                    self.tree.setItemWidget(w, 4, holder)
        self.tree.blockSignals(True)            # not a click: the remembered state stays
        for key, w in self._rows.items():
            if key[0] in ("repo", "group"):
                w.setExpanded(self._open.get(key, key[0] == "repo" or self.GROUP_OPEN[key[2]]))
        self.tree.blockSignals(False)

    def _expanded(self, w: QTreeWidgetItem, on: bool) -> None:
        """Remember each repo row's and group's open state across rebuilds."""
        for key, row in self._rows.items():
            if row is w and key[0] in ("repo", "group"):
                self._open[key] = on

    def _double(self, w: QTreeWidgetItem, _col: int) -> None:
        if item_id := w.data(0, Qt.ItemDataRole.UserRole):
            self.open_item.emit(item_id)

    def _clear(self, repo: str) -> None:
        self.signal_sent(f"clear {repo}", actions.clear(self.control, repo))

    def _tell(self, item_id: str) -> None:
        text, ok = QInputDialog.getMultiLineText(
            self, f"Message to {item_id}",
            f"Message for the running executor of {item_id}.\nIt arrives after its next tool call; "
            "it answers through a progress line.")
        if ok and text.strip():
            self.signal_sent(f"message to {item_id}", actions.tell(self.control, item_id, text))

    def _amend(self, item_id: str) -> None:
        text, ok = QInputDialog.getMultiLineText(
            self, f"Amend {item_id}",
            f"An addition to {item_id}'s prompt; where they conflict, it wins.\nQueued: appended at "
            "launch. Staged: added to the resume message, the stage kept. Running: arrives after its "
            "next tool call.")
        if ok and text.strip():
            path = actions.amend(self.control, item_id, text)
            self.notify(f"amendment to {item_id}: wrote queue/{path.parent.name}/{path.name}; "
                        "the daemon delivers it and logs `amended`")

    def _open_session(self, item_id: str) -> None:
        open_session(self, self.project, item_id, self._sessions.get(item_id))

    def _pick(self, ref: str) -> None:
        self.signal_sent(f"pick {ref} (opens in the browser)", actions.pick(self.control, ref))

    def _reviewed(self, item_id: str, button: QPushButton | None = None) -> None:
        if button is not None:
            button.setEnabled(False)            # the row turns reviewed once the daemon logs it
        self.signal_sent(f"mark {item_id} reviewed", actions.reviewed(self.control, item_id))

    def _cancel(self, item_id: str) -> None:
        if confirm(self, "Cancel queued item",
                   f"Cancel {item_id}?\n\nThe daemon moves it to .conductor/cancelled/ (kept, not deleted)."):
            self.signal_sent(f"cancel {item_id}", actions.cancel(self.control, item_id))


# ===================================================================== Questions
class QuestionCard(QGroupBox):
    def __init__(self, panel: "QuestionsPanel", q):
        super().__init__()
        self.setProperty("card", True)
        self._severity = None
        self.q = q
        self.panel = panel
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.addWidget(muted(QLabel(f"{q.id}   ·   {q.item}   ·   asked {when(q.asked)}")))
        # The status is a badge (tinted, larger than the question) so the deadline or the
        # block reads as state, not as more question text.
        self.status = QLabel()
        f = self.status.font()
        f.setPointSizeF(theme.BASE_POINT_SIZE * 1.25)
        f.setBold(True)
        self.status.setFont(f)
        lay.addWidget(self.status, 0, Qt.AlignmentFlag.AlignLeft)
        opts = [ln[2:].strip() for ln in q.body.split("\nOptions:", 1)[-1].splitlines()
                if "\nOptions:" in q.body and ln.startswith("- ")]
        # Options that become buttons are not listed again in the body.
        text = q.body.split("\nOptions:", 1)[0] if opts else q.body
        body = QLabel(text.strip())
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        f = body.font()
        f.setPointSizeF(theme.BASE_POINT_SIZE + 1)
        body.setFont(f)
        lay.addWidget(body)
        if opts:
            row = QHBoxLayout()
            row.addWidget(QLabel("Use option:"))
            for o in opts:
                label = o if len(o) < 40 else o[:37] + "…"
                b = QPushButton(label)
                if not q.block and q.default is not None and o == str(q.default):
                    b.setText(f"{label} (default)")
                    b.setProperty("tone", "default")
                b.setToolTip(o)
                b.clicked.connect(lambda _=False, o=o: self.box.setPlainText(o))
                row.addWidget(b)
            row.addStretch(1)
            lay.addLayout(row)
        row = QHBoxLayout()
        self.box = QPlainTextEdit()
        self.box.setPlaceholderText("Your answer (written to questions/<id>.answer.md)")
        self.box.setFixedHeight(64)
        row.addWidget(self.box, 1)
        self.send = QPushButton("Answer")
        self.send.setProperty("role", "primary")
        self.send.setMinimumHeight(34)
        self.send.clicked.connect(self._answer)
        row.addWidget(self.send, 0, Qt.AlignmentFlag.AlignBottom)
        lay.addLayout(row)
        self.tick()

    def tick(self) -> None:
        if self.q.answer_path.exists():
            severity = "answered"
            self.status.setText("answered — waiting for the executor to pick it up")
            self.send.setEnabled(False)
        elif self.q.block:
            severity = "block"
            self.status.setText("BLOCKING — the item waits until you answer")
        else:
            severity = "default"
            self.status.setText(f"default in {countdown(self.q.deadline)} (at {when(self.q.deadline)}): {self.q.default}")
        if severity != self._severity:
            self._severity = severity
            self.status.setProperty("pill", severity)
            theme.repolish(self.status)
            self.setProperty("severity", severity)
            theme.repolish(self)

    def _answer(self) -> None:
        text = self.box.toPlainText().strip()
        try:
            q = actions.answer(self.panel.control, self.q.id, text)
        except actions.AnswerError as exc:
            QMessageBox.warning(self, "Answer", str(exc))
            return
        note = actions.answer_note(q)
        self.panel.notify(note)
        if not q.is_open:               # the default (or a close) won the race: say so
            QMessageBox.information(self, "Answer", note)
        self.tick()


class QuestionsPanel(Panel):
    def __init__(self, project: Project, notify):
        super().__init__(project, notify)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 8)
        split = QSplitter(Qt.Orientation.Vertical)
        lay.addWidget(split)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(self._heading("Open"))
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.holder = QWidget()
        self.cards_lay = QVBoxLayout(self.holder)
        self.cards_lay.setContentsMargins(0, 0, 4, 0)
        self.cards_lay.setSpacing(10)
        self.cards_lay.addStretch(1)
        self.scroll.setWidget(self.holder)
        tl.addWidget(self.scroll)
        self.empty = muted(QLabel("No open questions."))
        tl.addWidget(self.empty)
        split.addWidget(top)
        self.bottom = bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 6, 0, 0)
        bl.addWidget(self._heading("Resolved"))
        self.resolved = QTreeWidget()
        self.resolved.setHeaderLabels(["Question", "Item", "Outcome", "At", "Text"])
        self.resolved.setRootIsDecorated(False)
        self.resolved.setAlternatingRowColors(True)
        self.resolved.itemDoubleClicked.connect(
            lambda w, _c: self.open_item.emit(w.text(1)) if w.text(1) else None)
        bl.addWidget(self.resolved)
        self.none_resolved = muted(QLabel("None yet."))
        bl.addWidget(self.none_resolved)
        split.addWidget(bottom)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([480, 160])
        self.cards: dict[str, QuestionCard] = {}
        self._resolved_sig: tuple | None = None
        self.timer = QTimer(self)                 # the countdown moves every second
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)

    @staticmethod
    def _heading(text: str) -> QLabel:
        h = QLabel(text)
        h.setStyleSheet(f"font-weight: bold; font-size: {theme.BASE_POINT_SIZE + 1.5}pt")
        return h

    def tick(self) -> None:
        for c in self.cards.values():
            c.tick()

    @staticmethod
    def _urgency(q) -> tuple:
        """Blocking questions first, then the soonest default; answered ones last."""
        return (q.answer_path.exists(), not q.block, str(q.deadline or "~"), q.asked)

    def update_snapshot(self, snap: Snapshot) -> None:
        ordered = sorted(snap.open_questions, key=self._urgency)
        open_ids = [q.id for q in ordered]
        if open_ids != list(self.cards):
            drafts = {qid: c.box.toPlainText() for qid, c in self.cards.items()}
            for c in self.cards.values():
                c.setParent(None)
                c.deleteLater()
            self.cards = {}
            for q in ordered:
                card = QuestionCard(self, q)
                card.box.setPlainText(drafts.get(q.id, ""))
                self.cards_lay.insertWidget(self.cards_lay.count() - 1, card)
                self.cards[q.id] = card
        self.empty.setVisible(not self.cards)
        self.scroll.setVisible(bool(self.cards))
        sig = tuple((q.id, q.result.get("outcome")) for q in snap.resolved_questions)
        if sig != self._resolved_sig:
            self._resolved_sig = sig
            self.resolved.clear()
            for q in snap.resolved_questions:
                r = q.result
                at = when(r.get("at"))
                self.resolved.addTopLevelItem(QTreeWidgetItem(
                    [q.id, q.item, str(r.get("outcome")), at, str(r.get("text") or r.get("reason") or "")]))
            for c in range(4):
                self.resolved.resizeColumnToContents(c)
            # An empty Resolved list collapses to one line, leaving the room to open cards.
            empty = not snap.resolved_questions
            self.resolved.setVisible(not empty)
            self.none_resolved.setVisible(empty)
            self.bottom.setMaximumHeight(self.bottom.sizeHint().height() if empty else 16777215)


# ===================================================================== Events
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


class TrailHighlighter(QSyntaxHighlighter):
    """Formats the trail's Events tab without touching its text: each event's header line
    (`#id  time  kind  what`) bold with its kind coloured, the focused (▶) event and its
    muted detail lines on the selection colour."""

    def __init__(self, doc):
        super().__init__(doc)
        self._formats: dict[tuple, QTextCharFormat] = {}

    def _fmt(self, fg: str | None, bold: bool, on: bool) -> QTextCharFormat:
        key = (fg, bold, on)
        if key not in self._formats:
            f = QTextCharFormat()
            if fg:
                f.setForeground(QColor(fg))
            if bold:
                f.setFontWeight(700)
            if on:
                f.setBackground(QColor(theme.SELECT))
            self._formats[key] = f
        return self._formats[key]

    # "▶ #id  <clock>  <kind> <what>": the header `TrailView._event_text` writes (an
    # event not tied to an item is shown without the 2-character marker).
    HEAD = re.compile(r"^(?:▶ |  )?#\d+ +\S.*?[ap]m +(\S+)")
    STATUS = re.compile(r"finished \*\*(\w+)\*\*")

    def highlightBlock(self, text: str) -> None:
        if m := self.HEAD.match(text):
            on = text[0] == "▶"
            kind_at, kind_end = m.span(1)
            kind = m.group(1)
            status = (s.group(1) if (s := self.STATUS.search(text, kind_end)) else None) if kind == "finish" else None
            fg = theme.kind_style(kind, RED_KINDS, status)[0]
            self.setFormat(0, kind_at, self._fmt(None, True, on))
            self.setFormat(kind_at, kind_end - kind_at, self._fmt(fg, True, on))
            self.setFormat(kind_end, len(text) - kind_end,
                           self._fmt(theme.RED if kind in RED_KINDS else None, False, on))
        elif text.startswith(TrailView.INDENT):
            # Only the focused event has detail lines: they share its band.
            self.setFormat(0, len(text), self._fmt(theme.MUTED, False, True))


class TrailView(QWidget):
    """An item's whole trail: prompt, every event, report, executor log(s), git.

    Built once per item. Moving to another event of the same item only re-renders the
    Events tab; new events for the open item refresh each tab in place (scroll kept,
    a tab whose text is unchanged untouched). Git runs on a worker thread.
    """
    TABS = ["Events", "Prompt", "Report", "Log", "Stage log", "Git"]
    INDENT = "      "                         # the focused event's detail lines

    def __init__(self, project: Project):
        super().__init__()
        self.project = project
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 0, 0, 0)
        self.title = QLabel("Select an event to see its item's trail.")
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.title.setStyleSheet(f"font-size: {theme.BASE_POINT_SIZE + 0.5}pt; padding: 2px 0 4px 0")
        head = QHBoxLayout()
        head.addWidget(self.title, 1)
        # A finished item's session, resumed interactively; never a live one (it would fork).
        self.open_btn = QPushButton("Open in terminal")
        self.open_btn.setToolTip("claude --resume <session> in a new console: in the item's worktree if it "
                                 "still exists, else the repo's main checkout")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_session)
        head.addWidget(self.open_btn, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)
        self.tabs = QTabWidget()
        self.text: dict[str, QPlainTextEdit | QTextEdit] = {}
        for name in self.TABS:
            # Events is a QTextEdit: its layout honours block margins, which give each
            # wrapped line a hanging indent (`_indent`). The rest stay plain (fast on big logs).
            t = QTextEdit() if name == "Events" else QPlainTextEdit()
            t.setReadOnly(True)
            t.setFont(mono())
            if name == "Events":
                t.setAcceptRichText(False)
                t.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            else:
                t.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap if name in ("Git",) else QPlainTextEdit.LineWrapMode.WidgetWidth)
            self.tabs.addTab(t, name)
            self.text[name] = t
        self._highlighter = TrailHighlighter(self.text["Events"].document())
        self.text["Events"].installEventFilter(self)
        self.tabs.currentChanged.connect(self._tab_changed)
        lay.addWidget(self.tabs)
        self.item: str | None = None
        self.focus_id: int | None = None
        self._trail = None
        self._git_for: tuple | None = None
        self._git_job: tuple | None = None          # (key, future)
        self._git_timer = QTimer(self)
        self._git_timer.setInterval(50)
        self._git_timer.timeout.connect(self._poll_git)

    def eventFilter(self, obj, event) -> bool:
        # A resize can cross the width at which hanging under `what` gives way.
        if event.type() == QEvent.Type.Resize and obj is self.text["Events"]:
            self._indent()
        return False

    def show_event(self, ev: Event) -> None:
        if ev.item:
            if ev.item == self.item and self._trail is not None:
                self.focus_id = ev.id               # same item: only the focus moves
                self._render_events(center=True)
                return
            self.show_item(ev.item, focus=ev)
            return
        self.item, self.focus_id, self._trail = None, None, None
        self.open_btn.setEnabled(False)
        self.title.setText(f"<b>#{ev.id}</b> {clock(ev.ts)} <b>{ev.kind}</b> — not tied to an item")
        self.text["Events"].setPlainText(self._event_text(ev, full=True))
        self._indent()
        for name in self.TABS[1:]:
            self.text[name].setPlainText("")
        self._git_for = None

    def show_item(self, item_id: str, focus: Event | None = None) -> None:
        self.item = item_id
        self.focus_id = focus.id if focus else None
        self._load(fresh=True)

    def refresh_item(self) -> None:
        """New events arrived for the open item: update in place."""
        if self.item:
            self._load(fresh=False)

    def _load(self, fresh: bool) -> None:
        tr = self._trail = self.project.trail(self.item)
        reds = [e for e in tr.events if e.kind in RED_KINDS]
        status = next((e.detail.get("status") for e in reversed(tr.events) if e.kind == "finish"), None)
        bits = [f"<b>{self.item}</b>", f"now in {tr.where}", f"{len(tr.events)} event(s)"]
        if status:
            bits.append(f"finished <b>{status}</b>")
        if reds:
            bits.append(f"<b style='color:{theme.RED}'>{len(reds)} red</b>")
        self.title.setText(" · ".join(bits))
        self.open_btn.setEnabled(self.session_id() is not None)
        self._render_events(center=fresh)
        self._set("Prompt", tr.prompt or "(no prompt file: not launched and not in any queue folder)", fresh)
        self._set("Report", tr.report or "(no report)", fresh)
        self._set("Log", tr.log or "(no executor log)", fresh)
        self._set("Stage log", tr.stage_log or "(not staged)", fresh)
        if fresh:
            self.text["Git"].setPlainText("")
            self._git_for = None
        self._tab_changed(self.tabs.currentIndex())

    def _set(self, name: str, text: str, fresh: bool, after: Callable[[], None] | None = None) -> None:
        """Replace a tab's text; unless `fresh`, keep its scroll position (or stay at the
        bottom of a growing log), and leave it alone when nothing changed. `after` runs
        once new text is in, before the scroll position is restored."""
        w = self.text[name]
        if fresh:
            w.setPlainText(text)
            if after:
                after()
            return
        if w.toPlainText() == text:
            return
        sb = w.verticalScrollBar()
        value, bottom = sb.value(), sb.maximum() > 0 and sb.value() >= sb.maximum() - 2
        w.setPlainText(text)
        if after:
            after()
        sb.setValue(sb.maximum() if bottom else value)

    def _render_events(self, center: bool) -> None:
        lines, focus_block, blocks = [], None, 0
        width = len(str(max((e.id for e in self._trail.events), default=0)))
        kinds = max((len(e.kind) for e in self._trail.events), default=0)
        for e in self._trail.events:
            on = e.id == self.focus_id
            # Free text (answers, reasons) can hold line breaks: count the blocks each
            # entry really takes (plus the blank line between entries), so the cursor
            # lands on the ▶ line.
            t = ("▶ " if on else "  ") + self._event_text(e, full=on, width=width, kinds=kinds, short=True)
            t = t.replace("\r\n", "\n").replace("\r", "\n").replace(" ", "\n").replace(" ", "\n")
            if on:
                focus_block = blocks
            blocks += t.count("\n") + 2
            lines.append(t)
        text = "\n\n".join(lines) or "no events for this item yet"
        w = self.text["Events"]
        if not (center and focus_block is not None):
            self._set("Events", text, fresh=center, after=self._indent)
            return
        w.setPlainText(text)
        self._indent()
        w.setTextCursor(QTextCursor(w.document().findBlockByNumber(focus_block)))
        # QTextEdit has no centerCursor(): scroll so the ▶ line sits mid-view.
        sb = w.verticalScrollBar()
        sb.setValue(sb.value() + w.cursorRect().center().y() - w.viewport().height() // 2)

    def _indent(self) -> None:
        """Hanging indents for the Events tab: a header's wrapped lines start under its
        `what` text, a detail line's under its own text, and the extra lines of a
        multi-line answer or reason under the `what` column too. When the pane is too
        narrow to leave ~20 characters beside that column, wrapped lines hang under the
        detail indent instead (re-applied on resize)."""
        w = self.text["Events"]
        doc = w.document()
        plain = QFontMetricsF(w.font())
        bold_font = QFont(w.font())
        bold_font.setBold(True)
        bold = QFontMetricsF(bold_font)
        # Measured as if the scroll bar were shown, so the choice cannot flip the bar on
        # and off.
        room = (w.contentsRect().width() - w.verticalScrollBar().sizeHint().width()
                - 2 * doc.documentMargin() - 20 * plain.horizontalAdvance("0"))
        fallback = plain.horizontalAdvance(self.INDENT)
        column = 0.0                                # the current entry's `what` column
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        block = doc.begin()
        while block.isValid():
            text = block.text()
            margin, indent = 0.0, 0.0
            if m := TrailHighlighter.HEAD.match(text):
                what = len(text) - len(text[m.end(1):].lstrip(" "))
                column = bold.horizontalAdvance(text[:m.end(1)]) + plain.horizontalAdvance(text[m.end(1):what])
                if column > room:
                    column = fallback
                margin, indent = column, -column
            elif text.startswith(self.INDENT):
                margin = plain.horizontalAdvance(self.INDENT)
                indent = -margin
            elif text:
                margin = column                     # a free-text line break inside an entry
            fmt = block.blockFormat()
            if fmt.leftMargin() != margin or fmt.textIndent() != indent:
                fmt.setLeftMargin(margin)
                fmt.setTextIndent(indent)
                cursor.setPosition(block.position())
                cursor.setBlockFormat(fmt)
            block = block.next()
        cursor.endEditBlock()

    @classmethod
    def _event_text(cls, e: Event, full: bool = False, width: int = 0, kinds: int = 15,
                    short: bool = False) -> str:
        """`#id  time  kind  what`, plus (when `full`) the event's every field; the id and
        kind fields are `width` and `kinds` wide. `short` drops the item the trail's title
        already names and cuts long paths to their last two parts; the detail block keeps
        them whole."""
        what = summary(e) if short else describe(e)
        text = f"#{e.id:<{width}}  {clock(e.ts):>7}  {e.kind:<{kinds}}  {what}"
        if full:
            pad = cls.INDENT
            text += f"\n{pad}ts {e.ts}"
            if e.paths:
                text += f"\n{pad}paths {', '.join(e.paths)}"
            text += f"\n{pad}" + json.dumps(e.detail, indent=1, ensure_ascii=False).replace("\n", f"\n{pad}")
        return text

    def session_id(self) -> str | None:
        """The open item's session, once it has finished and is not running or queued again."""
        tr = self._trail
        if tr is None or tr.where not in ("done", "gone"):
            return None
        fin = next((e for e in reversed(tr.events) if e.kind == "finish"), None)
        return fin.detail.get("session_id") if fin else None

    def _open_session(self) -> None:
        if self.item:
            open_session(self.window(), self.project, self.item, self.session_id())

    def _tab_changed(self, index: int) -> None:
        # Git is the one slow part: computed only when its tab is looked at, again only
        # when the item has new events, and never on the UI thread.
        if self.TABS[index] != "Git" or not self.item or self._trail is None:
            return
        key = (self.item, len(self._trail.events))
        if key == self._git_for:
            return
        first = self._git_for is None or self._git_for[0] != self.item
        self._git_for = key
        if first:
            self.text["Git"].setPlainText("(reading git …)")
        if self._git_job:
            self._git_job[1].cancel()              # superseded; no-op once it started
        self._git_job = (key, _GIT_POOL.submit(git_trail, self.project.cfg, self.item, list(self.project.events)))
        self._git_timer.start()

    def _poll_git(self) -> None:
        if not self._git_job:
            self._git_timer.stop()
            return
        key, fut = self._git_job
        if not fut.done():
            return
        self._git_timer.stop()
        self._git_job = None
        if key != self._git_for:
            return
        try:
            text = fut.result()
        except Exception as exc:                    # noqa: BLE001 - shown, never raised
            text = f"git: {exc!r}"
        self._set("Git", text, fresh=False)

    def wait_git(self, timeout: float = 30.0) -> None:
        """Block until the Git tab's text is in (tests, screenshots)."""
        import time
        end = time.monotonic() + timeout
        while self._git_job and time.monotonic() < end:
            self._git_job[1].result(timeout=max(end - time.monotonic(), 0.01))
            self._poll_git()


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


# ===================================================================== Proposed
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
