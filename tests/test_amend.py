"""`amend`: additions to an item's instructions, delivered by the item's state."""

import pytest

from conductor import cli, items
from conductor.app import actions, model
from conductor.inbox import describe


def amend_file(env, name: str, text: str, n: int = 1, by: str | None = "planner"):
    p = env.control / "queue" / "code" / (f"{name}.amend.md" if n == 1 else f"{name}.amend.{n}.md")
    p.write_text((f"---\nby: {by}\n---\n\n" if by else "") + text + "\n", encoding="utf-8")
    return p


def reports(env, name: str, suffix: str = ".md") -> str:
    return (env.control / "reports" / f"code__{name}{suffix}").read_text(encoding="utf-8")


def test_amend_files_are_never_items(tmp_path):
    for name in ("001_x.md", "001_x.amend.md", "001_x.amend.2.md", "002_y.z.md"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    assert items.amend_of(tmp_path / "001_x.amend.2.md") == ("001_x", 2)
    assert items.amend_of(tmp_path / "001_x.amend.md") == ("001_x", 1)
    assert items.amend_of(tmp_path / "001_x.md") is None
    assert [p.name for p in items.item_files(tmp_path)] == ["001_x.md", "002_y.z.md"]
    assert [p.name for p in items.find(tmp_path, "001")] == ["001_x.md"]
    # The writer goes after any amendment already waiting.
    assert actions.amend(tmp_path, "q/001_x", "more").name == "001_x.amend.md"
    (tmp_path / "queue" / "q" / "001_x.amend.4.md").write_text("x", encoding="utf-8")
    p = actions.amend(tmp_path, "q/001_x", "  and more \n")
    assert p.name == "001_x.amend.5.md" and p.read_text(encoding="utf-8") == "---\nby: app\n---\n\nand more\n"
    with pytest.raises(ValueError):
        actions.amend(tmp_path, "q/001_x", " \n")


@pytest.mark.slow
def test_queued_amendments_are_appended_at_launch(env):
    env.queue("001_long", env.hold())
    env.queue("002_next", "FAKE commit b.txt b", stage="false")
    env.tick_until(lambda: env.d.log.last("start", item="code/001_long"))
    first = amend_file(env, "002_next", "FAKE commit c.txt c")
    second = amend_file(env, "002_next", "Also: keep it short.", n=2, by=None)
    env.tick_until(lambda: len(env.events("amended")) == 2)
    a1, a2 = env.events("amended")
    assert (a1.item, a1.detail["state"], a1.detail["by"], a1.detail["text"]) == (
        "code/002_next", "queued", "planner", "FAKE commit c.txt c")
    assert a2.detail["by"] == "unknown" and "at launch" in a2.detail["delivery"]
    assert not first.exists() and not second.exists()
    assert not env.events("edited") and not env.events("invalid")
    assert [p.stem for p in items.item_files(env.control / "queue" / "code")] == ["002_next"]
    env.release()
    env.tick_until(lambda: env.finished("code/002_next"))
    prompt = reports(env, "002_next", ".prompt.md")
    head = f"## Amendment #{a1.id} ("
    assert prompt.index("FAKE commit b.txt b") < prompt.index(head) < prompt.index(f"## Amendment #{a2.id} (")
    assert "via planner): it amends the prompt above and wins where they conflict" in prompt
    assert (env.repo / "c.txt").is_file() and (env.repo / "b.txt").is_file()     # the executor ran it
    got = env.events("delivered")
    assert [(e.detail["amend"], e.detail["via"]) for e in got] == [(a1.id, "prompt"), (a2.id, "prompt")]
    kept = reports(env, "002_next", ".amend.md")
    assert kept.startswith("# Amendments to code/002_next")
    assert kept.index(f"## Amendment #{a1.id}") < kept.index(f"## Amendment #{a2.id}")
    assert "while queued; delivered" in kept and "via prompt" in kept and "Also: keep it short." in kept
    assert got[0].detail["kept"] == "reports/code__002_next.amend.md"
    assert "while queued: appended to the prompt at launch" in describe(a1)
    assert "amendment #" in describe(got[0])


def test_an_amendment_written_with_its_item_still_lands(env):
    env.queue("001_new", "FAKE report done")
    amend_file(env, "001_new", "FAKE commit d.txt d")
    env.tick_until(lambda: env.finished("code/001_new"))
    enq, = env.events("enqueue")
    am, = env.events("amended")
    start = env.d.log.last("start", item="code/001_new")
    assert enq.id < am.id < start.id and am.detail["state"] == "queued"
    assert (env.repo / "d.txt").is_file()


def test_an_amendment_for_an_unknown_item_waits(env):
    p = amend_file(env, "005_later", "hello")
    env.d.tick()
    env.d.tick()
    warn = [e for e in env.events("warning") if "left in place" in e.detail.get("note", "")]
    assert len(warn) == 1 and p.exists() and not env.events("amended")
    env.queue("005_later", "FAKE report done")
    env.tick_until(lambda: env.finished("code/005_later"))
    assert env.events("amended")[0].item == "code/005_later" and not p.exists()


@pytest.mark.slow
def test_a_staged_amendment_keeps_the_stage(env):
    env.queue("001_long", env.hold())
    env.queue("002_next", "FAKE commit b.txt b")
    env.tick_until(lambda: env.events("staged"))
    amend_file(env, "002_next", "FAKE commit c.txt c")
    env.tick_until(lambda: env.events("amended"))
    [am] = env.events("amended")
    assert am.detail["state"] == "staged" and "stage is kept" in am.detail["delivery"]
    env.release()
    env.tick_until(lambda: env.finished("code/002_next"))
    assert not env.events("edited")
    assert not any("stage discarded" in e.detail.get("note", "") for e in env.events("warning"))
    assert env.d.log.last("start", item="code/002_next").detail["resumed"]
    prompt = reports(env, "002_next", ".prompt.md")
    assert "The prompt is the one you read while staging." in prompt
    assert "overrides that plan" in prompt and f"## Amendment #{am.id}" in prompt
    assert "STAGED MEMORY: recalled the staged prompt" in reports(env, "002_next")
    assert (env.repo / "c.txt").is_file() and (env.repo / "b.txt").is_file()
    [got] = env.events("delivered")
    assert got.detail["via"] == "resume" and "while staged" in reports(env, "002_next", ".amend.md")


def test_a_running_item_gets_the_whole_amendment(env, capsys, tmp_path):
    env.queue("001_busy", "FAKE until-message 30")
    env.tick_until(lambda: env.events("start"))
    src = tmp_path / "amend.txt"
    src.write_text("first line\nsecond line # not a comment\n", encoding="utf-8")
    assert cli.main(["amend", "001", str(src), "--control", str(env.control)]) == 0
    assert "after its next tool call" in capsys.readouterr().out
    env.tick_until(lambda: env.finished("code/001_busy"))
    [am] = env.events("amended")
    assert (am.detail["state"], am.detail["by"], am.detail["text"]) == (
        "running", "cli", "first line\nsecond line # not a comment")
    [got] = env.events("delivered")
    assert (got.detail["amend"], got.detail["via"]) == (am.id, "hook")
    text = reports(env, "001_busy")
    assert f"CONTEXT: AMENDMENT #{am.id} from Matt via cli, an addition to the prompt" in text
    assert "first line\nsecond line # not a comment" in text
    assert "via hook" in reports(env, "001_busy", ".amend.md")
    assert "amendment #" in model.activity_text(got)


def test_finished_items_refuse_amendments(env, capsys):
    env.queue("001_done", "FAKE report done")
    env.tick_until(lambda: env.finished("code/001_done"))
    p = amend_file(env, "001_done", "one more thing")
    env.tick_until(lambda: env.events("amend_refused"))
    env.d.tick()
    [ref] = env.events("amend_refused")
    assert ref.detail["state"] == "finished" and "a new item" in ref.detail["reason"]
    assert p.exists() and not env.events("amended")
    assert "refused" in describe(ref) and any("refused" in t for t, _, _ in env.toasts)
    # A restarted daemon does not refuse it again; a changed file is refused anew.
    env.d.log.close()
    env.d = env.new_daemon()
    env.d.tick()
    assert len(env.events("amend_refused")) == 1
    assert cli.main(["amend", "001", "again", "--control", str(env.control)]) == 1
    assert "has finished" in capsys.readouterr().err
    assert cli.main(["amend", "009", "x", "--control", str(env.control)]) == 1
