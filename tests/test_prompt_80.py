"""Prompt 80: a gentler load warning. Outside load is logged with no message; an item causing
it is told once, then not again for `load_tell_minutes`; the caution rides the event, *Now*
and the `progress` return."""

from conductor import ask_server, daemon_memory, inbox, load, memory
from test_prompt_68 import busy_box


def held(env):
    env.queue("001_hold", env.hold())
    env.tick_until(lambda: env.events("start"))


def samples(env, n=daemon_memory.LOAD_SAMPLES):
    for _ in range(n):
        env.d.sample_memory(force=True)


def load_tells(env):
    return [e for e in env.events("tell") if "threads free" in e.detail["text"]]


def test_outside_load_logs_with_no_message(env, monkeypatch):
    held(env)
    busy_box(env, monkeypatch, 95, own=10)                  # Defender's load, the item at 2 threads
    samples(env, 5)
    [ev] = env.events("load_high")
    assert ev.detail["items"] == [] and ev.detail["causes"] == []
    text = inbox.describe(ev)
    assert "mostly outside the items: none told" in text and "inflated" in text
    assert not load_tells(env)
    assert "**high** since" in memory.load_line(env.cfg)


def test_the_item_causing_it_is_told_once_then_not_for_15_minutes(env, monkeypatch):
    held(env)
    busy_box(env, monkeypatch, 95, own=60)
    samples(env, 6)
    assert len(load_tells(env)) == 1 and len(env.events("load_high")) == 1
    told = env.d.load_told["code/001_hold"]
    env.d.load_told["code/001_hold"] = told - 14 * 60       # 14 minutes on: still quiet
    env.d._load_high["logged"] -= 14 * 60
    samples(env, 1)
    assert len(load_tells(env)) == 1 and len(env.events("load_high")) == 1
    env.d.load_told["code/001_hold"] = told - 15 * 60 - 1   # past 15: told again
    samples(env, 1)
    assert len(load_tells(env)) == 2 and env.events("load_high")[-1].detail["items"] == ["code/001_hold"]


def test_a_real_share_is_a_quarter_of_the_box_or_three_threads(monkeypatch):
    monkeypatch.setattr(load.psutil, "cpu_count", lambda: 20)
    row = lambda pct: {"cpu": 95, "top": [{"who": "a/1", "cpu": pct, "item": True}]}
    assert load.own_share(row(15), "a/1")                   # 3 threads of 20
    assert not load.own_share(row(10), "a/1") and not load.own_share(row(60), "a/2")


def test_the_warning_waits_out_its_seconds(env, monkeypatch):
    held(env)
    busy_box(env, monkeypatch, 95)
    env.d.cfg.load_warn_seconds = 3600
    samples(env, 6)
    assert not env.events("load_high") and memory.load_high(env.cfg.state_dir) is None


def test_progress_carries_the_caution_once_per_stretch(env, monkeypatch):
    held(env)
    busy_box(env, monkeypatch, 95, own=10)
    samples(env, 4)
    server = ask_server.Server(env.control, "code/001_hold", 0, None, out=None)
    first, _ = server.progress({"note": "timing the bench"})
    assert "a rate or timing read now is inflated" in first
    assert "inflated" not in server.progress({"note": "again"})[0]
