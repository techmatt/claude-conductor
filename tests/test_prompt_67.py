"""Prompt 67's small rules: design questions, staged plans' questions, `wait_for`'s failure
line and worker, a landing's subject, the self-audit's record and the app's code check."""

import subprocess
import sys
import threading

from conductor import ask_server, inbox, questions, resume
from conductor.app import model
from conductor.executor import RunPaths
from conftest import git


def server(env, item, tmp_path=None) -> ask_server.Server:
    return ask_server.Server(env.control, item, 10, None, out=None, workdir=tmp_path)


def wait(tmp_path, env, **args) -> str:
    return server(env, "code/009_x", tmp_path).wait_for({"every": 0.05, **args}, threading.Event())[0]


def ask(env, item, **args):
    return server(env, item).ask({"question": "Keep the wild card?", **args}, threading.Event())


def test_a_design_question_returns_at_once_and_a_late_answer_arrives_as_a_message(env):
    env.queue("001_hold", env.hold())
    env.tick_until(lambda: env.events("start"))
    assert ask(env, "code/001_hold", design=True)[1] is True            # no default
    assert ask(env, "code/001_hold", design=True, default="keep", block=True)[1] is True
    text, is_error = ask(env, "code/001_hold", design=True, default="keep it")
    assert not is_error and "Carry on with your default now: keep it" in text
    [q] = [q for q in questions.scan(env.control) if q.is_open]
    assert q.design and q.deadline is None and q.default == "keep it"
    env.tick_until(lambda: env.events("ask"))
    assert env.events("ask")[0].detail["design"] is True
    assert "**design, held for Matt**; default applied: keep it" in (env.control / "inbox.md").read_text(encoding="utf-8")
    snap = model.Project(env.control).snapshot()
    assert snap.state != "asking" and not env.d.blocked

    questions.write_answer(q, "drop it", by="app")
    env.tick_until(lambda: env.events("answer"))
    assert env.events("answer")[0].detail["by"] == "app"
    [tell] = [e for e in env.events("tell") if e.detail.get("answers") == q.id]
    assert "drop it" in tell.detail["text"] and tell.detail["by"] == "app" and "not an amendment" in tell.detail["text"]
    assert not env.events("late")


def test_a_design_question_outlives_its_item_and_its_answer_lands_in_the_log(env):
    env.queue("001_hold", env.hold())
    env.tick_until(lambda: env.events("start"))
    ask(env, "code/001_hold", design=True, default="keep it")
    [q] = questions.scan(env.control)
    env.d._close_questions("code/001_hold")
    assert questions.load(q.path).is_open
    env.d.runs.clear()
    questions.write_answer(q, "drop it")
    env.tick_until(lambda: env.events("answer"))
    assert not [e for e in env.events("tell") if e.detail.get("answers")]
    env.release()               # runs.clear() hid it from teardown: it would hold its folder 180 s (103)


def test_plan_questions():
    plan = ("Plan: do it.\n\n**Questions:**\n- 96 games or 96 a cell?\n  a second line\n1. Which seed?\n\n"
            "Outline:\n- one\n- two\n")
    assert questions.plan_questions(plan) == ["96 games or 96 a cell? a second line", "Which seed?"]
    assert questions.plan_questions("Questions: none\n\nOutline:\n- one") == []
    assert questions.plan_questions("Questions: which seed?\n") == ["which seed?"]
    assert questions.plan_questions("Open questions about the gate were settled.\n- x") == []


def test_a_staged_plans_questions_reach_the_inbox_until_it_launches(env):
    env.queue("001_long", env.hold())
    env.tick_until(lambda: env.events("start"))
    env.queue("002_next", "FAKE stage-question 96 games or 96 a cell?\nFAKE report done\n", depends_on="[001_long]")
    env.tick_until(lambda: env.events("staged"))
    [ev] = env.events("staged")
    assert ev.detail["questions"] == ["96 games or 96 a cell?"]
    env.tick_until(lambda: "## Staged plans' questions" in (env.control / "inbox.md").read_text(encoding="utf-8"))
    text = (env.control / "inbox.md").read_text(encoding="utf-8")
    assert "> 96 games or 96 a cell?" in text and "asks 1 question" in inbox.describe(ev)


def test_wait_for_stops_on_a_failure_line_and_a_dead_worker(env, tmp_path):
    log = tmp_path / "run.log"
    log.write_text("step 1\nthread 'main' panicked at x\nALL PASS\n", encoding="utf-8")
    text = wait(tmp_path, env, log="run.log", line="ALL PASS")
    assert "has a failure line: thread 'main' panicked" in text and "narrower `fail`" in text
    assert "has the line: ALL PASS" in wait(tmp_path, env, log="run.log", line="ALL PASS", fail="")
    log.write_text("Error: bad\n", encoding="utf-8")
    assert "has a failure line" in wait(tmp_path, env, log="run.log", line="never", minutes=0.01)
    log.write_text("0 errors, done\n", encoding="utf-8")                  # the wanted line wins on one line
    assert "has the line" in wait(tmp_path, env, log="run.log", line="done")
    assert "not a regular" in wait(tmp_path, env, log="run.log", line="x", fail="(")

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.3)"])
    log.write_text("step 1\n", encoding="utf-8")
    text = wait(tmp_path, env, log="run.log", line="never", pid=child.pid, minutes=0.2)
    child.wait()
    assert f"pid {child.pid} has exited, and no line matches" in text and "last written" in text
    assert "has exited, and " in wait(tmp_path, env, path="nope", pid=child.pid, minutes=0.2)
    assert "give one of" in wait(tmp_path, env, path="a", log="b", line="x")


def test_a_landing_takes_its_final_commits_subject(env):
    env.queue("001_one", "FAKE commit a.txt a\nFAKE report done\n")
    env.tick_until(lambda: env.finished("code/001_one"))
    assert git(env.repo, "log", "-1", "--format=%s", "main").strip() == "code/001_one: fake: a.txt"


def test_a_self_audit_reads_the_stage_prompt_and_amendments(env, tmp_path):
    p = RunPaths.for_name(env.d.cfg, "code", "001_x")
    for f in (p.stage_prompt, p.amend):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x\n", encoding="utf-8")
    assert {p.stage_prompt, p.amend} <= set(resume.readable(env.d.cfg, "code/001_x", tmp_path / "events.md"))


def test_the_app_says_when_the_daemon_runs_other_code(env, monkeypatch):
    project = model.Project(env.control)
    env.d.log.append("daemon_start", pid=1, control="x", code="new")
    monkeypatch.setattr(model, "daemon_info", lambda cfg: model.DaemonInfo(True, 1, "2026-01-01T00:00:00-07:00", "2026-01-01T00:00:00-07:00"))
    monkeypatch.setattr(model, "app_code", lambda: "old")
    assert project.snapshot().code_differs
    monkeypatch.setattr(model, "app_code", lambda: "new")
    assert not project.snapshot().code_differs
