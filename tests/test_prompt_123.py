"""123: the preambles rewritten as short guidance; a diff review before the report; a cap on fix loops."""

import re
from datetime import datetime

from conductor import executor
from conductor.items import Item

SHORT = "Fix the typo in the README and commit it. " * 3
LONG = SHORT * 20


def render(env, body: str, run: dict | None = None, **meta) -> str:
    item = Item("code", "001_a", env.control / "queue" / "code" / "001_a.md", num=1, body=body, **meta)
    paths = executor.RunPaths.for_item(env.cfg, item)
    run = run if run is not None else {"worktree": str(env.root / "wt"), "branch": "conductor/001_a",
                                       "base": "0123456789abcdef"}
    return executor.build_prompt(env.cfg, item, paths, 1.0, datetime(2026, 11, 16, 12), run).split(executor.PROMPT_FOLLOWS)[0]


def flat(text: str) -> str:
    return " ".join(text.split())


def test_a_writable_full_item_gets_the_diff_review_against_its_base(env):
    text = flat(render(env, LONG))
    assert "Diff review. Before the report, send one read-only subagent `git diff 0123456789`" in text
    assert "the prompt's rulings quoted word for word, its done-when list" in text
    assert "*What failure looks like*" in text and "no style-only comments" in text
    assert "at most one more pass, on the new diff only" in text
    assert "`Diff review: N found, M fixed, K rejected (why)`" in text
    # the review comes before the report-path line, so it is done before writing
    assert text.index("Diff review.") < text.index("Write your report to exactly this path")


def test_the_main_checkout_reviews_against_the_head_it_started_from(env):
    text = flat(render(env, LONG, run={"worktree": None, "branch": None, "base": None, "repo_head": "abcdef0123456789"}))
    assert "`git diff abcdef0123`" in text


def test_light_template_and_readonly_items_skip_it_and_ultracode_counts_its_own(env):
    assert "Diff review" not in render(env, SHORT)
    assert "Diff review" not in render(env, LONG, readonly=True)
    assert "Diff review" not in render(env, LONG, template="bug_hunt")
    assert "Diff review" not in render(env, LONG, template="watch")
    ultra = flat(render(env, LONG, ultracode=True))
    assert "Your Ultracode adversarial review of the diff (`git diff 0123456789`) is this item's diff review" in ultra
    assert "send one read-only subagent" not in ultra and "Diff review: N found" in ultra


def test_both_preambles_cap_fix_loops(env):
    full, light = flat(render(env, LONG)), flat(render(env, SHORT))
    assert ("After three failed attempts at the same failure (a check, build or gate), stop patching: change "
            "approach and say so in a `progress` line, asking `design: true` if the new approach changes design") in full
    assert "Three failed fixes for one failure: change approach." in light


def test_the_locked_lines_stay_and_the_incidents_leave(env):
    full = flat(render(env, LONG))
    assert "only hard stop is a prompt's wrong-repo guard" in full
    assert "Paired seeds for a comparison, fresh seeds for a final read" in full
    assert "a ruling putting over ~30% of a pass's items on one side (ask its meaning before acting on all)" in full
    assert "`done`: goal met; a soft-band or measurement miss that the done-when table reports is still `done`" in full
    assert "Write your report to exactly this path:" in render(env, SHORT)
    for name in ("PREAMBLE", "LIGHT", "CALLS", "CALLS_LIGHT", "SERVERS", "CALIBRATE", "PICK", "REVIEW", "REVIEW_ULTRA"):
        text = getattr(executor, name)
        assert not re.search(r"\b(aicomplete|fractals|wordwarriors|website) \d", text), name


def test_word_targets(env):
    # 123's soft targets: full ~900 words before its optional blocks, light ~250; the report says what runs over.
    assert len(executor.PREAMBLE.split("{calls}")[0].split()) <= 955     # 152: +8 for `out/` heredocs and `slow:` fixes
    assert len(executor.LIGHT.split()) <= 320        # 151: +5 naming heredoc-fed Python and bypass mode; 152: +3 `&` jobs, `slow:` fix
    assert len(render(env, SHORT).split()) <= 400
