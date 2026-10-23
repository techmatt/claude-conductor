"""Messages that reach an item blocked on its own question, `pause:` / `resume:`, and the
watch's repairs (tell, answer) at any autopilot setting."""

import json
import time
from datetime import timedelta

import pytest

from conductor import daemon_watch, questions, state
from conductor.app import model
from conductor.util import now, parse_stamp, read_text, stamp


def ask_line(**args) -> str:
    return "FAKE ask " + json.dumps(args)


def receipt(env, name: str) -> str:
    return read_text(env.control / "receipts" / "signals" / f"{name.removesuffix('.signal')}.md")


def report(env, name: str) -> str:
    return read_text(env.control / "reports" / f"code__{name}.md")


@pytest.mark.slow
def test_a_tell_to_an_item_blocked_on_its_question_answers_it(env):
    env.queue("001_ask", ask_line(question="Pausing: answer resume", block=True) + "\nFAKE report done")
    env.tick_until(lambda: env.events("ask"))
    env.signal("planner-1.signal", "by: planner\ntell: 001 Carry on now.")
    env.tick_until(lambda: env.finished("code/001_ask"))
    [tell] = env.events("tell")
    assert tell.detail["answers"] == "code__001_ask__1" and not env.events("delivered")
    assert 'result: "delivered as the answer to code__001_ask__1"' in receipt(env, "planner-1")
    [ans] = env.events("answer")
    assert ans.detail["by"] == "planner"
    assert "Answer to code__001_ask__1:\nMessage from the planner: Carry on now." in report(env, "001_ask")


@pytest.mark.slow
def test_pause_and_resume_stop_the_queue_and_the_clock(env):
    env.queue("001_work", 'FAKE until-message 30\nFAKE wait {"minutes": 1}\nFAKE report done')
    env.tick_until(lambda: env.events("start"))
    env.signal("cli-1.signal", "by: cli\npause: code")
    env.tick_until(lambda: env.events("pause"))
    [pause] = env.events("pause")
    assert pause.repo == "code" and pause.detail["items"] == ["code/001_work"]
    [tell] = env.events("tell")
    assert tell.detail["pause"] and "call `wait`" in tell.detail["text"]
    assert f"message #{tell.id} to code/001_work: delivered after its next tool call" in receipt(env, "cli-1")
    run = env.d.runs["code/001_work"]
    assert run.rec["paused_at"]
    # Nothing new starts, not even a readonly item that would start beside it.
    env.queue("002_look", "FAKE report done", readonly="true")
    env.tick_until(lambda: env.events("delivered"))
    for _ in range(3):
        env.d.tick()
    assert [e.item for e in env.events("start")] == ["code/001_work"]
    view = state.scan(env.d.cfg, env.d.log)
    assert state.decide(env.d.cfg, view)["code"].reason.startswith("paused")
    # Its clock stops: past its deadline while paused, no kill.
    run.rec["deadline"] = stamp(now() - timedelta(seconds=1))
    for _ in range(2):
        env.d.tick()
    assert not env.events("kill")
    env.d.write_inbox(force=True)
    assert "**PAUSED** since" in read_text(env.control / "inbox.md")
    snap = model.Project(env.control).snapshot()
    assert list(snap.paused) == ["code"] and snap.repo_names == ["code"]
    env.signal("cli-2.signal", "by: cli\npause: code")             # once is enough
    env.tick_until(lambda: (env.control / "signals" / "cli-2.signal.bad").exists())
    run.rec["deadline"] = before = stamp(now() + timedelta(minutes=10))
    time.sleep(1)
    env.signal("cli-3.signal", "by: cli\nresume: code")
    env.tick_until(lambda: env.finished("code/001_work") and env.finished("code/002_look"))
    [resume] = env.events("resume")
    assert resume.detail["paused_seconds"]["code/001_work"] >= 1
    assert parse_stamp(run.rec["deadline"]) > parse_stamp(before) and "paused_at" not in run.rec
    text = report(env, "001_work")
    assert "From conductor: RESUME" in text and "No message yet" not in text
    assert env.finished("code/001_work").detail["status"] == "done"
    env.signal("cli-4.signal", "by: cli\nresume: code")            # not paused
    env.tick_until(lambda: (env.control / "signals" / "cli-4.signal.bad").exists())


@pytest.mark.slow
def test_the_watch_repairs_with_autopilot_off_but_never_draws(env, monkeypatch):
    for k, v in {"watch_check_minutes": 10, "watch_blocked_minutes": 0}.items():
        setattr(env.d.cfg, k, v)
    env.signal("cli-1.signal", "by: cli\nautopilot: off")
    env.queue("001_ask", ask_line(question="Which store?", block=True) + "\nFAKE report done")
    env.tick_until(lambda: env.events("ask"))
    (env.control / "backlog" / "code" / "later.md").write_text("---\nrepo: code\n---\nFAKE report done\n",
                                                               encoding="utf-8")
    monkeypatch.setattr(daemon_watch, "WATCH_BODY", (
        "{reason}\nFAKE note ## Actions\\n- answer: code__001_ask__1 sqlite (source: Matt's message #7)"
        "\\n- tell: code/001_ask the answer came from Matt's ruling\\n- draw: code\\n"
        "- answer: code__001_ask__1 postgres\n"))
    env.signal("cli-2.signal", "by: cli\nwatch: now")
    env.tick_until(lambda: env.finished("code/W1_watch") and env.finished("code/001_ask"))
    [wa] = env.events("watch_answer")
    assert (wa.detail["question"], wa.detail["source"], wa.detail["text"]) == (
        "code__001_ask__1", "Matt's message #7", "sqlite")
    assert env.events("answer")[0].detail["by"] == "watch"
    assert "Answer to code__001_ask__1:\nsqlite\n\n(Answered by conductor's watch" in report(env, "001_ask")
    [tell] = env.events("tell")
    assert tell.detail["by"] == "watch" and "answers" not in tell.detail       # the question had its answer
    notes = [w.detail["note"] for w in env.events("warning") if w.detail.get("action")]
    assert any("`draw: code` not carried out: autopilot is off: recommended only" in n for n in notes)
    assert any("`answer: code__001_ask__1 postgres` not carried out: an answer is" in n for n in notes)
    assert not env.events("backlog_draw")


@pytest.mark.slow
def test_a_blocking_question_unanswered_launches_a_watch(env):
    for k, v in {"watch_check_minutes": 0.0001, "watch_quiet_minutes": 0, "watch_every_minutes": 0,
                 "watch_blocked_minutes": 0.0001}.items():
        setattr(env.d.cfg, k, v)
    env.queue("001_ask", ask_line(question="Which store?", block=True) + "\nFAKE report done")
    env.tick_until(lambda: env.events("watch"))
    [flag] = [e for e in env.events("watch_flag") if e.detail["flag"] == "blocked"]
    assert flag.detail["key"] == "blocked:code__001_ask__1" and "watch" not in flag.detail
    [watch] = env.events("watch")
    assert "blocked on question `code__001_ask__1`" in watch.detail["reason"]
    [q] = questions.scan(env.control)
    q.answer_path.write_text("sqlite\n", encoding="utf-8")
    env.tick_until(lambda: env.finished("code/001_ask") and env.finished("code/W1_watch"))
