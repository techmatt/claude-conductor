"""Prompt 148: scripts by Write, never a heredoc (it loses a level of backslash escaping);
a planner may queue a workflow retro unasked; *Open questions* holds only what Matt should
answer now."""

from pathlib import Path

from conductor import rules

from test_preamble import SHORT, render

ROOT = Path(__file__).resolve().parents[1]


def test_both_preambles_and_the_refusal_say_write_scripts_never_heredocs(env):
    full = " ".join(render(env, SHORT * 3).split())
    assert ("in `out/001_a/`, written with Write (a heredoc loses a level of backslash "
            "escaping), run alone, never mid-chain.") in full
    assert "<<'EOF'" not in full
    light = " ".join(render(env, SHORT).split())
    assert "Write files with Write/Edit only" in light and "heredocs mangle backslashes" in light
    why = rules.check({"tool_name": "Bash", "tool_input": {"command": "sed -i s/a/b/ x.py"}})
    assert "write a script to a file with Write" in why and "<<'EOF'" not in why


def test_planner_md_carries_both_standing_calls():
    text = " ".join((ROOT / "planner.md").read_text(encoding="utf-8").split())
    assert "***Open questions* is only what Matt should answer now**" in text
    assert "taken defaults go to `paged.md` unless the executor said Matt must decide" in text
    assert "queue **post-audit X** at once, even with `auto_retros = false`" in text
