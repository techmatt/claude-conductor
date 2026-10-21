"""An item's detail: its event trail, git history and log (`TrailView`), and the Conversation
view the Repos tab shows first."""

from __future__ import annotations

import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTabWidget, QTextBrowser, QTextEdit,
                               QVBoxLayout, QWidget)

from ..events import RED_KINDS, Event
from ..inbox import describe, outline_rows
from ..resume import label as resume_label
from ..util import clock
from . import theme
from .model import Project, conversation, current_outline, git_trail
from .widgets import mono, muted, open_session, summary, when

_GIT_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="conductor-git")


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


class ConversationView(QWidget):
    """One item as a chat thread (`model.conversation`): Matt's messages and amendments on
    the right with their replies under them, the executor's questions and progress on
    the left, conductor's start, picker and finish lines across. The latest outline is
    pinned above the thread. Tell…, Outline? and Amend… at the bottom act on the item on
    screen. It follows the newest entry unless Matt scrolled up."""
    report = Signal()               # the finish's "Report" link
    tell = Signal(str)
    outline = Signal(str)
    amend = Signal(str)

    LOOK = {"matt": (theme.SELECT, "Matt"), "executor": (theme.BASE, "executor"), "conductor": (theme.BAND, "conductor")}
    TONE = {"wait": theme.MUTED, "warn": theme.AMBER, "red": theme.RED, "done": theme.GREEN,
            "partial": theme.AMBER}

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        # Outside the scrolling thread, so it stays in view.
        self.pin = QLabel()
        self.pin.setWordWrap(True)
        self.pin.setTextFormat(Qt.TextFormat.RichText)
        self.pin.setStyleSheet(f"QLabel {{ background: {theme.BASE}; padding: 6px 8px; "
                               f"border-bottom: 1px solid {theme.MUTED}; }}")
        self.pin.hide()
        lay.addWidget(self.pin)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(lambda url: self.report.emit() if url.toString() == "report" else None)
        self.view.setStyleSheet(f"QTextBrowser {{ background: {theme.WINDOW}; }}")
        lay.addWidget(self.view, 1)
        bar = QHBoxLayout()
        self.tell_btn = QPushButton("Tell…")
        self.tell_btn.setToolTip("A message to the running executor; it arrives after its next tool call "
                                 "and the executor answers with a progress line, shown under it here")
        self.amend_btn = QPushButton("Amend…")
        self.amend_btn.setToolTip("An addition to this item's prompt: queued, appended at launch; staged, "
                                  "in the resume message; running, after its next tool call")
        self.outline_btn = QPushButton("Outline?")
        self.outline_btn.setToolTip("Ask the running executor for an updated outline: its remaining "
                                    "sections and finish times, pinned above the thread when it answers")
        self.tell_btn.clicked.connect(lambda: self.item and self.tell.emit(self.item))
        self.outline_btn.clicked.connect(lambda: self.item and self.outline.emit(self.item))
        self.amend_btn.clicked.connect(lambda: self.item and self.amend.emit(self.item))
        self.hint = muted(QLabel())
        bar.addWidget(self.hint, 1)
        bar.addWidget(self.tell_btn)
        bar.addWidget(self.outline_btn)
        bar.addWidget(self.amend_btn)
        lay.addLayout(bar)
        self.item: str | None = None
        self.turns: list = []
        self._html = ""
        self.set_actions(False, False)

    def set_actions(self, tell: bool, amend: bool) -> None:
        self.tell_btn.setEnabled(tell)
        self.outline_btn.setEnabled(tell)           # the Tell rule: only while it executes
        self.amend_btn.setEnabled(amend)
        self.hint.setText("" if tell or amend else "Tell and Amend reach a queued, staged or running item only.")

    def show_turns(self, item_id: str | None, turns: list, fresh: bool, outline: Event | None = None) -> None:
        self.item, self.turns = item_id, turns
        pin = self.render_outline(outline)
        if pin != self.pin.text():
            self.pin.setText(pin)
        self.pin.setVisible(bool(pin))
        html = self.render(turns)
        if html == self._html and not fresh:
            return
        sb = self.view.verticalScrollBar()
        bottom = fresh or sb.value() >= sb.maximum() - 4
        value = sb.value()
        self._html = html
        self.view.setHtml(html)
        sb.setValue(sb.maximum() if bottom else value)

    @staticmethod
    def render_outline(ev: Event | None) -> str:
        """The pinned outline: done rows ticked, the next undone one marked."""
        rows = outline_rows(ev)
        if not rows:
            return ""
        done = sum(1 for r in rows if r.get("done"))
        nxt = next((i for i, r in enumerate(rows) if not r.get("done")), None)
        out = [f"<b>Outline</b> <span style='color:{theme.MUTED}'>· {done}/{len(rows)} done · posted "
               f"{when(ev.ts)} · #{ev.id}</span>"]
        for i, r in enumerate(rows):
            eta = f" <span style='color:{theme.MUTED}'>· {_esc(r.get('eta') or '?')}</span>"
            if r.get("done"):
                out.append(f"<span style='color:{theme.GREEN}'>✓</span> "
                           f"<span style='color:{theme.MUTED}'>{_esc(r.get('title'))}</span>{eta}")
            elif i == nxt:
                out.append(f"<span style='color:{theme.AMBER}'>▶</span> <b>{_esc(r.get('title'))}</b>{eta}"
                           f" <span style='color:{theme.AMBER}'>· next</span>")
            else:
                out.append(f"<span style='color:{theme.MUTED}'>○</span> {_esc(r.get('title'))}{eta}")
        return "<br>".join(out)

    @classmethod
    def render(cls, turns: list) -> str:
        if not turns:
            return f"<p style='color:{theme.MUTED}'>Nothing said yet: the item has not started.</p>"
        rows = []
        for t in turns:
            bg, name = cls.LOOK.get(t.who, (theme.BASE, t.who))
            tone = cls.TONE.get(t.tone)
            colour = f" style='color:{tone}'" if tone and not t.note else ""
            head = (f"<b>{_esc(name)}</b> · <b{colour}>{_esc(t.title)}</b> "
                    f"<span style='color:{theme.MUTED}'>· {when(t.ts)} · #{t.id}</span>")
            body = f"<div>{_lines(t.text)}</div>" if t.text else ""
            for label, ts, text in t.under:
                stamp_ = f" {when(ts)}" if ts else ""
                if label == "reply":
                    body += (f"<table width='100%' cellpadding='6' style='margin-top:6px'><tr>"
                             f"<td bgcolor='{theme.BASE}'><b>executor replied</b>"
                             f"<span style='color:{theme.MUTED}'>{stamp_}</span><br>{_lines(text)}</td></tr></table>")
                else:
                    body += f"<div style='color:{theme.MUTED}'>{_esc(label)}{stamp_}: {_esc(text)}</div>"
            if t.note:
                body += f"<div style='color:{tone or theme.MUTED}'><i>{_esc(t.note)}</i></div>"
            if t.report:
                body += "<div><a href='report'>Report</a></div>"
            cell = f"<td bgcolor='{bg}'>{head}{body}</td>"
            # Matt's turns sit right, the executor's left, conductor's across the thread.
            spacer = "<td width='18%'></td>"
            row = (spacer + cell if t.who == "matt" else cell + spacer if t.who == "executor" else cell)
            rows.append(f"<table width='100%' cellpadding='8' cellspacing='0' style='margin-bottom:8px'>"
                        f"<tr>{row}</tr></table>")
        return "".join(rows)


