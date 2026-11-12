"""Prompt 107: workflow retros queue themselves. A finish that makes one due (partial, red,
overrun, the floor) has conductor write the self-audit **post-audit X** would, with no planner
step; the digest shows it queued or ran, never due, unless `auto_retros = false`."""

import re

import pytest

from conductor import session, state
from conductor.util import read_text

from conftest import git, transcript


def auto(env) -> None:
    toml = env.control / "conductor.toml"
    toml.write_text(read_text(toml).replace("auto_retros = false", "auto_retros = true"), encoding="utf-8", newline="\n")
    git(env.control, "commit", "-qam", "auto retros")
    env.d.log.close()
    env.d = env.new_daemon()


def fin(env, name: str, **detail):
    done = env.control / "done" / "code"
    done.mkdir(parents=True, exist_ok=True)
    (done / f"{name}.md").write_text("x\n", encoding="utf-8")
    detail.setdefault("session_id", transcript(f"s-{name}"))
    return env.d.log.append("finish", item=f"code/{name}", repo="code", status=detail.pop("status", "done"), **detail)


def retro_line(env) -> str:
    return session.workflow_retro(env.d.log, state.scan(env.d.cfg, env.d.log), "code", env.d.cfg.auto_retros)


def queued(env) -> list[str]:
    return [e.item for e in env.events("retro")]


def test_it_is_on_by_default_and_a_project_turns_it_off(env):
    assert env.d.cfg.auto_retros is False                   # the tests' toml
    fin(env, "001_a", status="partial")
    env.d.queue_retros()
    assert queued(env) == [] and "**due**: post-audit 001_a (partial)" in retro_line(env)
    auto(env)
    assert env.d.cfg.auto_retros is True


def test_a_partial_finish_queues_its_self_audit_once(env):
    auto(env)
    fin(env, "001_a")
    fin(env, "002_b", status="partial", friction="stale doc")
    env.d.queue_retros()
    assert queued(env) == ["code/003_post_audit_002"]
    [ev] = env.events("retro")
    assert ev.detail["of"] == "code/002_b" and ev.detail["why"] == ["partial", "1 friction"]
    text = read_text(env.control / "queue" / "code" / "003_post_audit_002.md")
    assert "resume: code/002_b\ntemplate: self_audit" in text
    assert "queued: 003_post_audit_002" in retro_line(env) and "due" not in retro_line(env)
    fin(env, "004_c", status="red")                          # while one waits: the next, after it
    env.d.queue_retros()
    assert queued(env) == ["code/003_post_audit_002"]
    assert "next: post-audit 004_c (red), queued once 003_post_audit_002 ends" in retro_line(env)
    (env.control / "queue" / "code" / "003_post_audit_002.md").unlink()
    fin(env, "003_post_audit_002", template="self_audit", resume_of="code/002_b")
    env.d.queue_retros()
    assert queued(env) == ["code/003_post_audit_002", "code/005_post_audit_004"]     # 004 finished after 003 queued


def test_no_retro_of_a_template_watch_or_resume_item_or_one_with_no_session(env):
    auto(env)
    fin(env, "001_hunt", status="red", template="bug_hunt")
    fin(env, "W1_watch", status="partial", template="watch")
    fin(env, "002_ask", status="partial", resume_of="code/001_hunt")
    fin(env, "003_x", status="red", session_id=None, reasons=["launch failed: no claude"])
    env.d.queue_retros()
    assert queued(env) == [] and "due" not in retro_line(env) and "next" not in retro_line(env)


def test_no_retro_of_an_item_whose_transcript_is_gone(env):
    """The sandbox's first daemon after 107 queued a retro of a 13-day-old item with no
    transcript, which went red at launch (cleanup 2026-11-12)."""
    auto(env)
    fin(env, "001_old", status="red", session_id="pruned")
    env.d.queue_retros()
    assert queued(env) == [] and "due" not in retro_line(env)
    fin(env, "002_new", status="partial")
    env.d.queue_retros()
    assert queued(env) == ["code/003_post_audit_002"]


def test_never_a_second_for_an_item_a_post_audit_covers(env):
    auto(env)
    fin(env, "001_a", status="partial")
    env.queue("002_mine", "", resume="code/001_a", template="self_audit")     # Matt's own, by hand
    env.d.queue_retros()
    assert queued(env) == []


def test_nothing_queued_while_the_repo_is_paused(env):
    auto(env)
    env.d.log.append("pause", repo="code")
    fin(env, "001_a", status="partial")
    env.d.queue_retros()
    assert queued(env) == [] and "next: post-audit 001_a (partial), queued when `code` resumes" in retro_line(env)
    env.d.log.append("resume", repo="code")
    env.d.queue_retros()
    assert queued(env) == ["code/002_post_audit_001"]


@pytest.mark.slow
def test_a_partial_run_queues_its_retro_and_the_digest_shows_it_not_due(env):
    """The done-when: 192's shape (a real run ending partial) queues its self-audit with no
    signal or file from the planner, and the next `session:` digest shows it, not **due**."""
    auto(env)
    env.queue("001_orig", "REMEMBER chose the blue door\nFAKE report partial")
    env.tick_until(lambda: queued(env))
    assert env.finished("code/001_orig").detail["status"] == "partial"
    assert queued(env) == ["code/002_post_audit_001"]
    env.signal("planner-1.signal", "by: planner\nsession: start")
    env.tick_until(lambda: env.events("checkpoint"))
    line = next(ln for ln in read_text(env.control / "session.md").splitlines() if ln.startswith("- workflow retro"))
    assert re.search(r"(queued|running): 002_post_audit_001", line) and "due" not in line
    env.tick_until(lambda: env.finished("code/002_post_audit_001"))
    fin_ev = env.finished("code/002_post_audit_001")
    assert fin_ev.detail["status"] == "done" and fin_ev.detail["resume_of"] == "code/001_orig"
    for _ in range(3):
        env.d.tick()
    assert queued(env) == ["code/002_post_audit_001"]
    assert retro_line(env).startswith("- workflow retro: 002_post_audit_001, ") and "next" not in retro_line(env)
