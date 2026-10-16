"""The main window (one tab per control folder) and the tray icon."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox,
    QPushButton, QSystemTrayIcon, QTabWidget, QToolBar, QVBoxLayout, QWidget,
)

from .. import config as config_mod
from ..util import clock
from . import actions, theme
from .model import PRIORITY, Project, Snapshot, overall
from .panels import EventsPanel, ProposedPanel, QuestionsPanel, ReposPanel, confirm

# Icon and status-dot colours (graphics, not text: they need 3:1, which each meets).
COLORS = {"red": theme.RED, "asking": theme.ICON_ASKING, "running": theme.ACCENT, "idle": theme.ICON_IDLE}
SETTINGS_KEY = "controls"


def state_icon(state: str, size: int = 64) -> QIcon:
    """A filled disc in the state's colour, drawn rather than shipped."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor("white"), size / 16))
    p.setBrush(QColor(COLORS.get(state, COLORS["idle"])))
    m = size / 10
    p.drawEllipse(int(m), int(m), int(size - 2 * m), int(size - 2 * m))
    p.end()
    return QIcon(pm)


def dot(state: str) -> str:
    colour = COLORS.get(state, COLORS["idle"])
    return f"<span style='color:{colour}; font-size:18px'>●</span>"


class ProjectView(QWidget):
    """One control folder: a header (state, daemon, start/stop) and the four panels."""

    def __init__(self, project: Project, notify):
        super().__init__()
        self.project = project
        self.notify = notify
        self.snap: Snapshot | None = None
        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        self.state_label = QLabel()
        self.daemon_label = QLabel()
        self.state_label.setStyleSheet(f"font-size: {theme.BASE_POINT_SIZE + 1}pt")
        self.daemon_label.setStyleSheet(f"font-size: {theme.BASE_POINT_SIZE + 1}pt")
        self.pending_label = QLabel()
        self.pending_label.setStyleSheet(f"color: {theme.AMBER}; font-weight: bold")
        self.start_btn = QPushButton("Start daemon")
        self.start_btn.clicked.connect(self.start_daemon)
        self.stop_btn = QPushButton("Stop daemon…")
        self.stop_btn.clicked.connect(self.stop_daemon)
        for w in (self.state_label, self.daemon_label, self.pending_label):
            head.addWidget(w)
        head.addStretch(1)
        head.addWidget(self.start_btn)
        head.addWidget(self.stop_btn)
        lay.addLayout(head)
        self.tabs = QTabWidget()
        self.repos_panel = ReposPanel(project, notify)
        self.questions_panel = QuestionsPanel(project, notify)
        self.events_panel = EventsPanel(project, notify)
        self.proposed_panel = ProposedPanel(project, notify)
        self.panels = {"repos": self.repos_panel, "questions": self.questions_panel,
                       "events": self.events_panel, "proposed": self.proposed_panel}
        for name, panel in self.panels.items():
            self.tabs.addTab(panel, name.capitalize())
            panel.open_item.connect(self.open_item)
        lay.addWidget(self.tabs, 1)

    def open_item(self, item_id: str) -> None:
        self.tabs.setCurrentWidget(self.events_panel)
        self.events_panel.show_item(item_id)

    def refresh(self) -> Snapshot:
        # New events go to the table first: polling has already moved past them, so a
        # failure anywhere later must not lose them. Each panel fails on its own.
        new = self.project.poll_events()
        errors: list[str] = []

        def guarded(name: str, fn, *args) -> None:
            try:
                fn(*args)
            except Exception as exc:          # noqa: BLE001 - reported, the rest still refresh
                errors.append(f"{name}: {exc!r}")

        guarded("events", self.events_panel.add_events, new)
        snap = self.project.snapshot()
        self.snap = snap
        guarded("repos", self.repos_panel.update_snapshot, snap)
        guarded("questions", self.questions_panel.update_snapshot, snap)
        guarded("proposed", self.proposed_panel.update_snapshot, snap)
        if errors:
            self.notify(f"{self.project.name}: refresh failed in " + "; ".join(errors))
        # The tab labels are the one signal visible from every panel: an open question or
        # an item waiting for approval colours its tab (and says so in words too).
        qs = snap.open_questions
        blocking = sum(1 for q in qs if q.block)
        bar = self.tabs.tabBar()
        unreviewed = sum(rs.unreviewed for rs in snap.repos)
        self.tabs.setTabText(0, f"Repos ({unreviewed} unreviewed)" if unreviewed else "Repos")
        bar.setTabTextColor(0, QColor(theme.AMBER if unreviewed else theme.MUTED))
        if qs:
            self.tabs.setTabText(1, f"Questions ({len(qs)}" + (f", {blocking} blocking)" if blocking else ")"))
            bar.setTabTextColor(1, QColor(theme.RED if blocking else theme.AMBER))
        else:
            self.tabs.setTabText(1, "Questions")
            bar.setTabTextColor(1, QColor(theme.MUTED))
        self.tabs.setTabText(3, f"Proposed ({len(snap.proposed)} to approve)" if snap.proposed else "Proposed")
        bar.setTabTextColor(3, QColor(theme.AMBER if snap.proposed else theme.MUTED))
        held = [rs for rs in snap.repos if rs.red]
        if held:
            what = " · ".join(f"{rs.name} <b style='color:{theme.RED}'>HELD</b> (red {(rs.red.item or '?').rsplit('/', 1)[-1]})"
                              for rs in held)
        elif snap.why.startswith(snap.state):
            what = snap.why
        else:
            what = f"{snap.state}: {snap.why}"
        self.state_label.setText(f"{dot(snap.state)} <b>{snap.project}</b> · {what}")
        d = snap.daemon
        since = f", since {clock(d.started)}" if d.started else ""
        self.daemon_label.setText(f"· daemon <b style='color:{theme.GREEN}'>running</b> (pid {d.pid}{since})"
                                  if d.alive else f"· daemon <b style='color:{theme.RED}'>not running</b>")
        self.start_btn.setEnabled(not d.alive)
        self.stop_btn.setEnabled(d.alive)
        sdir = self.project.control / "signals"
        pending = sorted(sdir.glob("app-*.signal")) if sdir.is_dir() else []
        bad = sorted(sdir.glob("app-*.signal.bad")) if sdir.is_dir() else []
        bits = []
        if pending:
            bits.append(f"{len(pending)} action(s) waiting for the daemon")
        if bad:
            bits.append(f"{len(bad)} rejected (see Events: invalid)")
        if snap.error:
            bits.append(snap.error)
        self.pending_label.setText(" · ".join(bits))
        return snap

    def start_daemon(self) -> None:
        p = actions.start_daemon(self.project.control)
        self.notify(f"started the daemon for {self.project.name} (pid {p.pid}); "
                    "output in .conductor/daemon.out.log")
        self.start_btn.setEnabled(False)

    def stop_daemon(self) -> None:
        if confirm(self, "Stop daemon",
                   f"Stop the conductor daemon for {self.project.name}?\n\n"
                   "Running executors and stages keep running; the next start adopts them. "
                   "Nothing new starts until the daemon runs again."):
            path = actions.stop_daemon(self.project.control)
            self.notify(f"stop requested: wrote signals/{path.name}")


