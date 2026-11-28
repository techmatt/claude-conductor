"""Prompt 152: fresh seeds after the diff review for any seeded bar, `&` jobs refused
anywhere in a Bash call, `slow:` naming an existing fix or a new one, the leftover-budget
rule at 30 minutes, and the digest marking a habits file over its cap."""

import pytest

from conductor import rules, session
from conductor.util import read_text

from test_preamble import render
from test_prompt_122 import bash, denied
from test_prompt_123 import LONG, SHORT


def flat(text: str) -> str:
    return " ".join(text.split())


# ---------------------------------------------------------------- seeds, slow, leftover
def test_full_preamble_says_fresh_seeds_after_the_review_for_any_seeded_bar(env):
    text = flat(render(env, LONG))
    assert "Any item with a bar read on seeds (rules, tuning, sim players, balance) gets it before its fresh seeds" in text
    assert "A rules change gets it" not in text


def test_slow_line_names_an_existing_fix_or_a_new_one(env):
    for text in (flat(render(env, LONG)), flat(render(env, SHORT))):
        assert "`use <" in text and "`needs <" in text
    assert "(check flags before `needs`)" in flat(render(env, LONG))


def test_leftover_rule_starts_at_30_minutes_and_repeats(env):
    full = flat(render(env, LONG))
    assert "With over 30 minutes of the time limit left at the end" in full
    assert "repeat while any such bar remains" in full
    assert "over an hour" not in full.split("Latitude.")[1].split("Measurement.")[0]
    assert "Goal unmet, over 30 minutes left" in flat(render(env, SHORT))


def test_preambles_claim_only_what_the_hooks_refuse(env):
    full = flat(render(env, LONG))
    assert "a heredoc into a file outside `out/`" in full
    assert "`&` jobs and sleeps" in full
    assert "%TEMP%, `&` jobs." in flat(render(env, SHORT))
    # and each claim holds: a heredoc outside out/ is refused, one into out/ passes
    assert denied(bash("cat > src/x.py <<'PY'\nx\nPY"))
    assert denied(bash("cat > out/131_a/edit.py <<'PY'\nx\nPY")) is None


# ---------------------------------------------------------------- & jobs
@pytest.mark.parametrize("command", [
    "make && (nice -n 10 sim.exe axes > out/x/a.txt 2>&1; echo DONE >> out/x/a.txt) &\nsleep 1; echo started",
    "python farm.py > out/x/f.log 2>&1 & echo $!",
    "server & curl localhost:5200",
    "cargo build &",
])
def test_an_amp_job_anywhere_is_refused(command):
    assert "dies with its shell" in denied(bash(command))


@pytest.mark.parametrize("command", [
    "make && make test",
    "cargo build 2>&1 | head -30",
    "echo oops >&2",
    "ls &>/dev/null",
    "cmd |& tee out/x/log.txt",
    "curl 'http://x/?a=1&b=2'",
    "grep -n \"a & b\" docs/x.md",
    "echo $((6 & 3))",
    "ls  # tea & cake",
    "echo a\\&b",
])
def test_other_ampersands_pass(command):
    assert denied(bash(command)) is None


def test_powershell_call_operator_passes():
    assert rules.check({"tool_name": "PowerShell", "tool_input": {"command": "& .\\build.ps1"}}) is None


# ---------------------------------------------------------------- habits cap
def habits(env, n: int, name: str = "habits.md") -> None:
    lines = ["# Planner habits", ""] + [f"- habit {i}\n  more on {i}" for i in range(n)]
    (env.control / name).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def test_digest_marks_habits_over_the_cap(env):
    habits(env, session.HABITS_CAP)
    assert session.habits_line(env.control) is None
    habits(env, 17)
    assert session.habits_line(env.control) == (
        "- **due** trim habits: `habits.md` 17 habits, cap 15: the next workflow retro (or post-audit) prunes it to the cap")


def test_the_session_digest_carries_the_mark(env):
    habits(env, 17)
    env.signal("planner-1.signal", "by: planner\nsession: start")
    env.tick_until(lambda: env.events("checkpoint"))
    text = read_text(env.control / "session.md")
    facts = text.split("## Maintenance facts")[1]
    assert "- **due** trim habits: `habits.md` 17 habits, cap 15" in facts


def test_an_unsplit_state_counts_its_habits_section(env):
    state = env.control / "state.md"
    body = "\n".join(f"- habit {i}" for i in range(16))
    state.write_text(f"# State\n\n## Planner habits\n\n{body}\n\n## Plan\n\n- a\n", encoding="utf-8", newline="\n")
    assert session.habits_line(env.control).startswith("- **due** trim habits: `state.md` 16 habits")
