"""Prompt 146: `status` names unreviewed reports; readonly items' working papers, warnings and
audit notes; the file-write rule up front; a design question toasts."""

import pytest

from conductor import audits, executor, planner_read, state, warns

from test_ask import ask_line
from test_preamble import SHORT, render
from test_prompt_107 import fin


def test_status_names_unreviewed_reports_conductors_own_post_audits_too(env):
    assert "unreviewed reports" not in planner_read.project_status(env.cfg)
    fin(env, "044_look", readonly=True)
    fin(env, "045_post_audit_044", resume_of="code/044_look", template="self_audit")
    fin(env, "046_watch", template="watch")                       # a watch never waits for review
    env.d.log.append("reviewed", item="code/044_look", by="planner")
    assert "unreviewed reports: 1 (045_post_audit_044)" in planner_read.project_status(env.cfg)
    assert state.unreviewed_line(env.cfg, state.scan(env.cfg, env.d.log)) == "unreviewed reports: 1 (045_post_audit_044)"


def test_a_readonly_item_keeps_papers_out_of_commits_skips_checks_and_keeps_notes(env):
    full = " ".join(render(env, SHORT * 3, readonly=True).split())
    assert "`experiments/results/001_a/`), committed" not in full
    assert "go in `out/keep/001_a/` (nothing you write commits" in full
    assert "readonly: skip them and write `warnings: as on main`" in full and "run them before the report" not in full
    assert "Audit notes." in full and "obvious note" not in full
    light = " ".join(render(env, SHORT, readonly=True).split())
    assert "Working papers go in `out/keep/001_a/`" in light and "`warnings: as on main`" in light
    assert "Audit notes." in light
    writable = " ".join(render(env, SHORT * 3).split())
    assert "`experiments/results/001_a/`), committed" in writable and "run them before the report" in writable
    assert "Audit notes." not in writable
    assert audits.writes_notes({"readonly": True}) and not audits.writes_notes({"readonly": True, "template": "watch"})
    assert warns.warnings_text({"warnings": "as on main"}) is None


def test_the_file_write_rule_comes_first(env):
    for text in (render(env, SHORT * 3), render(env, SHORT)):
        assert text.index("heredoc") < text.index("Asking.")
    assert "Files. Write every file with Write or Edit" in executor.PREAMBLE


@pytest.mark.slow
def test_a_design_question_from_a_running_item_toasts(env):
    env.queue("001_ask", ask_line(question="Which rate?", default="0.5", design=True))
    env.tick_until(lambda: env.events("ask"))
    env.d.flush_toasts(force=True)
    assert any(t[0] == "test: design question from 001_ask (on its default)" for t in env.toasts)