def _esc(text: str) -> str:
    return html.escape(str(text), quote=False)


def _lines(text: str) -> str:
    return _esc(text).replace("\n", "<br>")


class TrailView(QWidget):
    """An item's whole trail: prompt, every event, report, executor log(s), git; in the
    Repos tab, a Conversation tab first (`ConversationView`).

    Built once per item. Moving to another event of the same item only re-renders the
    Events tab; new events for the open item refresh each tab in place (scroll kept,
    a tab whose text is unchanged untouched). Git runs on a worker thread.
    """
    TABS = ["Events", "Prompt", "Report", "Log", "Stage log", "Git"]
    INDENT = "      "                         # the focused event's detail lines

    def __init__(self, project: Project, conversation: bool = False):
        super().__init__()
        self.project = project
        self.names = (["Conversation"] if conversation else []) + self.TABS
        self.conversation = ConversationView() if conversation else None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 0, 0, 0)
        self.title = QLabel("Select an item to follow it." if conversation else "Select an event to see its item's trail.")
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                           | Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.title.linkActivated.connect(self.show_item)        # a resume item's original
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
        if self.conversation is not None:
            self.tabs.addTab(self.conversation, "Conversation")
            self.conversation.report.connect(lambda: self.tabs.setCurrentWidget(self.text["Report"]))
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
        if d := next((e.detail for e in reversed(tr.events) if e.detail.get("resume_of")), None):
            orig = d["resume_of"]
            bits.append(resume_label(d).replace(orig, f"<a href='{orig}'>{orig}</a>"))
        if reds:
            bits.append(f"<b style='color:{theme.RED}'>{len(reds)} red</b>")
        self.title.setText(" · ".join(bits))
        self.open_btn.setEnabled(self.session_id() is not None)
        if self.conversation is not None:
            self.conversation.show_turns(self.item, conversation(tr.events), fresh, current_outline(tr.events))
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
        if self.names[index] != "Git" or not self.item or self._trail is None:
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
