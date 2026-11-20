"""Parked edits: `parked/<repo>.md` lines handed to the next serial item at launch, the
report's answers, and what goes back."""

import pytest

from conductor import executor, items, parked, state
from conductor.util import read_text

from conftest import transcript


def write_parked(env, *lines: str) -> None:
    parked.prepare(env.control)
    parked.path(env.control, "code").write_text("".join(f"- {ln}\n" for ln in lines), encoding="utf-8")


def test_who_carries_what(env):
    write_parked(env, "rename hardware to Chips", "[rules] node-cut costs 2")
    edits = parked.waiting(env.control, "code")
    assert [e.rules for e in edits] == [False, True]
    serial = items.parse(env.queue("001_a", "do it"), "code")
    rules = items.parse(env.queue("002_r", "bump the rules", rules="true"), "code")
    side = items.parse(env.queue("003_s", "art", alongside="card art only"), "code")
    tpl = items.parse(env.queue("004_t", "", template="general_cleanup"), "code")
    # 143: a `[rules]` edit rides any serial item, `rules: true` or not.
    assert [e.text for e in parked.eligible(serial, edits)] == ["rename hardware to Chips", "[rules] node-cut costs 2"]
    assert len(parked.eligible(rules, edits)) == 2 and rules.meta()["rules"] is True
    assert parked.eligible(side, edits) == [] and parked.eligible(tpl, edits) == []
    # The receipt's estimate: the first serial item takes them; one behind it, or one not
    # serial, says it carries none while edits wait, and why.
    view = state.scan(env.cfg, env.d.log)
    first = state.parked_estimate(env.cfg, view, serial)
    assert first.startswith("carries 2 parked edit(s) as things stand") and "node-cut" in first
    assert state.parked_estimate(env.cfg, view, rules) == \
        "carries none of the 2 parked edit(s) waiting: 001_a ahead of it takes them"
    assert state.parked_estimate(env.cfg, view, side).endswith("waiting: not a serial item")


def test_answers_and_settle():
    edits = [{"n": 1, "text": "a"}, {"n": 2, "text": "b"}, {"n": 3, "text": "c"}]
    report = "status: done\n\nparked P1: done\n**parked P2**: skipped (conflicts with the new layout)\n"
    assert parked.answers(report) == {1: ("done", ""), 2: ("skipped", "(conflicts with the new layout)")}
    done, back = parked.settle(edits, report, landed=True)
    assert [e["n"] for e in done] == [1] and [e["n"] for e in back] == [2, 3]     # unanswered goes back too
    assert parked.settle(edits, report, landed=False) == ([], edits)              # red: nothing landed


def test_a_workflow_retro_at_the_floor_names_the_most_friction(env):
    from conductor import session
    log = env.d.log
    (env.control / "done" / "code").mkdir(parents=True, exist_ok=True)

    def fin(name: str, **detail):
        (env.control / "done" / "code" / f"{name}.md").write_text("x\n", encoding="utf-8")
        return log.append("finish", item=f"code/{name}", repo="code", status=detail.pop("status", "done"),
                          session_id=transcript("s"), **detail)

    fin("010_audit", template="self_audit", resume_of="code/009_x")
    for n in range(11, 22):
        fin(f"0{n}_w")
    line = session.workflow_retro(log, state.scan(env.d.cfg, log), "code")
    assert line.endswith(" · 11 non-template item(s) finished since") and "**due**" not in line
    log.append("tell", item="code/013_w", by="watch", text="you seem stuck")
    log.append("default", item="code/013_w", question="code__013_w__1")
    fin("022_w", friction="stale README command")
    line = session.workflow_retro(log, state.scan(env.d.cfg, log), "code")
    assert "12 non-template item(s) finished since · **due**: post-audit 013_w (1 watch tell, 1 timed-out question)" in line


@pytest.mark.slow
def test_a_serial_item_carries_them_and_the_skipped_one_goes_back(env):
    write_parked(env, "rename hardware to Chips", "drop the old splash", "[rules] node-cut costs 2")
    env.queue("001_a", "FAKE note parked P1: done\nFAKE note parked P2: skipped (no splash left)\nFAKE report done")
    env.tick_until(lambda: env.finished("code/001_a"))
    prompt = read_text(executor.RunPaths.for_name(env.d.cfg, "code", "001_a").prompt)
    assert "## Parked edits (also do these)" in prompt
    assert "- P1. rename hardware to Chips\n- P2. drop the old splash\n- P3. [rules] node-cut costs 2" in prompt
    [start] = env.events("start")
    assert start.detail["parked"] == ["rename hardware to Chips", "drop the old splash", "[rules] node-cut costs 2"]
    fin = env.finished("code/001_a")
    assert fin.detail["parked_done"] == ["rename hardware to Chips"]
    assert fin.detail["parked_back"] == ["drop the old splash", "[rules] node-cut costs 2"]
    assert [e.text for e in parked.waiting(env.control, "code")] == ["drop the old splash", "[rules] node-cut costs 2"]
    done = read_text(parked.path(env.control, "code", done=True))
    assert "- rename hardware to Chips — 001_a (" in done and "drop the old splash — 001_a" in done
    assert (env.control / "parked" / ".gitignore").read_text(encoding="utf-8") == "*\n"
    env.d.write_inbox(force=True)
    inbox = read_text(env.control / "inbox.md")
    assert "2 parked edit(s) waiting (1 `[rules]`)" in inbox and "parked edits: 1 done, 2 back" in inbox


@pytest.mark.slow
def test_an_alongside_item_carries_none(env):
    write_parked(env, "rename hardware to Chips")
    env.queue("001_side", "FAKE report done", alongside="card art only")
    env.tick_until(lambda: env.finished("code/001_side"))
    assert "Parked edits" not in read_text(executor.RunPaths.for_name(env.d.cfg, "code", "001_side").prompt)
    assert "parked" not in env.events("start")[0].detail
    assert len(parked.waiting(env.control, "code")) == 1
