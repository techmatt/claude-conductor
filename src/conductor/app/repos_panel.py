"""The Repos tab: per repo, what is running, staged, queued and finished, with the selected
item's detail beside it."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QHeaderView, QInputDialog, QLabel, QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout)

from .. import audits
from ..events import Event
from ..util import clock, duration, now
from . import actions, theme
from .model import Project, Snapshot
from .trail import TrailView
from .widgets import (AMBER, KeepColourDelegate, Panel, compact_button, compact_buttons, confirm, muted, open_session,
                      pick_note, when)


class ReposPanel(Panel):
    COLS = ["Repo / item", "State", "Elapsed", "Note", ""]
    BUTTONS_WIDTH = 310     # the button column's least width; each rebuild widens it to fit

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
        h.resizeSection(4, self.BUTTONS_WIDTH)   # room for Mark reviewed, Pick… and Terminal
        self.tree.setItemDelegate(KeepColourDelegate(self.tree))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 8)
        hint = muted(QLabel("Select an item to follow it on the right; double-click opens it in Events. Clear, "
                            "Cancel, Mark reviewed and Pick… are signals the daemon carries out."))
        lay.addWidget(hint)
        # The tree on the left, the selected item's detail (Conversation first) on the right.
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.tree)
        self.detail = TrailView(project, conversation=True)
        self.detail.setMinimumWidth(320)
        self.detail.conversation.tell.connect(self._tell)
        self.detail.conversation.outline.connect(
            lambda item_id: self.signal_sent(f"outline request to {item_id}", actions.outline(self.control, item_id)))
        self.detail.conversation.amend.connect(self._amend)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([720, 520])
        lay.addWidget(split, 1)
        self.selected: str | None = None                # the item Matt picked in the tree
        self._actions: dict[str, tuple[bool, bool]] = {}    # item → (Tell allowed, Amend allowed)
        self._rebuilding = False
        self.tree.currentItemChanged.connect(self._current_changed)
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
                note = (f"finished {when(f.ts)}" + (f" · carry: {f.carry}" if f.carry not in (None, "none") else "")
                        + (f" · slow: {f.slow}" if f.slow else ""))
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
                note = (f"finished {when(f.ts)}" + (f" · carry: {f.carry}" if f.carry not in (None, "none") else "")
                        + (f" · slow: {f.slow}" if f.slow else ""))
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

    def update_snapshot(self, snap: Snapshot, new: list[Event] = ()) -> None:
        self._sessions = {f.item: f.session_id for rs in snap.repos for f in (*rs.finished, *rs.earlier)}
        self._picks = {f.item: f.pick for rs in snap.repos for f in (*rs.finished, *rs.earlier) if f.pick}
        plan = self._plan(snap)
        # Tell and Amend follow the tree's buttons: Tell only while executing, Amend
        # queued (staged included) or executing.
        self._actions = {}
        for _, _, _, rows in plan:
            for key, _, item_id, look, _ in rows:
                if item_id and key[0] in ("run", "queued"):
                    running = key[0] == "run" and look == "run"
                    self._actions[item_id] = (running, running or key[0] == "queued")
        sig = tuple((repo, tuple((k, look) for k, _, _, look, _ in rows)) for repo, _, _, rows in plan)
        if sig != self._sig:
            self._rebuild(plan)
            self._sig = sig
        self._follow(snap, new)
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
        self._rebuilding = True
        try:
            self._build(plan)
            # The selection survives: the selected item's row again (its key may have
            # changed, queued → run → done).
            if again := next((w for key, w in self._rows.items() if key[0] not in ("repo", "group", "red", "hold")
                              and w.data(0, Qt.ItemDataRole.UserRole) == self.selected), None):
                self.tree.setCurrentItem(again)
        finally:
            self._rebuilding = False

    def _build(self, plan) -> None:
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
                if texts[3]:
                    w.setToolTip(3, texts[3])       # a long note (carry, slow) reads whole here
                if look.endswith(":unreviewed") and pick_state != "saved":
                    # Needs Matt: its name amber until the report is reviewed or its pick saved.
                    w.setForeground(0, QBrush(AMBER))
                    w.setBackground(0, QBrush(QColor(theme.AMBER_BG)))
                    w.setFont(0, bold)
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
                    specs = [("Terminal", lambda _=False, i=key[1]: self._open_session(i), None)]
                    if pick_state and (ref := self._picks.get(key[1])):
                        # Amber until saved: the picker waits for Matt.
                        specs.insert(0, ("Pick…", lambda _=False, r=ref: self._pick(r),
                                         None if pick_state in ("saved", "settled") else "default"))
                    if look.endswith(":unreviewed"):
                        specs.insert(0, ("Mark reviewed", None, None if pick_state == "saved" else "default"))
                    holder = compact_buttons(*specs)
                    if b := holder.buttons.get("Mark reviewed"):
                        b.clicked.connect(lambda _=False, i=key[1], b=b: self._reviewed(i, b))
                    term = holder.buttons["Terminal"]
                    term.setToolTip("Open this item's session in a terminal (claude --resume)")
                    term.setEnabled(bool(self._sessions.get(key[1])))
                    self.tree.setItemWidget(w, 4, holder)
        # The button column fits its widest row, so no label is cut ("Open in terminal" was).
        widest = max((h.sizeHint().width() for w in self._rows.values()
                      if (h := self.tree.itemWidget(w, 4)) is not None), default=0)
        self.tree.header().resizeSection(4, max(self.BUTTONS_WIDTH, widest + 8))
        self.tree.blockSignals(True)            # not a click: the remembered state stays
        for key, w in self._rows.items():
            if key[0] in ("repo", "group"):
                w.setExpanded(self._open.get(key, key[0] == "repo" or self.GROUP_OPEN[key[2]]))
        self.tree.blockSignals(False)

    def _follow(self, snap: Snapshot, new: list[Event]) -> None:
        """Keep the detail on the selected item, or with nothing selected on the running
        item, else the most recently finished one; refresh it when it has new events."""
        shown = self.selected
        if shown is None:
            running = [a for rs in snap.repos for a in rs.running]
            done = sorted((f for rs in snap.repos for f in rs.finished), key=lambda f: f.ts, reverse=True)
            shown = (min(running, key=lambda a: a.started or "").item if running
                     else done[0].item if done else None)
        tell, amend = self._actions.get(shown, (False, False)) if shown else (False, False)
        self.detail.conversation.set_actions(tell, amend)
        if shown != self.detail.item:
            if shown:
                self.detail.show_item(shown)
        elif shown and any(e.item == shown for e in new):
            self.detail.refresh_item()

    def _current_changed(self, w: QTreeWidgetItem | None, _prev=None) -> None:
        if self._rebuilding:
            return
        item_id = w.data(0, Qt.ItemDataRole.UserRole) if w is not None else None
        if item_id and item_id != self.selected:
            self.selected = item_id
            tell, amend = self._actions.get(item_id, (False, False))
            self.detail.conversation.set_actions(tell, amend)
            self.detail.show_item(item_id)

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
