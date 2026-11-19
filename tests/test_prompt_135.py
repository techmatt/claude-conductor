"""135: the model toggle. One machine-wide setting (`models.py`) every project's daemon reads
at launch; a stage, an API or restart resume and an ask fork keep the model their session
was launched with; a `model:` signal flips it; the start event, the finish event and the
report header record what ran."""

import json

import pytest

from conductor import executor, inbox, models
from conductor.daemon_merge import stamp_model
from conductor.util import read_text

OPUS, FABLE = models.MODELS["opus"], models.MODELS["fable"]


@pytest.fixture
def setting(tmp_path, monkeypatch):
    path = tmp_path / "model.json"
    monkeypatch.setenv(models.ENV, str(path))
    return path


def report(env, name: str) -> str:
    return read_text(env.control / "reports" / f"code__{name}.md")


def receipt(env, name: str) -> str:
    return read_text(env.control / "receipts" / "signals" / f"{name}.md")


def send(env, name: str, text: str) -> None:
    env.signal(f"{name}.signal", text)
    env.tick_until(lambda: (env.control / "receipts" / "signals" / f"{name}.md").is_file())


def test_the_setting_defaults_to_opus_and_survives_a_bad_file(setting):
    assert models.current() == "opus" and models.current_id() == OPUS
    assert models.set_model("Fable", "test") == "opus"
    assert models.current_id() == FABLE and json.loads(setting.read_text())["by"] == "test"
    assert models.describe() == f"Fable ({FABLE})" and models.name_of(FABLE) == "fable"
    with pytest.raises(ValueError):
        models.set_model("sonnet", "test")
    setting.write_text("{not json", encoding="utf-8")
    assert models.current() == "opus"


def test_the_command_line_carries_the_model(env):
    argv = executor.command(env.cfg, "sid", None, model=FABLE)
    assert argv[argv.index("--model") + 1] == FABLE
    assert "--model" not in executor.command(env.cfg, "sid", None)        # a run from before 135
    fork = executor.command(env.cfg, "new", None, fork_of="old", model=OPUS)
    assert fork[fork.index("--model") + 1] == OPUS and "--fork-session" in fork


def test_the_report_stamp_goes_after_the_whole_header(tmp_path):
    p = tmp_path / "r.md"
    p.write_text("status: done\nblocks: none\ncarry: x\nfriction: y\nconductor: z\n\n# Report\nmodel: body\n",
                 encoding="utf-8")
    assert stamp_model(p, FABLE)
    assert stamp_model(p, OPUS)                         # replaced, never doubled
    lines = read_text(p).splitlines()
    assert lines[5] == f"model: {OPUS}" and lines.count(f"model: {FABLE}") == 0
    assert lines[-1] == "model: body"                   # the body's own line is the body's
    assert executor.read_header(p)[0]["friction"] == "y"
    assert not stamp_model(tmp_path / "none.md", OPUS)


@pytest.mark.slow
def test_a_signal_switches_new_sessions_only(env, setting):
    env.queue("001_long", env.hold())
    env.queue("002_next", "FAKE model")
    env.tick_until(lambda: env.events("staged"))                # 002 staged on Opus
    send(env, "planner-1", "by: planner\nmodel: fable")
    text = receipt(env, "planner-1")
    assert "outcome: \"accepted\"" in text and f"model is now Fable ({FABLE})" in text
    assert models.current() == "fable"
    [ev] = env.events("model")
    assert (ev.detail["model"], ev.detail["was"], ev.detail["by"]) == ("fable", "opus", "planner")
    send(env, "planner-2", "by: planner\nmodel: fable")
    assert "already Fable" in receipt(env, "planner-2") and len(env.events("model")) == 1
    send(env, "planner-3", "by: planner\nmodel: sonnet")
    assert "refused" in receipt(env, "planner-3") and "`model: opus|fable`" in receipt(env, "planner-3")
    now = read_text(env.control / "inbox.md").split("## Now", 1)[1]
    assert f"model for new sessions: **Fable ({FABLE})**" in now
    assert "001_long (on Opus" in now                           # the running item keeps its own
    env.release()
    env.tick_until(lambda: env.finished("code/002_next"))
    assert env.d.log.last("start", item="code/002_next").detail["resumed"]
    assert f"MODEL={OPUS}" in report(env, "002_next")          # the stage's model, not the toggle's
    env.queue("003_new", "FAKE model")
    env.tick_until(lambda: env.finished("code/003_new"))
    text = report(env, "003_new")
    assert f"MODEL={FABLE}" in text and f"\nmodel: {FABLE}\n" in text
    assert env.d.log.last("start", item="code/003_new").detail["model"] == FABLE
    assert env.finished("code/003_new").detail["model"] == FABLE
    # An ask forks 002's session: it keeps 002's model, whatever the toggle says.
    env.queue("004_ask", "FAKE model\n1. Why?", resume="code/002_next")
    env.tick_until(lambda: env.finished("code/004_ask"))
    assert f"MODEL={OPUS}" in report(env, "004_ask")


def test_the_inbox_line_names_the_setting(setting):
    assert "**Opus (claude-opus-5-5)**" in inbox.model_line()
    models.set_model("fable", "test")
    assert "**Fable (claude-fable-5-2)**" in inbox.model_line()


def test_the_app_menu_writes_the_setting(qapp, setting):
    from PySide6.QtWidgets import QWidget
    from conductor.app.model_menu import ModelMenu
    said, parent = [], QWidget()                                # kept: the button dies with its parent
    menu = ModelMenu(parent, said.append)
    tray = menu.tray_menu(parent)
    assert menu.button.text() == "Model: Opus" and len(tray.actions()) == 2
    menu.choose("fable")
    assert models.current() == "fable" and menu.button.text() == "Model: Fable"
    assert [n for n, a in menu.actions if a.isChecked()] == ["fable", "fable"]
    assert "every project" in said[-1]
    models.set_model("opus", "planner")                         # a signal elsewhere: the next refresh shows it
    menu.sync()
    assert menu.button.text() == "Model: Opus"
