"""Remote workers in the Conductor app: a status chip on a project's header and a Workers
tab, for a project with `[remote]` only (docs/remote-workers.md, *In the app*).

`window.ProjectView` makes one `RemoteStatus` when this module imports, and calls its
`refresh` each tick before Repos. Without `[remote]` it shows nothing: no chip, no tab,
no Note text. It reads `.conductor/remote/status.json`, which the broker's own 2-second
loop writes (`broker.write_status`), so the app polls nothing new. `summarize` is pure and
holds every rule for the dot and the words."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import psutil
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSizePolicy,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..app import theme
from ..app.widgets import muted
from ..util import clock, parse_stamp
from . import settings

STALE_SECONDS = 10.0            # status.json older than this: the broker isn't serving
RECENT_SECONDS = 300.0          # a gate failure, a broker restart or a re-run this recent is amber
RESTART_GAP = 60.0              # a broker starting this long after its daemon was restarted, not started
DOT = {"green": theme.GREEN, "amber": theme.ICON_ASKING, "grey": theme.ICON_IDLE, "red": theme.RED}
COLS = ["Worker", "Kind", "Cores", "State", "Last seen", "Shards today"]


@dataclass
class Chip:
    colour: str                 # green | amber | grey | red
    text: str                   # after the dot: `remote: …`
    problems: list[str] = field(default_factory=list)
    farming: dict[str, str] = field(default_factory=dict)   # item id -> Note suffix


def when(t: float) -> str:
    return clock(datetime.fromtimestamp(t).astimezone())


def ago(t: float | None, now: float) -> str:
    if not t:
        return "—"
    s = now - t
    return "now" if s < 5 else f"{int(s)}s ago" if s < 60 else f"{int(s // 60)} min ago" if s < 3600 else when(t)


def eta(seconds: float | None) -> str:
    if seconds is None:
        return ""
    return "ETA <1 min" if seconds < 60 else f"ETA {round(seconds / 60)} min"


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def summarize(status: dict | None, alive: bool, daemon_started: str | None, now: float) -> Chip:
    """The chip's dot and words, the tab's problem line and each farming item's suffix.
    Red: the broker isn't serving. Amber: a connected worker failed its gate, or in the last
    few minutes a gate failed, the broker restarted under a running daemon (not a handover
    for a code change: that is expected), or the main box
    re-ran shards a worker dropped. Grey: no worker connected. Green otherwise."""
    if not status or not alive or now - status.get("at", 0) > STALE_SECONDS:
        return Chip("red", "remote: broker down",
                    ["The broker isn't running: remote: true items run locally (.conductor/remote/broker.log)"])
    live = [w for w in status["workers"] if w["kind"] != "main" and w["fresh"]]
    problems = []
    failed = {w["id"] for w in live if w["gate"] == "fail"}
    seen = set()
    for g in reversed(status.get("gate_fails", [])):
        if (g["worker"] in failed or now - g["at"] < RECENT_SECONDS) and g["worker"] not in seen:
            seen.add(g["worker"])
            problems.append(f"{g.get('label') or g['worker']} failed the gate at {when(g['at'])} "
                            f"({g.get('reference', 'native')} reference)")
    started = status.get("started") or 0
    try:
        d0 = parse_stamp(daemon_started).timestamp() if daemon_started else None
    except (ValueError, TypeError):
        d0 = None
    if d0 and started - d0 > RESTART_GAP and now - started < RECENT_SECONDS and not status.get("handover"):
        problems.append(f"the broker restarted at {when(started)}")
    reruns = [t for t in status.get("reruns", []) if now - t < RECENT_SECONDS]
    if reruns:
        problems.append(f"{plural(len(reruns), 'shard')} re-run on the main box since {when(reruns[0])} "
                        "(a worker dropped or was slow)")
    job = status.get("job")
    farming = {}
    if job:
        text = (f"remote: farming {job['done']}/{job['shards']} shards · "
                + (plural(len(helping), "worker") if (helping := [w for w in live if w["gate"] != "fail"])
                   else "main box only")
                + (f" · {eta(job['eta'])}" if job.get("eta") is not None else ""))
        if job.get("item"):
            farming[job["item"]] = f"farming {job['done']}/{job['shards']}"
    elif live:
        text = f"remote: {plural(len(live), 'worker')} · {plural(sum(w['cores'] for w in live), 'core')} · idle"
    else:
        text = "remote: no workers"
    colour = "amber" if problems else "green" if live else "grey"
    return Chip(colour, text, problems, farming)


def read_status(state: Path) -> tuple[dict | None, bool]:
    """`status.json`, and whether the broker that wrote it is still running."""
    try:
        status = json.loads((state / "status.json").read_text(encoding="utf-8"))
        return status, psutil.pid_exists(int(status["pid"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None, False


class ChipLabel(QLabel):
    """The header chip; a click opens the Workers tab."""

    def __init__(self, on_click):
        super().__init__()
        self.on_click = on_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Remote workers: click for the Workers tab")

    def mousePressEvent(self, event) -> None:
        self.on_click()


class JobBar(QWidget):
    """The current job as one bar: done, out on a worker, pending."""

    def __init__(self):
        super().__init__()
        self.parts = (0, 0, 0)
        self.setFixedHeight(12)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set(self, done: int, out: int, pending: int) -> None:
        self.parts = (done, out, pending)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.BAND))
        total, x, w = sum(self.parts) or 1, 0.0, self.width()
        for n, colour in zip(self.parts[:2], (theme.ACCENT, "#7fa9d6")):
            span = w * n / total
            p.fillRect(int(x), 0, int(round(x + span)) - int(x), self.height(), QColor(colour))
            x += span
        p.end()


class WorkersPanel(QWidget):
    def __init__(self, copy):
        super().__init__()
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.broker = QLabel()
        top.addWidget(self.broker)
        self.copy_btn = QPushButton("Copy worker link")
        self.copy_btn.clicked.connect(copy)
        top.addWidget(self.copy_btn)
        top.addStretch(1)
        lay.addLayout(top)
        self.job = QLabel()
        self.bar = JobBar()
        lay.addWidget(self.job)
        lay.addWidget(self.bar)
        self.problem = QLabel()
        self.problem.setWordWrap(True)
        self.problem.setStyleSheet(f"color: {theme.AMBER}")
        lay.addWidget(self.problem)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table, 1)
        self.hint = muted(QLabel("Per-shard detail is on the worker page and in .conductor/remote/jobs/."))
        lay.addWidget(self.hint)

    def show_status(self, chip: Chip, status: dict | None, now: float) -> None:
        if chip.colour == "red" or not status:
            self.broker.setText(f"Broker <b style='color:{theme.RED}'>down</b>")
            self.copy_btn.setEnabled(False)
        else:
            self.broker.setText(f"Broker <b style='color:{theme.GREEN}'>up</b> since {when(status['started'])} "
                                f"· port {status['port']}")
            self.copy_btn.setEnabled(True)
        self.problem.setText(" · ".join(chip.problems))
        self.problem.setVisible(bool(chip.problems))
        job = status.get("job") if status else None
        if job:
            self.job.setText(f"Job: <b>{job.get('item') or job['id']}</b> · {job['done']} done · {job['out']} out · "
                             f"{job['pending']} pending of {job['shards']}" + (f" · {eta(job['eta'])}" if job.get("eta") is not None else ""))
            self.bar.set(job["done"], job["out"], job["pending"])
        else:
            self.job.setText("No job running.")
        self.bar.setVisible(bool(job))
        rows = status["workers"] if status and chip.colour != "red" else []
        self.table.setRowCount(len(rows))
        for r, w in enumerate(rows):
            main = w["kind"] == "main"
            state = f"working · {w['out']} out" if w["state"] == "working" else w["state"]
            cells = ["main box" if main else w["label"], w["engine"] if main else f"{w['kind']} · {w['engine']}",
                     plural(status.get("main_slots", 1), "slot") if main else str(w["cores"]), state,
                     ago(w["last_seen"], now), str(w["today"])]
            colour = {"working": theme.ACCENT, "gate failed": theme.RED, "stale": theme.MUTED}.get(w["state"])
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 3 and colour:
                    item.setForeground(QColor(colour))
                self.table.setItem(r, c, item)


class RemoteStatus:
    """The chip, Copy worker link and the Workers tab of one ProjectView."""

    def __init__(self, view):
        self.view = view
        self.control = view.project.control
        self._toml: tuple[float, settings.Remote | None] | None = None
        self.chip = ChipLabel(self.open_tab)
        self.chip.setStyleSheet(f"font-size: {theme.BASE_POINT_SIZE + 1}pt")
        self.chip.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)   # never clipped
        self.copy_btn = QPushButton("Copy worker link")
        self.copy_btn.setToolTip("The URL a browser on the LAN opens to join as a worker")
        self.copy_btn.clicked.connect(self.copy_link)
        at = view.head.indexOf(view.daemon_label) + 1
        view.head.insertWidget(at, self.chip)
        view.head.insertWidget(at + 1, self.copy_btn)
        self.panel = WorkersPanel(self.copy_link)
        self.url = ""
        self._show(False)

    def section(self) -> settings.Remote | None:
        """`[remote]`, re-read only when conductor.toml changes; a bad section counts as none."""
        path = self.control / settings.CONFIG_NAME
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        if not self._toml or self._toml[0] != mtime:
            try:
                r = settings.load(self.control)
            except (settings.RemoteError, OSError, ValueError):
                r = None
            self._toml = (mtime, r)
        return self._toml[1]

    def _show(self, on: bool) -> None:
        self.chip.setVisible(on)
        self.copy_btn.setVisible(on)
        tabs = self.view.tabs
        idx = tabs.indexOf(self.panel)
        if on and idx < 0:
            tabs.addTab(self.panel, "Workers")
        elif not on and idx >= 0:
            tabs.removeTab(idx)
        if not on:
            self.view.repos_panel.note_suffix = {}

    def refresh(self, snap) -> None:
        r = self.section()
        self._show(r is not None)
        if r is None:
            return
        now = time.time()
        status, alive = read_status(r.state)
        chip = summarize(status, alive, snap.daemon.started if snap.daemon.alive else None, now)
        self.url = status.get("url", "") if status and chip.colour != "red" else ""
        self.copy_btn.setEnabled(bool(self.url))
        self.chip.setText(f"<span style='color:{DOT[chip.colour]}'>●</span> {chip.text}")
        self.chip.setToolTip("\n".join(chip.problems) or "Remote workers: click for the Workers tab")
        self.view.repos_panel.note_suffix = chip.farming
        self.panel.show_status(chip, status, now)
        bar = self.view.tabs.tabBar()
        idx = self.view.tabs.indexOf(self.panel)
        bar.setTabTextColor(idx, QColor({"red": theme.RED, "amber": theme.AMBER}.get(chip.colour, theme.MUTED)))

    def open_tab(self) -> None:
        self.view.tabs.setCurrentWidget(self.panel)

    def copy_link(self) -> None:
        if self.url:
            QApplication.clipboard().setText(self.url)
            self.view.notify(f"Copied the worker link: {self.url}")
