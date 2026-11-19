"""The app's watch on each daemon (`app/heartbeat.py`, docs/app.md *Daemon down*): one toast per
death or hang, nothing for a daemon stopped on purpose, a restart action on the button."""

import json
from datetime import timedelta

from conductor.app import heartbeat
from conductor.state import DaemonInfo
from conductor.util import now, stamp


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_a_death_toasts_once_after_the_grace_and_a_clean_stop_never():
    mono = Clock()
    hb = heartbeat.Heartbeats(mono=mono)
    dead = DaemonInfo(False, 4242, "2026-11-18T17:00:00-08:00", "2026-11-18T17:45:00-08:00", died=True)
    assert hb.check("fractals", dead) is None                 # a restart's handover looks like this at first
    mono.t = heartbeat.GRACE_SECONDS + 1
    down = hb.check("fractals", dead)
    assert down.title == "fractals: the daemon died" and "pid 4242, last heartbeat" in down.body
    assert "Restart daemon" in down.body
    assert hb.check("fractals", dead) is None                 # one per death, not per tick
    # Remembered: a new app (same settings) stays quiet about the same death.
    assert heartbeat.Heartbeats(hb.toasted, mono=mono).check("fractals", dead) is None
    # A daemon stopped on purpose (no daemon.json) and a healthy one raise nothing.
    assert hb.check("fractals", DaemonInfo(False)) is None
    assert hb.check("fractals", DaemonInfo(True, 1, "x", stamp())) is None
    # The next daemon dying is a new death.
    again = DaemonInfo(False, 5151, "2026-11-19T08:00:00-08:00", stamp(), died=True)
    hb.check("fractals", again)
    mono.t += heartbeat.GRACE_SECONDS + 1
    assert hb.check("fractals", again) is not None


def test_a_hung_daemon_toasts_once_its_heartbeat_is_minutes_old():
    hb = heartbeat.Heartbeats(mono=Clock())
    fresh = DaemonInfo(True, 7, "2026-11-19T08:00:00-08:00", stamp(now() - timedelta(seconds=100)), stale=True)
    assert not heartbeat.stuck(fresh) and hb.check("aic", fresh) is None
    old = DaemonInfo(True, 7, "2026-11-19T08:00:00-08:00",
                     stamp(now() - timedelta(seconds=heartbeat.STUCK_SECONDS + 5)), stale=True)
    down = hb.check("aic", old)
    assert down.title == "aic: the daemon is not answering" and "Restart daemon…" in down.body
    assert hb.check("aic", old) is None


def _settings(tmp_path):
    from PySide6.QtCore import QSettings
    return QSettings(str(tmp_path / "app.ini"), QSettings.Format.IniFormat)


def test_the_window_toasts_a_dead_daemon_once_and_offers_a_restart(qapp, env, tmp_path, monkeypatch, app_toasts):
    from conductor.app import actions
    from conductor.app.window import MainWindow
    lock = env.cfg.state_dir / "daemon.json"
    lock.write_text(json.dumps({"pid": 999999, "started": stamp(), "beat": stamp()}), encoding="utf-8")
    mono = Clock()
    win = MainWindow([env.control], settings=_settings(tmp_path), tray=False, interval_ms=60_000)
    win.heartbeats = heartbeat.Heartbeats(mono=mono)     # its first refresh saw the death on the real clock
    view = win.views[env.control.resolve()]
    win.refresh()
    assert app_toasts == [] and view.daemon_btn.text() == "Restart daemon"
    mono.t = heartbeat.GRACE_SECONDS + 1
    win.refresh()
    win.refresh()
    assert [t[0] for t in app_toasts] == ["test: the daemon died"]
    started = []
    monkeypatch.setattr(actions, "start_daemon", lambda control: started.append(control) or type("P", (), {"pid": 1})())
    view.daemon_btn.click()
    assert started == [env.control.resolve()] or started == [env.control]
    # A new app on the same settings doesn't toast it again.
    win2 = MainWindow([env.control], settings=_settings(tmp_path), tray=False, interval_ms=60_000)
    assert win2.heartbeats.toasted == win.heartbeats.toasted != []
    win2.heartbeats.seen.clear()
    win2.heartbeats.mono = lambda: 0.0 if not win2.heartbeats.seen else 3600.0
    win2.refresh()
    assert len(app_toasts) == 1
    # Stopped on purpose: the lock is gone, the button starts, nothing toasts.
    lock.unlink()
    win2.refresh()
    assert win2.views[env.control.resolve()].daemon_btn.text() == "Start daemon" and len(app_toasts) == 1
    for w in (win, win2):
        w._quitting = True
        w.close()


def test_restart_daemon_kills_only_the_hung_process_then_starts(tmp_path, monkeypatch):
    import subprocess
    import sys
    from conductor import procs
    from conductor.app import actions
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        state = tmp_path / ".conductor"
        state.mkdir()
        (state / "daemon.json").write_text(json.dumps(
            {"pid": child.pid, "create_time": procs.create_time(child.pid), "started": stamp()}), encoding="utf-8")
        started = []
        monkeypatch.setattr(actions, "start_daemon", lambda control: started.append(control))
        actions.restart_daemon(tmp_path)
        assert child.wait(10) is not None and started == [tmp_path]
    finally:
        if child.poll() is None:
            child.kill()
