"""Diagnostics and deadlines (98): the steps log, page logs, `remote-diag.md`, a gate that is
never answered, and a Node wasm reference or build that hangs."""

import json
import shutil
import sys
import time

import pytest

from conductor.remote import client, diag
from conductor.remote.board import GATE_SECONDS, Board

from .test_remote_board import Clock


def board(tmp_path, lease=600):
    clock = Clock()
    clock.t = time.time()
    b = Board(tmp_path, lease_seconds=lease, fresh_seconds=5, native_argv=["sim"], clock=clock)
    b.add_job("k1", "fp", [], [(0, 10), (10, 10)], ".")
    b.set_bundle("k1", "ready", wasm="wfp")
    return b, clock


def runs(tmp_path):
    return diag.pair(diag.read_steps(tmp_path))


def test_an_unanswered_gate_is_late_at_a_minute_whatever_the_lease_and_fails_the_second_time(tmp_path):
    # aicomplete 163: lease_seconds = 600 held a gate no core answered for 10 minutes.
    b, clock = board(tmp_path)
    b.hello("tab", "browser", cores=7)
    assert b.poll("tab")["task"] == "gate"
    clock.t += 10
    b.add_job("k1", "fp", [], [(0, 10)], ".")             # the main box keeps the job open
    assert b.poll("tab")["task"] == "idle"                 # pending, inside its deadline
    assert b.state_of("tab")["state"] == "preparing"
    clock.t += GATE_SECONDS
    b.add_job("k1", "fp", [], [(0, 10)], ".")
    b.hello("tab", "browser", cores=7)                     # its hellos go on: only the gate is stuck
    s = b.state_of("tab")
    assert s["state"] == "error" and "passed its 60 s deadline (try 1 of 2)" in s["detail"]
    r = runs(tmp_path)
    assert r[-1]["step"] == "gate" and r[-1]["end"] is None   # a stall: a start with no end
    assert "past its 60 s deadline" in diag._step_text(r[-1], clock.t)
    b.add_job("k1", "fp", [], [(0, 10)], ".")
    assert b.poll("tab")["task"] == "gate"                 # handed out again: try 2
    assert [x.get("result") for x in runs(tmp_path) if x["step"] == "gate"] == ["timeout", None]
    clock.t += GATE_SECONDS + 1
    b.add_job("k1", "fp", [], [(0, 10)], ".")
    b.hello("tab", "browser", cores=7)
    assert b.poll("tab")["task"] == "idle"                 # the second late gate fails
    s = b.state_of("tab")
    assert s["state"] == "error" and "no answer to the fingerprint check in 60 s, 2 times" in s["detail"]
    assert "no answer" in (tmp_path / "gates.log").read_text(encoding="utf-8")
    b.hello("tab", "browser", cores=7, fresh=True)         # a reload gates it afresh
    b.add_job("k1", "fp", [], [(0, 10)], ".")
    assert b.poll("tab")["task"] == "gate"


def test_a_reload_drops_a_pending_gate_at_once(tmp_path):
    b, clock = board(tmp_path)
    b.hello("tab", "browser")
    assert b.poll("tab")["task"] == "gate"
    clock.t += 5
    assert b.poll("tab")["task"] == "idle"
    b.hello("tab", "browser", fresh=True)                  # new cores: the old one's gate is nobody's
    assert b.poll("tab")["task"] == "gate"


def test_a_gate_answer_ends_its_step_and_the_first_shard_is_a_handout(tmp_path):
    b, _ = board(tmp_path)
    b.hello("tab", "browser")
    b.poll("tab")
    assert b.gate("tab", "k1", "wfp")
    assert b.poll("tab")["task"] == "shard"
    assert b.poll("tab")["task"] == "shard"
    r = runs(tmp_path)
    assert [(x["step"], x.get("result", "")[:5]) for x in r] == [("gate", "ok"), ("handout", "first")]
    assert r[0]["reference"] == "wasm" and r[0]["end"] is not None


def test_the_referencing_bundle_is_preparing_then_an_error_past_its_deadline(tmp_path):
    b, _ = board(tmp_path)
    b.add_job("k2", "fp", [], [(0, 10)], ".")
    b.hello("tab", "browser")
    b.set_bundle("k1", "referencing")
    b.set_bundle("k2", "referencing")
    s = b.state_of("tab")
    assert (s["state"], s["step"]) == ("preparing", "referencing")
    assert b.poll("tab")["task"] == "idle"                 # no gate before the reference
    b.set_bundle("k1", "referencing", error="Node passed its 120 s deadline and was killed; trying once more")
    assert b.state_of("tab")["state"] == "error"
    b.set_bundle("k1", "ready", note="browsers gated against the native reference: x")
    t = b.poll("tab")
    assert t["task"] == "gate" and b.reference_kind("tab", "k1") == "native"
    assert b.view("tab")["job"]["bundle"]["note"].startswith("browsers gated against the native")


