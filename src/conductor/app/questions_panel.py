"""The Questions tab: one card per open ask, answered in place."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QSplitter,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import actions, theme
from .model import Project, Snapshot
from .widgets import SEND_HINT, FlowLayout, Panel, SendBox, countdown, muted, when


class QuestionCard(QGroupBox):
    def __init__(self, panel: "QuestionsPanel", q):
        super().__init__()
        self.setProperty("card", True)
        self._severity = None
        self.q = q
        self.panel = panel
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        # Every label wraps and the options flow onto new rows: nothing in the card may be
        # wider than the window, or the Answer button hides behind a sideways scroll.
        head = muted(QLabel(f"{q.id}   ·   {q.item}   ·   asked {when(q.asked)}"))
        head.setWordWrap(True)
        lay.addWidget(head)
        # The status is a banner (tinted, larger than the question) so the deadline or the
        # block reads as state, not as more question text.
        self.status = QLabel()
        self.status.setWordWrap(True)
        f = self.status.font()
        f.setPointSizeF(theme.BASE_POINT_SIZE * 1.25)
        f.setBold(True)
        self.status.setFont(f)
        lay.addWidget(self.status)
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
            holder = QWidget()
            row = FlowLayout(holder)
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
            lay.addWidget(holder)
        row = QHBoxLayout()
        self.box = SendBox(lines=3)
        self.box.setPlaceholderText("Your answer (written to questions/<id>.answer.md)")
        self.box.submitted.connect(self._answer)
        col = QVBoxLayout()
        col.addWidget(self.box)
        col.addWidget(muted(QLabel(SEND_HINT)))
        row.addLayout(col, 1)
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
        if not self.send.isEnabled():           # Enter in the box after it was answered
            return
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
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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
        # Text is the stretched last column: a long one elides, its whole text on hover.
        self.resolved.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.resolved.setTextElideMode(Qt.TextElideMode.ElideRight)
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
                text = str(r.get("text") or r.get("reason") or "")
                row = QTreeWidgetItem([q.id, q.item, str(r.get("outcome")), at, " ".join(text.split())])
                row.setToolTip(4, text)
                self.resolved.addTopLevelItem(row)
            for c in range(4):
                self.resolved.resizeColumnToContents(c)
            # An empty Resolved list collapses to one line, leaving the room to open cards.
            empty = not snap.resolved_questions
            self.resolved.setVisible(not empty)
            self.none_resolved.setVisible(empty)
            self.bottom.setMaximumHeight(self.bottom.sizeHint().height() if empty else 16777215)
