"""Prompt 79 (planner notes of 2026-11-05): split rulings and play-changing fixes are design
asks, helper briefs quote rulings, readonly records go in `carry:`, pickers start unselected,
and `append_only` files merge by union (the landing tests are in test_worktrees.py)."""

import subprocess

from conductor import executor, worktrees
from conductor.config import ConfigError, load

from test_preamble import SHORT, render


def flat(text: str) -> str:
    return " ".join(text.split())


def test_both_preambles_ask_about_a_split_ruling_and_a_play_changing_fix(env):
    # aicomplete 111 cut 76 prices on a misread ceiling (112 reverted them); its fix also
    # made "a draw never reshuffles" without asking.
    full, light = flat(render(env, SHORT * 3)), flat(render(env, SHORT))
    assert "a ruling putting over ~30% of a pass's items on one side (ask its meaning before acting on all)" in full
    assert "a fix changing play beyond its bug (the fix as default): `design: true`" in full
    assert "A ruling splitting a pass (over ~30% of items one way) or a fix changing play beyond its bug: " \
           "ask `design: true`" in light


def test_helper_briefs_quote_rulings_word_for_word(env):
    assert "Helper briefs quote the prompt's rulings word for word and give each helper" in flat(render(env, SHORT * 3))


def test_a_readonly_item_puts_its_records_in_carry_but_a_watch_is_not_told(env):
    line = "seed-ledger entries and anything the prompt says to commit go in `carry:` for the planner"
    assert line in flat(render(env, SHORT * 3, readonly=True))
    assert line in flat(render(env, SHORT, readonly=True))
    assert line not in flat(render(env, SHORT * 3))
    assert line not in flat(render(env, SHORT, readonly=True, template="watch"))


def test_the_picker_schema_says_rows_start_unselected():
    assert "Rows start with nothing selected (leave out `default`): Matt selects the few he likes." in flat(executor.PICK)


def test_append_only_is_a_repo_option_of_relative_globs(tmp_path):
    (tmp_path / "code").mkdir()
    toml = tmp_path / "conductor.toml"
    toml.write_text('project = "p"\n[repos.code]\npath = "code"\nappend_only = ["experiments/history.md"]\n',
                    encoding="utf-8")
    assert load(tmp_path).opts("code").append_only == ["experiments/history.md"]
    toml.write_text('project = "p"\n[repos.code]\npath = "code"\nappend_only = ["../x.md"]\n', encoding="utf-8")
    try:
        load(tmp_path)
        raise AssertionError("a path outside the repo was accepted")
    except ConfigError as exc:
        assert "append_only" in str(exc)


def test_attribute_patterns_match_only_their_own_path(tmp_path):
    names = ["experiments/history.md", "logs/run [1].md", "with space/a*b.md"]
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    attrs = tmp_path / "attrs"
    attrs.write_text("".join(f"{worktrees._attr_pattern(n)} merge=union\n" for n in names), encoding="utf-8")
    probe = names + ["history.md", "logs/run 1.md", "with space/aXb.md"]
    out = subprocess.run(["git", "-C", str(tmp_path), "-c", f"core.attributesFile={attrs.as_posix()}",
                          "check-attr", "merge", "--", *probe], capture_output=True, text=True, check=True).stdout
    got = {ln.rsplit(": merge: ", 1)[0]: ln.rsplit(": ", 1)[1] for ln in out.splitlines()}
    assert {n: got[n] for n in probe} == {**{n: "union" for n in names},
                                          **{n: "unspecified" for n in probe[len(names):]}}
