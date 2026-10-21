"""The main window (one tab per control folder) and the tray icon."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMainWindow, QMenu,
                               QMessageBox, QPushButton, QSizePolicy, QSystemTrayIcon, QTabWidget, QToolBar,
                               QVBoxLayout, QWidget)

from .. import config as config_mod
from ..inbox import checkpoint_name
from ..util import clock
from . import actions, icon, status, theme
from .events_panel import EventsPanel
from .model import PRIORITY, Project, Snapshot, overall
from .proposed_panel import ProposedPanel
from .questions_panel import QuestionsPanel
from .repos_panel import ReposPanel
from .widgets import confirm, muted

# Status-dot colours (graphics, not text: they need 3:1, which each meets).
COLORS = {"red": theme.RED, "asking": theme.ICON_ASKING, "running": theme.ACCENT, "idle": theme.ICON_IDLE}
SETTINGS_KEY = "controls"
AUTOPILOT = {                   # mode → menu label, what it means
    "off": ("Off", "Nothing starts by itself: no backlog draws, no watch tells or draws (the watch still "
                   "runs and recommends)"),
    "night": ("Night only", "Backlog draws and watch actions only at night ([defaults] night hours)"),
    "always": ("Always", "Backlog draws and watch actions day and night"),
}
HUBS = {"need": "red", "running": "gold", "idle": "slate"}     # a project tab's icon, by `status` kind
TURN_MS = 1000                  # the running title's glyph and the tray's pulse


def tray_hub(st: status.Status, tick: int) -> str:
    """The tray's hub: red when something needs Matt, pulsing gold while items run."""
    return {"need": "red", "running": "dim" if tick % 2 else "gold"}.get(st.kind, "gold")


def state_icon(kind: str) -> QIcon:
    """The app's icon with its hub coloured by a `status` kind: red when something needs
    Matt, gold while items run, slate when idle."""
    return icon.icon(HUBS.get(kind, "slate"))


def dot(state: str) -> str:
    colour = COLORS.get(state, COLORS["idle"])
    return f"<span style='color:{colour}; font-size:18px'>●</span>"


