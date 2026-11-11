"""105: a rewritten staged item keeps its amendments (aicomplete 177), `[remote] default`
(aicomplete 175-178), Windows-update restarts and the runs they cut off (workbench 009),
and `external_data` items."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from conductor import executor, inbox, items, procs, remote, winupdate
from conductor.events import Event
from conductor.util import now
from test_amend import amend_file, reports


# ------------------------------------------------------------------ amendments through a restage
@pytest.mark.slow
def test_a_rewritten_staged_item_keeps_its_amendments(env):
    env.queue("001_long", env.hold())
    env.queue("002_next", "FAKE commit b.txt b")
    env.tick_until(lambda: env.events("staged"))
    amend_file(env, "002_next", "Keep the c column.")    # no FAKE line: the stage memory would run it twice
    env.tick_until(lambda: env.events("amended"))
    [am] = env.events("amended")
    env.queue("002_next", "FAKE commit d.txt d")
    env.tick_until(lambda: len(env.events("staged")) == 2)
    [edited] = env.events("edited")
    assert edited.detail["staged"] and edited.detail["amendments"] == [am.id]
    assert any(f"amendment #{am.id} still stands" in e.detail.get("note", "") for e in env.events("warning"))
    receipt = (env.control / "receipts" / "queue" / "code" / "002_next.md").read_text(encoding="utf-8")
    assert f"#{am.id} still stands" in receipt and "don't re-send" in receipt
    stage_prompt = reports(env, "002_next", ".stage.prompt.md")
    assert "Amendments already sent" in stage_prompt and "Keep the c column." in stage_prompt
    env.release()
    env.tick_until(lambda: env.finished("code/002_next"))
    [got] = env.events("delivered")
    assert got.detail["via"] == "resume" and got.detail["amend"] == am.id
    assert "Keep the c column." in reports(env, "002_next", ".prompt.md")
    assert (env.repo / "d.txt").is_file() and not (env.repo / "b.txt").exists()


def test_amends_line_counts():
    from conductor import talk
    assert talk.amends_line([7]).startswith("amendment #7 still stands:")
    assert talk.amends_line([7, 9]).startswith("amendments #7, #9 still stand:")


# ------------------------------------------------------------------ remote by default
def _control(tmp_path: Path, default: str | None) -> Path:
    keys = 'native = ["sim", "{out}"]\nfingerprint = ["sim", "fp"]\n' + (f"default = {default}\n" if default else "")
    (tmp_path / "conductor.toml").write_text(f'project = "p"\n[remote]\n{keys}', encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("meta, default, want", [
    ({}, "true", True), ({"readonly": True}, "true", False), ({"template": "cleanup"}, "true", False),
    ({"remote": False}, "true", False), ({}, None, False), ({"remote": True}, None, True), ({}, "false", False),
])
def test_remote_default_decides_an_item_without_remote(tmp_path, meta, default, want):
    item = SimpleNamespace(**({"remote": None, "readonly": False, "template": None} | meta))
    assert remote.wants(_control(tmp_path, default), item) is want


def test_remote_is_none_unless_the_item_says(tmp_path):
    p = tmp_path / "queue" / "code" / "001_a.md"
    p.parent.mkdir(parents=True)
    p.write_text("---\nrepo: code\n---\nbody\n", encoding="utf-8")
    assert items.parse(p, "code").remote is None
    p.write_text("---\nrepo: code\nremote: false\n---\nbody\n", encoding="utf-8")
    assert items.parse(p, "code").remote is False


def test_a_bad_default_is_refused_and_the_broker_ignores_it(tmp_path):
    from conductor.remote import settings
    with pytest.raises(settings.RemoteError, match="remote.default"):
        settings.load(_control(tmp_path, '"yes"'))
    a = settings.load(_control(tmp_path, "true")).digest()
    assert a == settings.load(_control(tmp_path, None)).digest()


# ------------------------------------------------------------------ Windows-update restarts
def test_active_hours_read_as_clock_times():
    assert winupdate.hours_text((8, 17)) == "8am-5pm"
    assert winupdate.hours_text((0, 12)) == "12am-12pm"
    assert winupdate.hours_text(None) == ""


def test_a_pending_update_restart_warns_once_while_work_waits(env, monkeypatch):
    monkeypatch.setattr(winupdate, "pending", lambda: {"active": (8, 17)})
    env.d.check_update_restart()
    assert not [e for e in env.events("warning") if e.detail.get("update_restart")]     # nothing queued
    env.queue("001_a", env.hold())
    for _ in range(2):
        env.d._update_due = 0.0
        env.d.check_update_restart()
    [w] = [e for e in env.events("warning") if e.detail.get("update_restart")]
    assert "outside its active hours (8am-5pm)" in w.detail["note"]


def _cut(env):
    """Kill the running executor once its session has a transcript (a session cut before it
    has one can't be resumed), and start a new daemon over the same folders."""
    from conductor import resume
    start = env.events("start")[0].detail
    env.tick_until(lambda: resume.session_file(start["session_id"]))
    procs.kill_tree(start["pid"])
    env.d.runs.clear()
    env.d.log.close()
    env.d = env.new_daemon()


@pytest.mark.slow
def test_a_run_cut_off_resumes_its_session_in_place(env, monkeypatch):
    env.queue("001_long", "FAKE sleep 30", external_data="true")
    env.tick_until(lambda: env.events("start"))
    _cut(env)
    [rec] = [e for e in env.events("recover") if e.detail.get("continued")]
    assert rec.detail["continued"] == 1 and "its session resumed in place" in rec.detail["note"]
    assert "resumed in place" in inbox.describe(rec)
    prompt = (env.control / rec.paths[0]).read_text(encoding="utf-8")
    assert "cut off at about" in prompt and "outside git" in prompt and "write your report" in prompt.lower()
    run = json.loads(next((env.control / "running" / "code").glob("*.run.json")).read_text(encoding="utf-8"))
    assert run["continued"] == 1 and run["session_id"] == env.events("start")[0].detail["session_id"]
    env.tick_until(lambda: env.finished("code/001_long"))
    assert len(env.events("start")) == 1


@pytest.mark.slow
def test_a_second_cut_is_red_and_names_the_restart(env, monkeypatch):
    env.queue("001_long", "FAKE sleep 30", external_data="true")
    env.queue("002_next", "FAKE report done")
    env.tick_until(lambda: env.events("start"))
    path = next((env.control / "running" / "code").glob("*.run.json"))
    rec = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(rec | {"continued": 1}), encoding="utf-8")
    from conductor import daemon_machine
    monkeypatch.setattr(daemon_machine.winupdate, "boot_time", now)
    monkeypatch.setattr(daemon_machine.winupdate, "restart_reason", lambda: "Operating System: Upgrade (Planned)")
    _cut(env)
    fin = env.finished("code/001_long")
    assert fin.detail["status"] == "red"
    assert "the machine restarted (Operating System: Upgrade (Planned))" in fin.detail["reasons"][0]
    assert "(after its resume)" in fin.detail["reasons"][0]
    red = env.d.log.last("red", item="code/001_long")
    assert red.detail["external_data"] == "conductor/001_long"
    assert "`land: code/001_long` first" in inbox.describe(red)
    env.tick_until(lambda: env.events("hold"))
    assert len(env.events("start")) == 1


# ------------------------------------------------------------------ external_data
def test_external_data_items_are_told_to_commit_before_each_step(env):
    assert "Data outside git" in env_prompt(env, external_data=True)
    assert "Data outside git" not in env_prompt(env)


def env_prompt(env, **meta) -> str:
    item = items.Item("code", "001_a", env.control / "queue" / "code" / "001_a.md", num=1,
                      body="# Dedupe\n\nDo it.", **meta)
    paths = executor.RunPaths.for_item(env.cfg, item)
    from datetime import datetime
    return executor.build_prompt(env.cfg, item, paths, 1.0, datetime(2026, 11, 11, 17), {"port": 5200})


def test_a_red_without_external_data_offers_nothing():
    ev = Event(1, "", "red", "code/001_a", "code", [], {"reasons": ["x"], "after_red": "stop"})
    assert "land:" not in inbox.describe(ev)
