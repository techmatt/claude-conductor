"""Prompt 149: a dead daemon is restarted by the app and toasted within seconds; Windows'
reason (a sign-out, a crash) is named; `conductor autostart` brings the app back at logon."""

import json
import subprocess
import sys
import time
from datetime import timedelta

import pytest

from conductor import autostart, procs, winupdate
from conductor.app import heartbeat
from conductor.state import DaemonInfo
from conductor.util import now, stamp

LOGOFF_XML = """<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System>
<Provider Name='Microsoft-Windows-Winlogon'/><EventID>7002</EventID>
<TimeCreated SystemTime='2026-11-24T02:15:18.6401147Z'/></System></Event>"""


def test_windows_reason_reads_a_sign_out_from_the_event_log_xml():
    text = winupdate.describe_ends(LOGOFF_XML)
    assert text.startswith("Windows signed you out (a shut-down, restart or sign-out ends every process) at ")
    assert text.endswith(("am", "pm"))
    assert winupdate.describe_ends("<Event><System><EventID>4624</EventID>"
                                   "<TimeCreated SystemTime='2026-11-24T02:15:18Z'/></System></Event>") is None
    assert winupdate.describe_ends("") is None


def test_a_death_after_a_real_life_is_revived_a_young_one_only_toasted():
    hb = heartbeat.Heartbeats(mono=lambda: 1000.0)
    old = DaemonInfo(False, 11, stamp(now() - timedelta(hours=3)), stamp(now() - timedelta(minutes=1)), died=True)
    hb.seen[heartbeat.key(old)] = 0.0
    down = hb.check("helium", old)
    assert down.revive and down.title == "helium: the daemon died"
    young = DaemonInfo(False, 12, stamp(now() - timedelta(seconds=40)), stamp(now() - timedelta(seconds=30)), died=True)
    hb.seen[heartbeat.key(young)] = 0.0
    down = hb.check("helium", young)
    assert not down.revive and "so not restarted" in down.body
    up = heartbeat.revived("helium", old, ["helium-hustle/075_story"], "Windows signed you out (…) at 6:15pm")
    assert up.title == "helium: the daemon died; restarted"
    assert "Windows signed you out (…) at 6:15pm. A new daemon is starting; it adopts helium-hustle/075_story" in up.body


@pytest.mark.slow
def test_a_killed_daemon_process_is_toasted_and_restarted_within_seconds(qapp, env, tmp_path, monkeypatch, app_toasts):
    """The prompt's bar: a killed daemon produces a toast within about a minute. A real
    process stands in for the daemon and is killed; the app's own timer finds it."""
    from PySide6.QtCore import QSettings
    from conductor.app import actions
    from conductor.app.window import MainWindow
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        lock = env.cfg.state_dir / "daemon.json"
        lock.write_text(json.dumps({"pid": child.pid, "create_time": procs.create_time(child.pid),
                                    "started": stamp(now() - timedelta(hours=1)), "beat": stamp()}), encoding="utf-8")
        started = []
        monkeypatch.setattr(actions, "start_daemon",
                            lambda control: started.append(control) or procs.Detached(4321, None))
        settings = QSettings(str(tmp_path / "app.ini"), QSettings.Format.IniFormat)
        win = MainWindow([env.control], settings=settings, tray=False, interval_ms=500)
        child.kill()
        child.wait(10)
        killed = time.monotonic()
        while not app_toasts and time.monotonic() - killed < 60:
            qapp.processEvents()
            time.sleep(0.05)
        took = time.monotonic() - killed
        assert app_toasts, "no toast within a minute of the kill"
        assert took < heartbeat.GRACE_SECONDS + 10
        title, body, _ = app_toasts[0]
        assert title == "test: the daemon died; restarted" and "A new daemon is starting" in body
        assert len(started) == 1
        win._quitting = True
        win.close()
    finally:
        if child.poll() is None:
            child.kill()


def test_autostart_writes_and_removes_a_startup_cmd(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    text = autostart.script(tmp_path / "repo")
    assert '-m conductor app' in text and "pythonw.exe" in text and text.endswith("\r\n")
    if sys.platform != "win32":
        with pytest.raises(OSError):
            autostart.set_on(True)
        return
    assert autostart.status() == "off"
    autostart.set_on(True)
    assert autostart.entry().is_file() and autostart.status().startswith("on: ")
    assert autostart.entry().read_bytes().count(b"\r\n") == 3
    autostart.set_on(False)
    assert autostart.status() == "off"
