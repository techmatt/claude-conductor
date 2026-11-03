"""Prompt 71: "restart all" interrupts no item. `restart: daemon` takes the self-restart
path (safe point, executors adopted), the app offers it while items run, and the tool
waits for each daemon's new pid."""

import json
import os
import time
from types import SimpleNamespace

import pytest

from conductor.app import model
from test_prompt_68 import restart_tool
from test_restart import stand_in


def settled_signal(env, name: str, text: str) -> None:
    env.signal(name, text)
    old = time.time() - 5                         # past the settle time, as in test_restart
    os.utime(env.control / "signals" / name, (old, old))


@pytest.mark.slow
def test_an_asked_restart_keeps_the_running_item_and_the_new_daemon_adopts_it(env):
    env.queue("001_long", env.hold() + "\nFAKE commit a.txt a")
    env.tick_until(lambda: env.events("start"))
    spawned = []
    stand_in(env.d, ["v1"], spawned, checkout=["conductor's checkout has uncommitted changes"])
    env.d.code_version = "v1"                     # unchanged code; a dirty checkout doesn't hold an ask
    settled_signal(env, "r.signal", "by: cli\nrestart: daemon\n")
    env.tick_until(lambda: env.d._restart_asked is not None)
    assert env.d.restart_due() and spawned
    [ev] = env.events("daemon_restart")
    assert ev.detail["reason"] == "asked by cli (restart: daemon)" and ev.detail["running"] == ["code/001_long"]
    env.d.runs.clear()
    env.d.release()
    env.d.log.close()
    env.d = env.new_daemon()
    assert [e.item for e in env.events("adopt")] == ["code/001_long"]
    env.release()
    env.tick_until(lambda: env.finished("code/001_long"))
    assert env.finished("code/001_long").detail["status"] == "done"
    assert not env.events("red") and not env.events("daemon_stop")


def test_an_asked_restart_names_its_blockers_at_once_and_waits(env):
    d, spawned = env.d, []
    stand_in(d, [None], spawned)
    d.code_version = None                         # not a git checkout: only a code change is off
    rdir = env.control / "running" / "code"
    rdir.mkdir(parents=True, exist_ok=True)
    setup = rdir / "001_a.run.json"
    setup.write_text(json.dumps({"item": "code/001_a", "phase": "setup"}), encoding="utf-8")
    d.ask_restart("signals/r.signal", "restart_all")
    assert not d.restart_due() and not d.restart_due() and not spawned
    [warn] = env.events("warning")
    assert warn.detail["note"].startswith("`restart: daemon` (by restart_all) waits for a safe point: "
                                          "code/001_a: its setup is running")
    setup.unlink()
    assert d.restart_due() and spawned and env.events("daemon_restart")[0].detail["to"] == "unknown"


def test_an_asked_restart_that_fails_is_dropped_with_a_warning(env):
    d, spawned = env.d, []
    stand_in(d, ["v1"], spawned, import_error="SyntaxError: half-written")
    d.code_version = "v1"
    d.ask_restart("signals/r.signal", "cli")
    assert not d.restart_due() and d._restart_asked is None
    [warn] = env.events("warning")
    assert "`restart: daemon` (by cli) not done: does not import: SyntaxError" in warn.detail["note"]
    assert not d.restart_due() and not spawned    # not retried: asking again does that


def test_a_stop_beside_the_ask_wins(env):
    stand_in(env.d, ["v1"], [])
    env.d.code_version = "v1"
    settled_signal(env, "r.signal", "by: cli\nrestart: daemon\nstop: daemon\n")
    env.d.tick()
    assert env.d.stop_requested is not None and not env.d.restart_due()
    assert env.d._restart_asked is None and not env.events("daemon_restart")


def test_the_offer_shows_while_items_run():
    assert model.restart_due([SimpleNamespace(code_differs=True), SimpleNamespace(code_differs=False)])
    assert not model.restart_due([SimpleNamespace(code_differs=False)])
    assert not model.restart_due([])


def tool_with(monkeypatch, tmp_path, pids):
    tool, signals = restart_tool(), []
    monkeypatch.setattr(tool.config, "load", lambda c: SimpleNamespace(events_db=tmp_path / "events.db"))
    seq = iter(pids)
    monkeypatch.setattr(tool.state, "daemon_info", lambda c: SimpleNamespace(pid=next(seq, pids[-1]), alive=True))
    monkeypatch.setattr(tool.actions, "write_signal", lambda c, *lines, by: signals.append((c, lines, by)))
    monkeypatch.setattr(tool.procs, "alive", lambda pid, ct: True)
    monkeypatch.setattr(tool.time, "sleep", lambda s: None)
    return tool, signals


def test_the_tool_asks_each_daemon_and_waits_for_its_new_pid(monkeypatch, tmp_path, capsys):
    tool, signals = tool_with(monkeypatch, tmp_path, [10, 10, 11])
    assert tool.restart_daemons([tmp_path]) == []
    assert signals == [(tmp_path, ("restart: daemon",), "restart_all")]
    assert "daemon 10 restarted, 11 now (nothing running)" in capsys.readouterr().out


def test_the_tool_reports_a_daemon_still_waiting(monkeypatch, tmp_path, capsys):
    tool, _ = tool_with(monkeypatch, tmp_path, [10])
    assert tool.restart_daemons([tmp_path], wait=0) == [tmp_path]
    out = capsys.readouterr().out
    assert "not restarted after 0s (no blocker named yet)" in out and "`--force`" in out


def test_the_tool_restarts_the_app_either_way_and_force_stops_and_starts(monkeypatch, tmp_path):
    tool, done = restart_tool(), []
    monkeypatch.setattr(tool, "controls", lambda: [tmp_path])
    monkeypatch.setattr(tool, "apps", lambda: [])
    monkeypatch.setattr(tool.config, "load", lambda c: c)
    monkeypatch.setattr(tool.state, "daemon_info", lambda c: SimpleNamespace(alive=True))
    monkeypatch.setattr(tool, "restart_daemons", lambda live: (done.append("asked"), live)[1])
    monkeypatch.setattr(tool, "force_daemon", lambda c: done.append("forced"))
    monkeypatch.setattr(tool, "restart_app", lambda apps, fallback: done.append("app"))
    assert tool.main([]) == 1 and done == ["asked", "app"]           # one daemon still waiting
    done.clear()
    assert tool.main(["--force"]) == 0 and done == ["forced", "app"]
