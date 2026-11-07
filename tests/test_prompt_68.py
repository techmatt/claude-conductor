"""Prompt 68's rules: the preamble's new lines, `ask` beside an amendment, an `eta`'s source,
the load warning and the app's restart offer (its rules since 71: `test_prompt_71.py`)."""

import importlib.util
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from conductor import ask_server, daemon_memory, inbox, load, nightshift, notify, questions
from conductor.util import now
from test_memory import status
from test_preamble import SHORT, render


def ask(server, question, **args):
    return server.ask({"question": question, **args}, threading.Event())


def test_the_preamble_carries_the_new_lines(env):
    full = " ".join(render(env, SHORT * 3).split())
    assert "Every background call sets a `timeout` (a kill limit; 10 minutes if left out)" in full
    assert "post the gap in SE and the rule being applied" in " ".join(render(env, SHORT * 3, rules=True).split())
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


def busy_box(env, monkeypatch, cpu, own=None):
    """A 20-thread box `cpu`% busy, `own`% of it (all, by default) the held item's; 80's
    3-minute wait off, so samples alone decide."""
    monkeypatch.setattr(load.psutil, "cpu_count", lambda: 20)
    env.d.cfg.memory_sample_seconds = 0.01
    env.d.cfg.load_warn_seconds = 0
    env.d.memory_probe = lambda: status(50, 10)
    own = cpu if own is None else own
    top = [{"who": "code/001_hold", "cpu": own, "item": True}] if own else []
    if cpu > own:
        top.append({"who": "MsMpEng.exe", "cpu": cpu - own, "item": False})
    sample = {"cpu": cpu, "top": sorted(top, key=lambda t: -t["cpu"])}
    env.d._load.sample = lambda owned: sample


def test_a_short_box_warns_once_per_item_after_a_run_of_samples(env, monkeypatch):
    env.queue("001_hold", env.hold())
    env.tick_until(lambda: env.events("start"))
    busy_box(env, monkeypatch, 95)                          # 1 of 20 threads free
    for _ in range(daemon_memory.LOAD_SAMPLES - 1):
        env.d.sample_memory(force=True)
    assert not env.events("load_high")
    for _ in range(3):
        env.d.sample_memory(force=True)
    [ev] = env.events("load_high")
    assert ev.detail["free"] == 1.0 and ev.detail["items"] == ["code/001_hold"]
    assert "under 2" in inbox.describe(ev) and "inflated" in inbox.describe(ev)
    assert notify.message("p", ev.kind, ev.item, ev.repo, ev.detail) is None     # 70: no toast
    [tell] = [e for e in env.events("tell") if "threads free" in e.detail["text"]]
    assert tell.item == "code/001_hold" and tell.detail["by"] == "conductor"


def test_a_spike_or_a_box_with_threads_to_spare_never_warns(env, monkeypatch):
    env.queue("001_hold", env.hold())
    env.tick_until(lambda: env.events("start"))
    busy_box(env, monkeypatch, 85)                          # 3 free: under 68's 4, not 80's 2
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


def restart_tool():
    path = Path(__file__).resolve().parent.parent / "tools" / "restart_all.py"
    spec = importlib.util.spec_from_file_location("restart_all", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_restart_tool_refuses_an_unknown_flag(monkeypatch):
    tool = restart_tool()
    monkeypatch.setattr(tool, "controls", lambda: pytest.fail("an unknown flag must not restart"))
    assert tool.main(["--help"]) == 2


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
