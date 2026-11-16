"""Prompt 121: a warning unchanged from main goes to the repeat line instead of leading the
digests; the picker images' scale (`test_prompt_79.py`); a selected project tab keeps its
signal colour (`test_app_title.py`)."""

from conductor import executor, inbox, state

from test_prompt_107 import fin
from test_signals import send

# helium-hustle 016-020's own wording, before the preamble's marker.
HH = ["balance: the standing milestone and run-1 length misses (M5-M14), unchanged by this item (no rules change)",
      "balance: 24 judge misses (M5-M14 and run 1's length and time), all from before this item, which changes no rules or code",
      "balance: 24 milestone and run-1 targets missed for the author and casual players, the same as on main",
      "balance: the judge misses most milestone windows (pre-existing; this item changes no rules)",
      "balance: unchanged from main: 24 milestone misses"]


def test_unchanged_reads_the_marker_and_the_paraphrases():
    assert all(inbox.unchanged(t) for t in HH)
    assert not inbox.unchanged("balance: 3 new milestone misses after the price change")
    assert not inbox.unchanged("docs: web/README.md at 14.1 KB (about 1.2 KB free)")
    assert inbox.warning_parts(HH[3]) == [("balance", HH[3].split(": ", 1)[1])]     # `;` inside (…) stays


def test_the_preambles_ask_for_the_marker():
    assert "`<kind>: unchanged from main: <text>`" in executor.PREAMBLE
    assert "unchanged from main" not in executor.LIGHT       # its 430-word cap; `unchanged` reads paraphrases


def test_an_unchanged_warning_leaves_the_top_for_the_repeat_line(env):
    for i, w in enumerate(HH, 16):
        fin(env, f"0{i}_ui", warnings=w)
    fin(env, "021_rules", warnings="balance: M7 now reached on run 4; docs: README at 14.9 KB")
    view = state.scan(env.d.cfg, env.d.log)
    lines = inbox.warning_lines(view, view.unreviewed())
    assert [ln.split(" (")[0] for ln in lines if not ln.startswith("- repeated")] == ["- code/021_rules"]
    assert lines[0].endswith("**balance**: M7 now reached on run 4; **docs**: README at 14.9 KB")    # new still leads
    [rep] = [ln for ln in lines if ln.startswith("- repeated")]
    assert rep == ("- repeated in `code`'s last 5 report(s): balance ×5 (017, 018, 019, 020, 021); "
                   "balance, unchanged from main on 016, 017, 018, 019, 020")

    send(env, "by: planner\nsession: start", "planner.signal")
    text = (env.control / "session.md").read_text(encoding="utf-8")
    warned = text.split("## Warnings\n", 1)[1].split("\n## ", 1)[0]
    assert "milestone" not in warned.replace("M7 now reached", "") and "unchanged from main on 016" in warned
    send(env, "review: all", "review.signal")
    review = (env.control / "review.md").read_text(encoding="utf-8")
    assert "unchanged from main on 016" in review and "the same as on main" not in review.split("## Unreviewed")[0]


def test_only_unchanged_warnings_leave_one_line_and_an_unbolded_event(env):
    ev = fin(env, "001_art", warnings=HH[-1])
    view = state.scan(env.d.cfg, env.d.log)
    assert inbox.warning_lines(view, view.unreviewed()) == [
        "- repeated in `code`'s last 5 report(s): balance, unchanged from main on 001"]
    line = inbox.describe(ev)
    assert " · warnings: balance: unchanged from main" in line and "**warnings" not in line