class MainWindow(QMainWindow):
    def __init__(self, folders: list[Path] = (), settings: QSettings | None = None,
                 tray: bool = True, interval_ms: int = 1500):
        super().__init__()
        self.setWindowTitle("Conductor")
        self.resize(1250, 780)
        self.setWindowIcon(state_icon("idle"))
        self.settings = settings or QSettings("conductor", "conductor-app")
        self.views: dict[Path, ProjectView] = {}
        self._quitting = False
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.empty = QLabel("No project yet. Add a control folder with “Add project…”.")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(4, 4, 4, 4)
        cl.addWidget(self.tabs)
        cl.addWidget(self.empty)
        self.setCentralWidget(central)
        bar = QToolBar("Projects")
        bar.setMovable(False)
        self.addToolBar(bar)
        add = QAction("Add project…", self)
        add.triggered.connect(self.choose_project)
        bar.addAction(add)
        rem = QAction("Remove project", self)
        rem.triggered.connect(self.remove_current)
        bar.addAction(rem)
        self.tray = self._make_tray() if tray and QSystemTrayIcon.isSystemTrayAvailable() else None
        # A remembered folder that fails to load now (drive not mounted, conductor.toml
        # mid-edit) stays remembered; only Remove project forgets one.
        self.unloaded: list[Path] = []
        for f in self.remembered():
            if self.add_project(Path(f), remember=False, quiet=True) is None:
                self.unloaded.append(Path(f).resolve())
        for f in folders:
            self.add_project(Path(f), remember=True, quiet=True)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(interval_ms)
        self.refresh()

    # ------------------------------------------------------------ settings
    def remembered(self) -> list[str]:
        value = self.settings.value(SETTINGS_KEY, [])
        if isinstance(value, str):          # one entry comes back as a bare string
            value = [value]
        return [str(v) for v in value or []]

    def _remember(self) -> None:
        keep = [*self.views, *(p for p in self.unloaded if p not in self.views)]
        self.settings.setValue(SETTINGS_KEY, [str(p) for p in keep])
        self.settings.sync()

    # ------------------------------------------------------------ projects
    def add_project(self, folder: Path, remember: bool = True, quiet: bool = False) -> ProjectView | None:
        folder = Path(folder).resolve()
        if folder in self.views:
            self.tabs.setCurrentWidget(self.views[folder])
            return self.views[folder]
        try:
            project = Project(folder)
        except config_mod.ConfigError as exc:
            msg = f"{folder}: {exc}"
            if quiet:
                self.statusBar().showMessage(msg, 15000)
            else:
                QMessageBox.warning(self, "Add project", msg)
            return None
        view = ProjectView(project, self.notify)
        self.views[folder] = view
        self.tabs.addTab(view, project.name)
        self.tabs.setTabToolTip(self.tabs.count() - 1, str(folder))
        if remember:
            self._remember()
        self._show_empty()
        return view

    def choose_project(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Control folder (holds conductor.toml)")
        if folder and self.add_project(Path(folder)):
            self.refresh()

    def remove_current(self) -> None:
        view = self.tabs.currentWidget()
        if not isinstance(view, ProjectView):
            return
        if not confirm(self, "Remove project",
                       f"Stop showing {view.project.name}?\n\nThis changes nothing in the project; "
                       "its daemon and executors keep running."):
            return
        folder = next(k for k, v in self.views.items() if v is view)
        self.tabs.removeTab(self.tabs.indexOf(view))
        del self.views[folder]
        self.unloaded = [p for p in self.unloaded if p != folder]
        view.project.close()
        view.deleteLater()
        self._remember()
        self._show_empty()
        self.refresh()

    def _show_empty(self) -> None:
        self.empty.setVisible(not self.views)
        self.tabs.setVisible(bool(self.views))

    def notify(self, text: str) -> None:
        self.statusBar().showMessage(text, 15000)

    # ------------------------------------------------------------ refresh
    def refresh(self) -> None:
        states, lines = [], []
        for folder, view in list(self.views.items()):
            try:
                snap = view.refresh()
            except Exception as exc:          # a refresh must never stop the timer
                self.statusBar().showMessage(f"{folder}: refresh failed: {exc!r}", 10000)
                continue
            states.append(snap.state)
            lines.append(f"{snap.project}: {snap.state} — {snap.why}")
            idx = self.tabs.indexOf(view)
            self.tabs.setTabIcon(idx, state_icon(snap.state, 16))
        self.state = overall(states)
        self.setWindowIcon(state_icon(self.state))
        if self.tray:
            self.tray.setIcon(state_icon(self.state))
            self.tray.setToolTip(("Conductor — " + self.state + "\n" + "\n".join(lines))[:250])

    # ------------------------------------------------------------ tray
    def has_tray(self) -> bool:
        return self.tray is not None

    def _make_tray(self) -> QSystemTrayIcon:
        tray = QSystemTrayIcon(state_icon("idle"), self)
        menu = QMenu()
        show = menu.addAction("Open Conductor")
        show.triggered.connect(self.show_window)
        menu.addSeparator()
        quit_ = menu.addAction("Quit (daemons keep running)")
        quit_.triggered.connect(self.quit)
        tray.setContextMenu(menu)
        tray.activated.connect(self._tray_clicked)
        tray.show()
        self._tray_menu = menu
        return tray

    def _tray_clicked(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit(self) -> None:
        self._quitting = True
        for view in self.views.values():
            view.project.close()
        if self.tray:
            self.tray.hide()
        QApplication.quit()

    def closeEvent(self, event) -> None:
        # Closing the window only hides it while the tray icon can bring it back; it
        # never touches a daemon or an executor either way.
        if self.tray and not self._quitting:
            event.ignore()
            self.hide()
            return
        for view in self.views.values():
            view.project.close()
        super().closeEvent(event)


__all__ = ["MainWindow", "ProjectView", "state_icon", "PRIORITY"]
