"""Prompt 113 (planner notes of 2026-11-07 to 11-13, fractals): items fix the obvious in place
instead of leaving audit notes, so a self-audit writes and lands like any serial item; a repo
without a *Warning checks* section raises no warning; ultracode helpers get their own scratch
path; a refused edit block says old text must be whole lines."""

import pytest

from conductor import executor, inbox, notify, request
from conductor.items import Item

from test_preamble import SHORT, render
from test_resume import finish_original, parsed, prompt


def flat(text: str) -> str:
    return " ".join(text.split())


def test_both_preambles_and_the_self_audit_fix_the_obvious_in_place(env):
    full, light = flat(render(env, SHORT * 3)), flat(render(env, SHORT))
    assert "Unless readonly, fix anything obvious you find in place before you finish, even at some extra time" in full
    assert "a note or follow-up is only for what needs Matt's decision or is large" in full
    assert "Unless readonly, fix small obvious things in place." in light
    assert "give each helper its own scratch path (`out/001_a/<helper>/`)" in full
    body = flat(executor.body(env.d.cfg, Item("code", "002_a", env.control / "x.md", num=2, body="",
                                              template="self_audit", resume_of="code/001_a")))
    assert "Fix what is obvious in place, before you finish, even at some extra time" in body
    assert "Change nothing" not in body and "A note is only for what needs Matt's decision or is large" in body


def test_a_self_audit_writes_unless_marked_readonly_and_an_ask_never_does(tmp_path):
    assert parsed(tmp_path, "resume: code/001_orig").readonly
    assert not parsed(tmp_path, "resume: code/001_orig\ntemplate: self_audit", body="").readonly
    kept = parsed(tmp_path, "resume: code/001_orig\ntemplate: self_audit\nreadonly: true", body="")
    assert kept.readonly and not kept.errors


@pytest.mark.slow
def test_a_post_audit_lands_its_fixes(env):
    finish_original(env)
    env.queue("002_post", "FAKE commit fix.txt fixed", resume="code/001_orig", template="self_audit")
    env.tick_until(lambda: env.finished("code/002_post"))
    fin = env.finished("code/002_post")
    assert fin.detail["status"] == "done" and fin.detail.get("merged")
    p = prompt(env, "002_post")
    assert "it lands like any item (its fixes commit)" in p and "This item is readonly" not in p
    assert (env.repo / "fix.txt").read_text(encoding="utf-8").strip() == "fixed"


def test_a_missing_warning_checks_section_is_no_warning():
    said = "data: 4 of 12 drafts miss the band; docs: CLAUDE.md has no *Warning checks* section to run"
    assert inbox.warnings_text({"warnings": said}) == "data: 4 of 12 drafts miss the band"
    only = {"warnings": "docs: CLAUDE.md has no *Warning checks* section to run", "status": "done"}
    assert inbox.warnings_text(only) is None
    assert "warning" not in notify.note("finish", "code/043_x", "code", only).body     # 115: never toasted


def test_a_refused_edit_block_says_old_text_must_be_whole_lines():
    with pytest.raises(request.RequestError, match=r"not found \(old text must be whole lines\)"):
        request.apply_edits("one two three\n", "state.md", [("one two\n", "x\n")])