class ProjectView(QWidget):
    """One control folder: a header (state, daemon, start/stop) and the four panels."""

    def __init__(self, project: Project, notify, settings: QSettings | None = None):
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
        # One daemon button: Start daemon while it is down, Stop daemon… while it runs.
        self.daemon_btn = QPushButton("Start daemon")
        self.daemon_btn.clicked.connect(self.toggle_daemon)
        self.checkpoint_btn = QPushButton("New checkpoint…")
        self.checkpoint_btn.setToolTip("A session boundary: finished, reviewed items from before it drop out of "
                                       "Repos, Events and the inbox (a `checkpoint:` signal)")
        self.checkpoint_btn.clicked.connect(self.new_checkpoint)
        # Autopilot: whether the night shift may start work on its own (an `autopilot:` signal),
        # and beside it what it will start next.
        self.autopilot_btn = QPushButton()
        menu = QMenu(self.autopilot_btn)
        group = QActionGroup(menu)
        self.autopilot_actions: dict[str, QAction] = {}
        for mode, (label, tip) in AUTOPILOT.items():
            a = menu.addAction(label)
            a.setCheckable(True)
            a.setToolTip(tip)
            group.addAction(a)
            a.triggered.connect(lambda _=False, m=mode: self.set_autopilot(m))
            self.autopilot_actions[mode] = a
        menu.setToolTipsVisible(True)
        self.autopilot_btn.setMenu(menu)
        self.autopilot_label = muted(QLabel())
        for w in (self.pending_label, self.autopilot_label):       # clipped when narrow; the tooltip has it all
            w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        for w in (self.state_label, self.daemon_label):
            head.addWidget(w)
        head.addWidget(self.pending_label, 1)
        # One Run button per `[run.<name>]` target, rebuilt when the targets change, each
        # with a Stop button beside it, shown while the target is up or starting.
        self.run_bar = QHBoxLayout()
        head.addLayout(self.run_bar)
        self.run_buttons: dict[str, QPushButton] = {}
        self.stop_buttons: dict[str, QPushButton] = {}
        head.addWidget(self.autopilot_btn)
        head.addWidget(self.autopilot_label, 1)
        head.addWidget(self.checkpoint_btn)
        head.addWidget(self.daemon_btn)
        lay.addLayout(head)
        # The header's second line: the box's memory (`memory.line`), only when it needs
        # Matt: amber under pressure, red when critical. Hidden (and its space collapsed) at ok.
        self.memory_label = QLabel()
        self.memory_label.hide()
        lay.addWidget(self.memory_label)
        self.tabs = QTabWidget()
        self.repos_panel = ReposPanel(project, notify, settings)
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
        guarded("repos", self.repos_panel.update_snapshot, snap, new)
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
        counts = [f"{unreviewed} unreviewed"] * bool(unreviewed) + [f"{snap.to_pick} to pick"] * bool(snap.to_pick)
        self.tabs.setTabText(0, f"Repos ({', '.join(counts)})" if counts else "Repos")
        bar.setTabTextColor(0, QColor(theme.AMBER if counts else theme.MUTED))
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
        else:
            what = snap.why.replace("daemon down", f"daemon <b style='color:{theme.RED}'>down</b>")
        self.state_label.setText(f"{dot(snap.state)} {what}")
        self.state_label.setToolTip(f"{snap.project}: {snap.state}")
        d = snap.daemon
        beat = clock(d.beat) if d.beat else "never"
        if d.alive and d.stale:
            self.daemon_label.setText(f"· daemon <b style='color:{theme.AMBER}'>not answering</b> since {beat}")
        elif d.alive:
            self.daemon_label.setText(f"· daemon <b style='color:{theme.GREEN}'>up</b>"
                                      + (f" since {clock(d.started)}" if d.started else ""))
        elif d.died:
            self.daemon_label.setText(f"· daemon <b style='color:{theme.RED}'>died</b> (last heartbeat {beat})")
        else:
            self.daemon_label.setText(f"· daemon <b style='color:{theme.RED}'>down</b>")
        self.daemon_label.setVisible(not snap.why.endswith("with the daemon down"))   # said once is enough
        self.daemon_label.setToolTip(f"pid {d.pid}, last heartbeat {beat}" + (
            "; it never stopped cleanly" if d.died else "") if d.pid else "no daemon has run here yet")
        text, level = snap.memory
        colour = {"pressure": theme.AMBER, "critical": theme.RED}.get(level)
        self.memory_label.setText(f"<b style='color:{colour}'>{text}</b>" if colour else "")
        self.memory_label.setVisible(bool(colour))
        self.daemon_btn.setText("Stop daemon…" if d.alive else "Start daemon")
        self.daemon_btn.setToolTip("Stop the daemon; running executors keep running" if d.alive else
                                   "Start the daemon (`conductor run`), output in .conductor/daemon.out.log")
        self.daemon_btn.setEnabled(True)
        mode, plan = snap.autopilot
        self.autopilot_btn.setText(f"Autopilot: {AUTOPILOT.get(mode, (mode,))[0]}")
        self.autopilot_btn.setToolTip(AUTOPILOT.get(mode, ("", "unknown mode"))[1])
        for m, a in self.autopilot_actions.items():
            a.setChecked(m == mode)
        self.autopilot_label.setText(plan)
        self.autopilot_label.setToolTip(plan)
        self._update_run_buttons(snap)
        cp = snap.checkpoint
        self.checkpoint_btn.setToolTip(
            (f"Current: {checkpoint_name(cp)}, {clock(cp.ts)}. " if cp else "No checkpoint yet. ")
            + "A new one is a session boundary: finished, reviewed items from before it drop out of "
              "Repos, Events and the inbox (a `checkpoint:` signal)")
        sdir =self.project.control / "signals"
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
        self.pending_label.setToolTip(" · ".join(bits))
        return snap

    def _update_run_buttons(self, snap: Snapshot) -> None:
        if list(snap.targets) != list(self.run_buttons):
            for b in [*self.run_buttons.values(), *self.stop_buttons.values()]:
                self.run_bar.removeWidget(b)
                b.deleteLater()
            self.run_buttons, self.stop_buttons = {}, {}
            for name in snap.targets:
                b = QPushButton()
                b.clicked.connect(lambda _=False, n=name: self.run_target(n))
                self.run_bar.addWidget(b)
                self.run_buttons[name] = b
                s = QPushButton(f"Stop {name}")
                s.setToolTip(f"Stop run target {name}: kills its process tree (a `run: {name} stop` signal; "
                             "the daemon must be running)")
                s.clicked.connect(lambda _=False, n=name: self.stop_target(n))
                self.run_bar.addWidget(s)
                self.stop_buttons[name] = s
        colours = {"up": theme.GREEN, "starting": theme.AMBER, "down": theme.MUTED}
        for name, st in snap.targets.items():
            b = self.run_buttons[name]
            t = self.project.cfg.targets.get(name)
            text = f"Run {name} ● {st}"
            if b.text() != text:
                b.setText(text)
                b.setStyleSheet(f"QPushButton {{ color: {colours.get(st, theme.MUTED)}; font-weight: bold; }}")
            b.setToolTip(f"{'Open' if st == 'up' else 'Start, then open'} {t.url if t else ''} "
                         "(a `run:` signal; the daemon must be running)")
            self.stop_buttons[name].setVisible(st in ("up", "starting"))

    def run_target(self, name: str) -> None:
        path = actions.run_target(self.project.control, name)
        self.notify(f"run {name}: wrote signals/{path.name}; the daemon starts it if needed and opens it")

    def stop_target(self, name: str) -> None:
        path = actions.run_target(self.project.control, name, stop=True)
        self.notify(f"stop {name}: wrote signals/{path.name}; the daemon stops it")

    def new_checkpoint(self, label: str | None = None) -> None:
        if label is None:
            label, ok = QInputDialog.getText(
                self, "New checkpoint",
                "Label for the new checkpoint (a session boundary).\n\nFinished items reviewed before it drop "
                "out of the current view; unreviewed reports, holds, questions and queued work carry forward.",
                text="session start")
            if not ok:
                return
        path = actions.checkpoint(self.project.control, label)
        self.notify(f"new checkpoint: wrote signals/{path.name}; the daemon logs it")

    def set_autopilot(self, mode: str) -> None:
        path = actions.autopilot(self.project.control, mode)
        self.notify(f"autopilot {mode}: wrote signals/{path.name}; the daemon applies it and logs `autopilot`")

    def toggle_daemon(self) -> None:
        (self.stop_daemon if self.snap and self.snap.daemon.alive else self.start_daemon)()

    def start_daemon(self) -> None:
        p = actions.start_daemon(self.project.control)
        self.notify(f"started the daemon for {self.project.name} (pid {p.pid}); "
                    "output in .conductor/daemon.out.log")
        self.daemon_btn.setEnabled(False)

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
        self.setWindowTitle(status.APP)
        self.resize(1250, 780)
        self.setWindowIcon(icon.app_icon())
        self.status = status.Status("idle", [])
        self._good: dict[Path, Snapshot] = {}      # each project's last snapshot read whole: the title's
        self._tick = 0
        # Only while items run: turns the title's glyph and pulses the tray's hub.
        self.turner = QTimer(self)
        self.turner.timeout.connect(self._turn)
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
        view = ProjectView(project, self.notify, self.settings)
        self.views[folder] = view
        self.tabs.addTab(view, project.name)
        self.tabs.setTabToolTip(self.tabs.count() - 1, str(folder))
        if remember:
            self._remember()
        self._show_empty()
        return view

    def open_forwarded(self, folders: list[Path]) -> None:
        """A second `conductor app` launch: add its folders, then come forward on the
        last one named."""
        for f in folders:
            if view := self.add_project(Path(f), remember=True, quiet=True):
                self.tabs.setCurrentWidget(view)
        self.refresh()
        self.show_window()

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
            if not snap.error or folder not in self._good:
                self._good[folder] = snap     # a read that failed part-way keeps the title where it was
            idx = self.tabs.indexOf(view)
            self.tabs.setTabIcon(idx, state_icon(status.summarize([self._good[folder]]).kind))
        self._good = {f: s for f, s in self._good.items() if f in self.views}
        self.state = overall(states)
        self.status = status.summarize(list(self._good.values()))
        if self.status.kind == "running":
            if not self.turner.isActive():
                self.turner.start(TURN_MS)
        else:
            self.turner.stop()
        self._show_status()
        if self.tray:
            head = status.APP + "".join(f" · {p}" for p in self.status.parts)
            self.tray.setToolTip((head + "\n" + "\n".join(lines))[:250])

    def _turn(self) -> None:
        self._tick += 1
        self._show_status()

    def _show_status(self) -> None:
        """The title and the tray's icon: the gold hub pulses while items run and turns
        red when something needs Matt. Needs Matt holds still."""
        self.setWindowTitle(self.status.title(self._tick))
        if self.tray:
            self.tray.setIcon(icon.icon(tray_hub(self.status, self._tick)))

    # ------------------------------------------------------------ tray
    def has_tray(self) -> bool:
        return self.tray is not None

    def _make_tray(self) -> QSystemTrayIcon:
        tray = QSystemTrayIcon(icon.icon(), self)
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
