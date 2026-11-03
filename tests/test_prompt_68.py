"""Prompt 68's rules: the preamble's new lines, `ask` beside an amendment, an `eta`'s source,
the load warning and the app's restart offer."""

import importlib.util
import threading
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from conductor import ask_server, daemon_memory, inbox, load, nightshift, notify, questions
from conductor.app import model
from conductor.util import now
from test_memory import status
from test_preamble import SHORT, render


def ask(server, question, **args):
    return server.ask({"question": question, **args}, threading.Event())


def test_the_preamble_carries_the_new_lines(env):
    full = " ".join(render(env, SHORT * 3).split())
    assert "A background call's `timeout` is a kill limit, not a wait" in full
    assert "post the gap in SE and the rule being applied" in full
    assert "`5:40pm (by rate)`" in full
    assert "finders and verifiers" in full and "never how big" in full


def test_an_amendment_named_beside_a_default_is_refused_once(env):
    env.queue("001_hold", env.hold())
    env.tick_until(lambda: env.events("start"))
    server = ask_server.Server(env.control, "code/001_hold", 0, None, out=None)
    text, refused = ask(server, "Amendment #2 says 30 runs; stop at 20?", default="20")
    assert refused and "default overrides prompt" in text and not questions.scan(env.control)
    assert ask(server, "Amendment #2 says 30 runs; stop at 20?", default="20")[1] is False     # the same call again
    assert ask(server, "default overrides prompt: 30 runs\nThe amended count is 30; stop at 20?", default="20")[1] is False
    assert ask(server, "Which seed?", default="the amended one")[1] is True                    # the default names it
    assert ask(server, "Which seed?", default="the first")[1] is False


@pytest.mark.parametrize("question, default, hit", [
    ("Per the amendment, go on?", "yes", True), ("AMENDED band: keep it?", "yes", True),
    ("Keep the band?", "per amendment 3", True), ("Keep the band?", "yes", False),
    ("default overrides prompt: x\nThe amendment wins?", "yes", False)])
def test_what_counts_as_naming_an_amendment(question, default, hit):
    assert questions.unflagged_amendment(question, default) is hit


def test_an_eta_with_its_source_still_parses():
    posted = now()
    assert nightshift.parse_eta("5:40pm (by rate)", posted) == nightshift.parse_eta("5:40pm", posted)
    assert nightshift.parse_eta("25m (to the cap)", posted) == posted + timedelta(minutes=25)
    assert nightshift.parse_eta("(to the cap)", posted) is None


def busy_box(env, monkeypatch, cpu):
    monkeypatch.setattr(load.psutil, "cpu_count", lambda: 20)
    env.d.cfg.memory_sample_seconds = 0.01
    env.d.memory_probe = lambda: status(50, 10)
    sample = {"cpu": cpu, "top": [{"who": "code/001_hold", "cpu": cpu, "item": True}]}
    env.d._load.sample = lambda owned: sample


def test_a_short_box_warns_once_per_item_after_a_run_of_samples(env, monkeypatch):
    env.queue("001_hold", env.hold())
    env.tick_until(lambda: env.events("start"))
    busy_box(env, monkeypatch, 90)                          # 2 of 20 threads free
    for _ in range(daemon_memory.LOAD_SAMPLES - 1):
        env.d.sample_memory(force=True)
    assert not env.events("load_high")
    for _ in range(3):
        env.d.sample_memory(force=True)
    [ev] = env.events("load_high")
    assert ev.detail["free"] == 2.0 and ev.detail["items"] == ["code/001_hold"]
    assert "under the 4 kept" in inbox.describe(ev)
    assert notify.message("p", ev.kind, ev.item, ev.repo, ev.detail) is None     # 70: no toast
    [tell] = [e for e in env.events("tell") if "threads free" in e.detail["text"]]
    assert tell.item == "code/001_hold" and tell.detail["by"] == "conductor"


def test_a_spike_or_a_box_with_threads_to_spare_never_warns(env, monkeypatch):
    env.queue("001_hold", env.hold())
    env.tick_until(lambda: env.events("start"))
    busy_box(env, monkeypatch, 70)                          # 6 free
    for _ in range(4):
        env.d.sample_memory(force=True)
    busy_box(env, monkeypatch, 95)
    for _ in range(daemon_memory.LOAD_SAMPLES - 1):
        env.d.sample_memory(force=True)
    busy_box(env, monkeypatch, 70)
    env.d.sample_memory(force=True)
    busy_box(env, monkeypatch, 95)
    env.d.sample_memory(force=True)
    assert not env.events("load_high")


def test_restart_is_offered_only_with_other_code_and_nothing_active():
    snap = lambda differs, active: SimpleNamespace(code_differs=differs, active=active)
    assert model.restart_due([snap(True, False), snap(False, False)])
    assert not model.restart_due([snap(True, False), snap(False, True)])
    assert not model.restart_due([snap(False, False)])
    assert not model.restart_due([])


def test_the_snapshot_is_active_on_a_question_but_not_a_design_one(env):
    project = model.Project(env.control)
    assert not project.snapshot().active
    questions.create(env.control, "code/001_x", "Keep it?", [], "keep", False, 10, design=True)
    assert not project.snapshot().active
    questions.create(env.control, "code/001_x", "Keep that?", [], "keep", False, 10)
    assert project.snapshot().active


def restart_tool():
    path = Path(__file__).resolve().parent.parent / "tools" / "restart_all.py"
    spec = importlib.util.spec_from_file_location("restart_all", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("busy, code, restarted", [(1, 1, False), (0, 0, True)])
def test_restart_all_if_idle_refuses_while_anything_is_active(monkeypatch, tmp_path, busy, code, restarted):
    tool, done = restart_tool(), []
    monkeypatch.setattr(tool, "controls", lambda: [tmp_path])
    monkeypatch.setattr(tool, "apps", lambda: [])
    monkeypatch.setattr(tool.config, "load", lambda c: c)
    monkeypatch.setattr(tool.state, "daemon_info", lambda c: SimpleNamespace(alive=True))
    monkeypatch.setattr(tool, "check", lambda live, apps: busy)
    monkeypatch.setattr(tool, "restart_daemon", lambda c: done.append(c))
    monkeypatch.setattr(tool, "restart_app", lambda apps, fallback: done.append("app"))
    assert tool.main(["--if-idle"]) == code
    assert bool(done) is restarted


def test_the_header_shows_the_offer_and_its_click_restarts(qapp, env, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from conductor.app import actions, window
    win = window.MainWindow([env.control], settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat),
                            tray=False, interval_ms=60_000)
    view = win.views[env.control]
    assert view.restart_btn.isHidden()
    monkeypatch.setattr(window, "restart_due", lambda snaps: True)
    win.refresh()
    assert not view.restart_btn.isHidden()
    started = []
    monkeypatch.setattr(actions, "restart_all", started.append)
    view.restart_btn.click()
    assert started == [env.control] and not view.restart_btn.isEnabled()
    win.close()