def test_page_lines_go_on_the_brokers_clock_roll_at_500_and_follow_a_rename(tmp_path):
    now = time.time()
    lines = [{"t": (now - 3600 - 10) * 1000 + i, "s": "core3", "m": f"line {i}"} for i in range(150)]
    # the page's clock is an hour behind: the lines land on the broker's
    assert diag.page_lines(tmp_path, "b1", "WOrk laptop", lines, (now - 3600) * 1000, now) == 150
    got = diag.read_page(tmp_path, "b1")
    assert len(got) == 150 and abs(got[0][0] - (now - 10)) < 2 and got[0][1:] == ("core3", "line 0")
    for _ in range(4):
        diag.page_lines(tmp_path, "b1", "WOrk laptop", lines, now * 1000, now)
    assert len(diag.read_page(tmp_path, "b1")) == diag.PAGE_LINES
    diag.page_lines(tmp_path, "b1", "Laptop 2", lines[:1], now * 1000, now)
    assert [p.name for p in (tmp_path / "workers").iterdir()] == ["Laptop-2-b1.log"]
    old = time.time() - 8 * 86400
    import os
    os.utime(tmp_path / "workers" / "Laptop-2-b1.log", (old, old))
    diag.sweep(tmp_path)
    assert not list((tmp_path / "workers").iterdir())


def test_the_digest_shows_states_trouble_with_page_lines_and_events_interleaved(tmp_path):
    b, clock = board(tmp_path)
    b.hello("tab", "browser", cores=7, label="WOrk laptop")
    b.poll("tab")
    diag.page_lines(tmp_path, "tab", "WOrk laptop", [{"t": clock.t * 1000 + 500, "s": "core1", "m": "gate k1: handed out"}],
                    clock.t * 1000, clock.t)
    diag.step(tmp_path, "build", "start", "b1", clock.t - 100, key="k1")
    diag.step(tmp_path, "build", "end", "b1", clock.t - 70, key="k1", result="ok", exit=0)
    clock.t += GATE_SECONDS + 2
    b.add_job("k1", "fp", [], [(0, 10)], ".")              # a job still open
    b.hello("tab", "browser", cores=7, label="WOrk laptop")
    text = diag.render("aic", b.panel(6), tmp_path, clock.t)
    assert "- WOrk laptop (tab, browser, 7 cores): error · the fingerprint check for k1 passed" in text
    trouble = text.split("## Past deadline or failed")[1].split("##")[0]
    assert "fingerprint gate k1 for WOrk laptop: started, no end, past its 60 s deadline" in trouble
    assert "core1 gate k1: handed out" in trouble
    assert "wasm build k1: ok after 30.0 s; exit 0" in text
    events = text.split("## WOrk laptop (tab): last 2 events")[1]
    assert events.index("broker") < events.index("core1")
    assert diag.write_digest(tmp_path, text) and not diag.write_digest(tmp_path, text)
    assert (tmp_path / "remote-diag.md").read_text(encoding="utf-8") == text


class FakeBroker:
    def __init__(self):
        self.calls = []

    def call(self, op, body=None, **_):
        self.calls.append((op, body))
        return {"browsers": 1}


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_a_hanging_node_reference_is_killed_retried_once_then_browsers_gate_on_native(farm, monkeypatch):
    farm.setup(build='["x", "{bundle}"]')
    monkeypatch.setitem(diag.DEADLINES, "reference", 1.0)
    monkeypatch.setenv(client.FAULT_VAR, "node-hang")
    bundle = farm.r.state / "bundles" / "k1"
    bundle.mkdir(parents=True)
    (bundle / ".ok").write_text("ok\n")
    fake, notes = FakeBroker(), {}
    t0 = time.time()
    import threading
    client.ensure_bundle(farm.r, "k1", farm.r.control, fake, threading.Event(), notes)
    assert time.time() - t0 < 15
    states = [(b["state"], b.get("error", "")[:14], b.get("note", "")[:26]) for op, b in fake.calls if op == "bundle"]
    assert states == [("referencing", "", ""), ("referencing", "Node passed it", ""),
                      ("ready", "", "browsers gated against the")]
    assert "twice" in notes["fallback"]
    r = [x for x in diag.pair(diag.read_steps(farm.r.state))]
    assert [(x["step"], x.get("result")) for x in r] == [("reference", "timeout"), ("reference", "timeout"),
                                                        ("fallback", "fallback")]
    assert (bundle / "wasm.fallback").exists()             # the next job doesn't wait again
    t0 = time.time()
    assert client.wasm_reference(farm.r, "k1")[0] == "" and time.time() - t0 < 1


@pytest.mark.slow           # two killed builds: ~3s
def test_a_build_past_its_deadline_is_killed_and_tried_once_more(farm, monkeypatch, tmp_path):
    py = tmp_path / "hang.py"
    py.write_text("import time\nprint('building', flush=True)\ntime.sleep(60)\n", encoding="utf-8")
    farm.setup(build=f'["{sys.executable.replace(chr(92), "/")}", "{py.as_posix()}", "{{bundle}}"]')
    monkeypatch.setitem(diag.DEADLINES, "build", 1.0)
    root, key = client.repo_key(tmp_path / "code")
    assert client.build_bundle(farm.r, key, root) == 1
    failed = (farm.r.state / "bundles" / f"{key}.failed").read_text(encoding="utf-8")
    assert "deadline, twice" in failed
    r = diag.pair(diag.read_steps(farm.r.state))
    assert [(x["step"], x.get("result"), x.get("try")) for x in r] == [("build", "timeout", 1), ("build", "timeout", 2)]
    assert "building" in r[0]["stderr"]


def test_the_brokers_hello_takes_the_pages_log(farm):
    farm.setup()
    b = client.Broker.find(farm.r)
    now = time.time() * 1000
    reply = b.call("hello", {"worker": "b1", "kind": "browser", "label": "WOrk laptop", "clock": now,
                             "log": [{"t": now, "s": "page", "m": "joined"}]})
    assert reply["log_taken"] == 1
    assert diag.read_page(farm.r.state, "b1")[0][1:] == ("page", "joined")
    assert json.loads(json.dumps(reply))["ok"]

