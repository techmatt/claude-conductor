"""The app's remote pieces: `Board.panel`, the chip's rules (`app.summarize`), and the chip,
Copy worker link and Workers tab in a real window, shown only for a project with `[remote]`."""

import json
import os
import sys
import time

import pytest

from conductor.remote.board import Board

from .conftest import remote_section

B = 1_700_000_000.0               # a real epoch: Windows can't localise a time near 0


class Clock:
    def __init__(self):
        self.t = B + 1000

    def __call__(self):
        return self.t


def test_panel_rows_states_today_and_reruns(tmp_path):
    clock = Clock()
    b = Board(tmp_path, lease_seconds=30, fresh_seconds=5, native_argv=["sim"], clock=clock)
    jid = b.add_job("k1", "fp", [], [(i * 10, 10) for i in range(4)], ".", item="code/001_sim")
    for wid in ("good", "bad", "gone"):
        b.hello(wid, "local-remote", cores=3, label=f"{wid} box")
        assert b.poll(wid)["task"] == "gate"
    assert b.gate("good", "k1", "fp") and b.gate("gone", "k1", "fp") and not b.gate("bad", "k1", "nope")
    s0 = b.poll("good")["shard"]
    assert b.result("good", jid, s0, True, b"x", ms=100)
    b.poll("good")                                       # one out on `good`
    b.poll("gone")                                       # one out on `gone`, which then goes quiet
    clock.t += 6
    b.hello("good", "local-remote", cores=3, label="good box")
    b.hello("bad", "local-remote", cores=3, label="bad box")
    assert b.claim(jid)["rerun"] and b.reruns == [clock.t]   # `gone`'s lease is dead: its shard runs here
    assert not b.claim(jid)["rerun"]                     # the last unleased shard; good's is still out
    p = b.panel()
    rows = {w["id"]: w for w in p["workers"]}
    assert p["workers"][0]["id"] == "main" and rows["main"]["state"] == "working" and rows["main"]["out"] == 2
    assert rows["good"]["state"] == "working" and rows["good"]["out"] == 1 and rows["good"]["today"] == 1
    assert rows["bad"]["state"] == "gate failed" and rows["gone"]["state"] == "stale"
    assert p["job"] | {"eta": None} == {"id": jid, "item": "code/001_sim", "shards": 4, "done": 1, "out": 3,
                                        "pending": 0, "eta": None, "started": B + 1000}
    assert p["gate_fails"][0]["label"] == "bad box" and p["gate_fails"][0]["at"] == B + 1000


def status(**over) -> dict:
    s = {"at": B + 5000, "pid": 1, "port": 8770, "url": "http://10.0.0.2:8770/w/t/", "started": B + 1000,
         "main_slots": 6, "job": None, "gate_fails": [], "reruns": [],
         "workers": [{"id": "main", "kind": "main", "engine": "native", "label": "main box", "cores": 1,
                      "state": "idle", "out": 0, "gate": "", "fresh": True, "last_seen": None, "today": 0}]}
    return s | over


def worker(wid="w1", cores=7, gate="pass", fresh=True, kind="browser") -> dict:
    return {"id": wid, "kind": kind, "engine": "wasm", "label": f"Edge on {wid}", "cores": cores, "state": "idle",
            "out": 0, "gate": gate, "fresh": fresh, "last_seen": B + 4999, "today": 3}


