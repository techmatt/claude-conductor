"""75: staged questions under a heading, the audit-note rule, the done-when check before
an early finish, and parked edits in scope whatever the prompt's file limits."""

from datetime import datetime

from conductor import audits, executor, inbox, questions
from conductor.events import Event
from conductor.items import Item

from test_preamble import SHORT, render


def test_questions_under_a_heading_are_parsed():
    plan = ("Plan: do it.\n\n## Questions\n\n1. Target 0 energy or near 0?\n- Use the gift?\n\n"
            "Is 96 a cell right?\n\n## Outline\n- one\n")
    assert questions.plan_questions(plan) == ["Target 0 energy or near 0?", "Use the gift?", "Is 96 a cell right?"]
    assert questions.plan_questions("### Open questions\n\nNone.\n\n## Outline\n- a") == []
    assert questions.plan_questions("## Questions:\n- which seed?\n") == ["which seed?"]


def test_the_staged_line_says_when_no_question_was_parsed():
    ev = Event(id=1, ts="2026-11-03T21:00:00", kind="staged", item="code/001_a", repo="code", paths=[],
               detail={"plan": "Plan: x", "questions": [], "questions_parsed": 0})
    assert "no questions parsed" in inbox.describe(ev)


def test_an_audit_item_gets_the_note_rule_done_when_check_and_parked_line(env):
    item = Item("code", "001_a", env.control / "queue" / "code" / "001_a.md", num=1, body=SHORT * 3, audit=True)
    paths = executor.RunPaths.for_item(env.cfg, item)
    run = {"port": 5200, "worktree": str(env.root / "worktrees" / "001_a"), "branch": "conductor/001_a",
           "base": "0123456789abcdef"}
    text = executor.build_prompt(env.cfg, item, paths, 4.0, datetime(2026, 11, 3, 17), run,
                                 parked=[{"n": 1, "text": "rename hardware to Chips"}])
    assert "implement each obvious note yourself" in text and "notes you implemented" in text
    assert "list each done-when item\nagainst its measured result" in text
    assert "whatever the prompt's\nown file limits say" in text
    plain = render(env, SHORT * 3)
    assert "obvious note" not in plain and "list each done-when item" in plain
    assert "obvious note" not in render(env, SHORT * 3, audit=True, readonly=True)


def test_a_self_audit_writes_only_what_its_originals_notes_miss():
    assert "never a repeat" in audits.template_text("self_audit")
