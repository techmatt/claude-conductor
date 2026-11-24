"""Prompt 144: a parked edit can be withdrawn or replaced (signal and MCP); `retry: … model:`
and a resume item's `model:` move a session to another model; a usage limit with a reset
time waits for the reset (or reds at once with the way round); `edit_state`'s empty `new`
on a piece is refused; and the preamble's two lines from aicomplete 300."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from conductor import daemon_api, executor, items, models, parked, planner_mcp  # noqa: E402
from conductor.daemon_api import retry_parts  # noqa: E402
from conductor.parked import signal_parts as park_parts  # noqa: E402
from conductor.util import read_text  # noqa: E402
from test_prompt_143 import call  # noqa: E402
from test_planner_mcp import alive  # noqa: E402

OPUS, FABLE = models.MODELS["opus"], models.MODELS["fable"]
SEVEN_TEN = "[rules] Rounds aim: 7-10 rounds a game; make design_check.json's band say 7-10."
BOTNET = "[rules] Botnet Conscripts' troop garrisons by the rule."


@pytest.fixture
def setting(tmp_path, monkeypatch):
    monkeypatch.setenv(models.ENV, str(tmp_path / "model.json"))


def write_parked(env, *lines: str) -> None:
    parked.prepare(env.control)
    parked.path(env.control, "code").write_text("".join(f"- {ln}\n" for ln in lines), encoding="utf-8")


def receipt(env, name: str) -> str:
    return read_text(env.control / "receipts" / "signals" / f"{name}.md")


def send(env, name: str, text: str) -> str:
    env.signal(f"{name}.signal", text)
    env.tick_until(lambda: (env.control / "receipts" / "signals" / f"{name}.md").is_file())
    return receipt(env, name)


def test_a_signal_removes_or_replaces_one_waiting_line_and_lists_what_waits(env):
    write_parked(env, SEVEN_TEN, BOTNET)
    got = send(env, "p1", "park: code remove 7-10 rounds")
    assert 'outcome: "accepted"' in got and "removed `[rules] Rounds aim" in got, got
    assert "1 parked edit(s) now wait for code: P1. [rules] Botnet" in got, got
    assert [e.text for e in parked.waiting(env.control, "code")] == [BOTNET]
    got = send(env, "p2", "park: code replace garrisons => [rules] Botnet Conscripts garrison in a city # not a comment")
    assert "replaced `[rules] Botnet" in got and "garrison in a city # not a comment" in got, got
    got = send(env, "p3", "park: code remove [rules]")      # two lines would hold it? one now: it goes
    assert "0 parked edit(s) now wait" in got, got
    assert parked.waiting(env.control, "code") == []
    [ev, *_] = env.events("parked_edit")
    assert ev.detail["op"] == "remove" and ev.detail["was"] == SEVEN_TEN and ev.repo == "code"


def test_a_piece_in_no_line_or_several_is_refused_naming_them(env):
    write_parked(env, SEVEN_TEN, BOTNET)
    got = send(env, "p1", "park: code remove [rules]")
    assert 'outcome: "refused"' in got and "2 waiting lines hold '[rules]'" in got, got
    got = send(env, "p2", "park: code remove sonnet")
    assert "no waiting line holds 'sonnet'" in got and "waiting: [rules] Rounds aim" in got, got
    assert len(parked.waiting(env.control, "code")) == 2       # nothing changed
    # A line already handed out is named with its item: only a message reaches it now.
    parked.path(env.control, "code", done=True).write_text(
        "- fix the typo — 004_x (2026-11-20T10:00:00-08:00)\n", encoding="utf-8")
    got = send(env, "p3", "park: code remove the typo")
    assert "already handed out (fix the typo — 004_x" in got, got


def test_park_parts_and_retry_parts():
    assert park_parts("code [rules] a line") == ("code", "add", "[rules] a line", None)
    assert park_parts("code remove 7-10") == ("code", "remove", "7-10", None)
    assert park_parts("code replace old bit => new line") == ("code", "replace", "old bit", "new line")
    with pytest.raises(ValueError):
        park_parts("code replace no arrow")
    assert retry_parts("code/297_x") == ("code/297_x", None)
    assert retry_parts("code/297_x model: Opus") == ("code/297_x", "opus")
    with pytest.raises(ValueError):
        retry_parts("code/297_x model: sonnet")


@pytest.mark.slow
def test_mcp_park_removes_and_replaces_through_the_daemon(env):
    write_parked(env, SEVEN_TEN, BOTNET)
    alive(env)
    text, err = call(env, "park", {"repo": "code", "op": "remove", "match": "7-10 rounds"})
    assert not err and text.startswith("accepted: removed `[rules] Rounds aim"), text
    assert "now wait for code: P1. [rules] Botnet" in text, text
    text, err = call(env, "park", {"repo": "code", "op": "replace", "match": "Botnet", "line": "[rules] Botnet v2"})
    assert not err and "with `[rules] Botnet v2`" in text, text
    text, err = call(env, "park", {"repo": "code", "op": "remove", "match": "nothing like it"})
    assert text.startswith("REFUSED: park: no waiting line holds"), text
    text, err = call(env, "park", {"repo": "code", "line": "rename a thing"})
    assert not err and "waiting: P1. [rules] Botnet v2 | P2. rename a thing" in text, text


def test_retry_with_a_model_resumes_on_it_and_the_record_and_finish_say_so(env, setting):
    models.set_model("fable", "test")
    env.queue("001_limit", "FAKE commit a.txt one\nFAKE usage-limit fable 11am (America/Los_Angeles)\nFAKE model",
              max_hours="0.01")
    env.tick_until(lambda: env.finished("code/001_limit"))
    fin = env.finished("code/001_limit")
    [reason] = [r for r in fin.detail["reasons"] if r.startswith("API error")]
    # The reset is past its deadline: red at once, naming the reset and the way round.
    assert "the limit resets at " in reason and "11:00am, past its deadline" in reason and "`retry: code/001_limit model: opus`" in reason, reason
    assert fin.detail["model"] == FABLE and not [e for e in env.events("api_retry") if e.detail["state"] == "waiting"]
    env.signal("r.md", "retry: code/001_limit model: opus\nby: Matt")
    env.tick_until(lambda: env.finished("code/001_limit").detail["status"] == "done")
    fin = env.finished("code/001_limit")
    assert fin.detail["model"] == OPUS and fin.detail["models"] == [FABLE, OPUS] and fin.detail.get("merged")
    [ev] = [e for e in env.events("api_retry") if e.detail["state"] == "retry"]
    assert ev.detail["model"] == OPUS and "on Opus (claude-opus" in ev.detail["note"]
    assert "now on Opus" in read_text(env.control / ev.paths[0])
    report = read_text(env.control / "reports" / "code__001_limit.md")
    assert f"model: {OPUS}" in report       # and it finished: on Fable the fake hits the limit again
    assert models.current() == "fable"                      # the machine-wide setting is untouched


def test_a_usage_limit_waits_for_its_reset_when_that_fits(env, setting, monkeypatch):
    models.set_model("fable", "test")
    soon = datetime.now().astimezone() + timedelta(minutes=30)
    hour = soon.hour % 12 or 12
    env.queue("001_wait", f"FAKE usage-limit fable {hour}:{soon.minute:02d}{'am' if soon.hour < 12 else 'pm'}",
              max_hours="4")
    env.tick_until(lambda: env.events("api_retry"))
    [ev] = env.events("api_retry")
    assert ev.detail["state"] == "waiting" and ev.detail["usage_limit"] == "fable"
    assert "resumes at" in ev.detail["note"] and "after the reset" in ev.detail["note"]
    at = datetime.fromisoformat(ev.detail["at"])
    assert timedelta(minutes=32) <= at - datetime.now().astimezone() <= timedelta(minutes=34)
    run = env.d.runs["code/001_wait"]
    assert not run.rec.get("api_tries") and run.rec["api_wait"]["usage"]     # the 2/10/30 tries untouched
    env.d.write_inbox(force=True)
    assert "**waiting on the API** (You've hit your Fable limit" in read_text(env.control / "inbox.md")
    assert "the usage limit's reset at" in read_text(env.control / "inbox.md")


def test_usage_limit_reading():
    at = datetime(2026, 11, 20, 9, 30).astimezone()
    assert daemon_api.usage_limit("You've hit your Fable limit · resets 11am (America/Los_Angeles)", at) == \
        ("fable", at.replace(hour=11, minute=0))
    assert daemon_api.usage_limit("hit your Opus limit · resets 8:15am", at)[1] == \
        at.replace(hour=8, minute=15) + timedelta(days=1)            # past today: tomorrow's
    assert daemon_api.usage_limit("hit your Opus limit · resets Nov 22, 3pm", at)[1] == \
        at.replace(day=22, hour=15, minute=0)
    assert daemon_api.usage_limit("API Error: 529 Overloaded", at) is None
    assert daemon_api.other_models("fable") == ["opus"]


@pytest.mark.slow
def test_a_resume_item_takes_its_own_model(env, setting):
    env.queue("001_orig", "FAKE commit a.txt one")
    env.tick_until(lambda: env.finished("code/001_orig"))
    assert env.finished("code/001_orig").detail["model"] == OPUS
    env.queue("002_ask", "Why?\nFAKE model", resume="code/001_orig", model="fable")
    env.tick_until(lambda: env.finished("code/002_ask"))
    assert env.finished("code/002_ask").detail["model"] == FABLE
    bad = items.parse(env.queue("003_new", "work", model="fable"), "code")
    assert any("`model` is for a resume item only" in e for e in bad.errors)


def test_an_empty_new_on_a_piece_is_refused_and_a_piece_edit_says_what_it_did():
    text = "# State\n\n- The executor memory note `final-x` (under `~/.claude`, see 143): delete its last line.\n"
    with pytest.raises(planner_mcp.Refused) as exc:
        planner_mcp.widen(text, "- The executor memory note `final-x`", "")
    assert "would leave '(under" in str(exc.value) and "send it whole as `old`" in str(exc.value)
    whole = "- The executor memory note `final-x` (under `~/.claude`, see 143): delete its last line."
    assert planner_mcp.widen(text, whole, "") == (whole + "\n", "")
    old, new = planner_mcp.widen(text, "see 143", "see 144")
    assert old == whole + "\n" and new == whole.replace("143", "144") + "\n"
    desc = next(t for t in planner_mcp.TOOLS if t["name"] == "edit_state")["description"]
    assert "an empty `new` needs `old` to be whole lines" in desc


def test_the_preamble_lines_from_aicomplete_300():
    assert "Confirm afresh a\n`design: true` default changing the shipped build before regen and docs" in executor.PREAMBLE
    for text in (executor.REVIEW, executor.REVIEW_ULTRA):
        assert "a self-check of the" in text and "fresh seeds" in text
    assert "fresh seeds" not in executor.CALIBRATE