def test_the_chip_in_each_state():
    from conductor.remote.app import summarize
    now = B + 5001
    assert summarize(None, False, None, now).colour == "red"
    assert summarize(status(), False, None, now).text == "remote: broker down"
    assert summarize(status(at=B + 4900), True, None, now).colour == "red"           # a stale file
    c = summarize(status(), True, None, now)
    assert (c.colour, c.text, c.problems) == ("grey", "remote: no workers", [])
    idle = status(workers=[*status()["workers"], worker(), worker("w2", fresh=False)])
    c = summarize(idle, True, None, now)
    assert (c.colour, c.text) == ("green", "remote: 1 worker · 7 cores · idle")
    job = {"id": "j1", "item": "code/001_sim", "shards": 36, "done": 18, "out": 6, "pending": 12, "eta": 230.0,
           "started": B + 4800}
    farming = status(job=job, workers=[*status()["workers"], worker(), worker("w2", cores=4, kind="local-remote")])
    c = summarize(farming, True, None, now)
    assert c.text == "remote: farming 18/36 shards · 2 workers · ETA 4 min" and c.colour == "green"
    assert c.farming == {"code/001_sim": "farming 18/36"}
    assert summarize(status(job=job), True, None, now).text == "remote: farming 18/36 shards · main box only · ETA 4 min"
    # Amber: a connected worker failed its gate (however long ago), with who, when and which reference.
    fail = {"worker": "w1", "label": "Edge on Mac", "reference": "wasm", "at": B + 1200}
    c = summarize(status(workers=[*status()["workers"], worker(gate="fail")], gate_fails=[fail]), True, None, now)
    assert c.colour == "amber" and c.problems[0].startswith("Edge on Mac failed the gate at ") \
        and c.problems[0].endswith("(wasm reference)")
    c = summarize(status(job=job, workers=[*status()["workers"], worker(gate="fail")], gate_fails=[fail]), True,
                  None, now)
    assert c.text == "remote: farming 18/36 shards · main box only · ETA 4 min"     # a failed worker isn't farming
    assert summarize(status(gate_fails=[fail]), True, None, now).colour == "grey"    # long gone, long ago
    assert summarize(status(gate_fails=[fail | {"at": B + 4900}]), True, None, now).colour == "amber"
    # Amber: shards re-run lately; a broker restarted under a running daemon (not one started with it).
    c = summarize(status(reruns=[B + 4800, B + 4850], workers=idle["workers"]), True, None, now)
    assert c.colour == "amber" and c.problems[0].startswith("2 shards re-run on the main box since")
    def stamp(t):
        return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(t))
    assert summarize(status(started=B + 4900), True, stamp(B + 1000), now).problems[0].startswith("the broker restarted")
    assert summarize(status(started=B + 4900), True, stamp(B + 4895), now).colour == "grey"
    assert summarize(status(started=B + 1000), True, stamp(B + 900), now).colour == "grey"


@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtw = pytest.importorskip("PySide6.QtWidgets")
    yield qtw.QApplication.instance() or qtw.QApplication([sys.argv[0]])


def test_chip_tab_and_copy_link_only_with_remote(qapp, project, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from conductor import procs
    from conductor.app.window import MainWindow
    monkeypatch.setattr(procs, "spawn_detached", lambda *a, **k: procs.Detached(0, None))
    plain, remote = project("plain"), project("remote", remote_section())
    win = MainWindow([plain.control, remote.control], tray=False, interval_ms=60_000,
                     settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    pv, rv = win.views[plain.control.resolve()], win.views[remote.control.resolve()]
    win.refresh()
    assert pv.tabs.count() == 4 and pv.remote.chip.isHidden() and pv.remote.copy_btn.isHidden()
    assert rv.tabs.count() == 5 and rv.tabs.tabText(4) == "Workers" and not rv.remote.chip.isHidden()
    assert "remote: broker down" in rv.remote.chip.text() and not rv.remote.copy_btn.isEnabled()
    state = remote.control / ".conductor" / "remote"
    state.mkdir(parents=True, exist_ok=True)
    url = "http://192.168.1.5:8770/w/abc/"
    s = status(at=time.time(), pid=os.getpid(), url=url, workers=[*status()["workers"], worker()])
    (state / "status.json").write_text(json.dumps(s), encoding="utf-8")
    win.refresh()
    assert "remote: 1 worker · 7 cores · idle" in rv.remote.chip.text() and rv.remote.copy_btn.isEnabled()
    assert rv.remote.panel.table.rowCount() == 2 and rv.remote.panel.table.item(0, 2).text() == "6 slots"
    assert rv.remote.panel.table.item(1, 5).text() == "3"
    rv.remote.copy_btn.click()
    assert QApplication.clipboard().text() == url
    rv.remote.chip.mousePressEvent(None)
    assert rv.tabs.currentWidget() is rv.remote.panel
    # The section removed: the chip and tab go with it.
    toml = remote.control / "conductor.toml"
    toml.write_text(toml.read_text(encoding="utf-8").split("\n[remote]")[0] + "\n", encoding="utf-8")
    os.utime(toml, (time.time() + 5, time.time() + 5))
    win.refresh()
    assert rv.tabs.count() == 4 and rv.remote.chip.isHidden()
    win.close()


def test_a_handover_for_a_code_change_is_not_amber(tmp_path):
    from conductor.remote import app
    from conductor.util import stamp
    from datetime import datetime
    now = B + 5000
    d0 = stamp(datetime.fromtimestamp(B))
    status = {"at": now, "started": now - 30, "workers": [], "gate_fails": [], "reruns": [], "job": None}
    assert "restarted" in " ".join(app.summarize(status, True, d0, now).problems)
    assert not app.summarize({**status, "handover": True}, True, d0, now).problems
