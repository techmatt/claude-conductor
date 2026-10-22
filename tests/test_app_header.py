"""The project header (one daemon button, the short state, autopilot) and the message boxes."""

import pytest
from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtGui import QKeyEvent

from conductor.state import DaemonInfo
from conductor.util import clock, now


@pytest.mark.slow
def test_header_is_short_with_one_daemon_button_and_autopilot(qapp, env, tmp_path, monkeypatch):
    from conductor.app import model
    from conductor.app.window import MainWindow
    env.queue("001_long", env.hold())
    env.tick_until(lambda: env.events("start"))
    env.queue("002_next", "FAKE report done", depends_on="[001_long]")
    (env.control / "backlog" / "code" / "disk_space_audit.md").write_text("---\nrepo: code\n---\nFAKE report done\n",
                                                                         encoding="utf-8")
    env.tick_until(lambda: model.Project(env.control).snapshot().repos[0].staged)
    started = now().isoformat()
    monkeypatch.setattr(model, "daemon_info", lambda cfg: DaemonInfo(True, 9840, started, started))
    win = MainWindow([env.control], settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat),
                     tray=False, interval_ms=60_000)
    view = win.views[env.control.resolve()]
    win.refresh()
    assert view.state_label.text().endswith("1 running · 1 staged") and env.control.name not in view.state_label.text()
    assert f"up</b> since {clock(started)}" in view.daemon_label.text() and "9840" in view.daemon_label.toolTip()
    assert view.daemon_btn.text() == "Stop daemon…" and not hasattr(view, "start_btn")
    assert view.autopilot_btn.text() == "Autopilot: Always"
    assert view.autopilot_label.text() == "next: disk_space_audit when the queue empties"
    monkeypatch.setattr(model, "daemon_info", lambda cfg: DaemonInfo(False))
    win.refresh()
    assert view.daemon_btn.text() == "Start daemon" and "with the daemon <b" in view.state_label.text()
    view.autopilot_actions["off"].trigger()
    env.tick_until(lambda: env.events("autopilot"))
    win.refresh()
    assert view.autopilot_btn.text() == "Autopilot: Off" and view.autopilot_actions["off"].isChecked()
    assert view.autopilot_label.text() == "1 in the backlog, not drawn"
    env.release()
    env.tick_until(lambda: env.finished("code/002_next"))
    assert not env.events("backlog_draw")
    win.close()


def test_rejected_shows_until_dismissed_or_a_checkpoint(qapp, env, tmp_path):
    """Word Warriors: "2 rejected" stayed hours after the 7:59am picks; nothing cleared it."""
    from conductor.app.window import MainWindow
    win = MainWindow([env.control], settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat),
                     tray=False, interval_ms=60_000)
    view = win.views[env.control.resolve()]
    for n in (1, 2):
        env.signal(f"app-{n}.signal", "by: app\nbogus: x\n")
    env.tick_until(lambda: len(env.events("invalid")) == 2)
    win.refresh()
    assert "2 rejected" in view.pending_label.text() and not view.dismiss_btn.isHidden()
    view.dismiss_btn.click()
    env.tick_until(lambda: env.events("ack"))
    win.refresh()
    assert "rejected" not in view.pending_label.text() and view.dismiss_btn.isHidden()
    env.signal("app-3.signal", "by: app\nbogus: x\n")
    env.tick_until(lambda: len(env.events("invalid")) == 3)
    win.refresh()
    assert "1 rejected" in view.pending_label.text()
    env.signal("cp.signal", "checkpoint: next\n")
    env.tick_until(lambda: env.events("checkpoint"))
    win.refresh()
    assert "rejected" not in view.pending_label.text()
    win.close()


def test_a_send_box_wraps_and_enter_sends(qapp):
    from conductor.app.widgets import SEND_HINT, MessageDialog
    from PySide6.QtWidgets import QLabel
    d = MessageDialog(None, "Message", "Say it")
    assert d.box.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert d.box.lineWrapMode() == d.box.LineWrapMode.WidgetWidth and d.box.minimumHeight() > 80
    d.box.insertPlainText("one")
    d.box.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier, "\r"))
    d.box.insertPlainText("two")
    assert d.result() == 0
    d.box.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, "\r"))
    assert d.result() == 1 and d.box.toPlainText() == "one\ntwo"           # Enter is OK
    assert SEND_HINT in [w.text() for w in d.findChildren(QLabel)]
