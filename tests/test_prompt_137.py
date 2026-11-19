"""137: state.md split (habits.md, paged.md, the code repo's docs/rulings.md); bug hunts stop on yield."""

from conftest import git
from test_prompt_123 import LONG, SHORT, flat, render

from conductor import executor
from conductor.util import read_text


def session_md(env) -> str:
    env.signal("planner-1.signal", "by: planner\nsession: start")
    env.tick_until(lambda: env.events("checkpoint"))
    return read_text(env.control / "session.md")


def commit_control(env, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (env.control / rel).write_text(text, encoding="utf-8", newline="\n")
    git(env.control, "add", "--", *files)
    git(env.control, "commit", "-qm", "split")


def test_a_split_project_takes_its_habits_from_habits_md(env):
    commit_control(env, {"state.md": "# state\n\n## Next\n\n- the wavefront\n",
                         "habits.md": "# test — planner habits\n\n- stage receipts before quoting them\n"})
    text = session_md(env)
    assert "## Planner habits\n\nThe planner's own rules, from `habits.md`" in text
    assert "- stage receipts before quoting them" in text
    assert "split state" not in text and "trim state" not in text


def test_an_unsplit_project_reads_as_before(env):
    commit_control(env, {"state.md": "# state\n\n## Planner habits\n\n- keep it short\n"})
    text = session_md(env)
    assert "The planner's own rules, from `state.md`" in text and "- keep it short" in text
    assert "split state" not in text        # under budget: no due line (the receipt test covers past it)


def test_the_preamble_and_diff_review_name_docs_rulings_md_only_when_the_repo_has_it(env):
    wt = env.root / "wt"
    run = {"worktree": str(wt), "branch": "conductor/001_a", "base": "0123456789abcdef"}
    for body in (LONG, SHORT):
        assert "rulings.md" not in render(env, body, run=dict(run))
    assert "rulings.md" not in render(env, LONG, run=dict(run), ultracode=True)
    (wt / "docs").mkdir(parents=True)
    (wt / "docs" / "rulings.md").write_text("# Rulings\n\n- cap at 4\n", encoding="utf-8")
    full = flat(render(env, LONG, run=dict(run)))
    assert "the project's locked rulings (`docs/rulings.md`, read before you start; CLAUDE.md, docs, the prompt)" in full
    assert "the prompt's rulings quoted word for word, the repo's `docs/rulings.md` whole, its done-when list" in full
    assert "Conflicts: locked rulings (`docs/rulings.md`: read it first), goal" in flat(render(env, SHORT, run=dict(run)))
    assert "diff review, checked against `docs/rulings.md` too:" in flat(render(env, LONG, run=dict(run), ultracode=True))
    assert "rulings.md" not in render(env, LONG, run=dict(run), template="watch", readonly=True)


def test_a_bug_hunt_is_told_to_stop_on_yield_and_reports_its_rounds(env):
    body = ("FAKE note **Time**: start 10:00am, scope: recent changes since abc1234, end 10:50am, "
            "stopped on yield after rounds 3 and 4\\n- round 1, shop screen, 20m: 3 user-facing, 1 wording"
            "\\n- round 3, checker sweep, 10m: 0 user-facing, 2 wording\\n- round 4, menus, 10m: 0 user-facing, 1 polish")
    env.queue("001_hunt", body, template="bug_hunt", stage="false")
    env.tick_until(lambda: env.finished("code/001_hunt"))
    paths = executor.RunPaths.for_name(env.cfg, "code", "001_hunt")
    report_path, prompt = paths.report, flat(read_text(paths.prompt))
    assert "**Scope: recent changes**, unless the planner's note below asks for a **full bug hunt**" in prompt
    assert "**After two thin rounds in a row, the hunt is over**" in prompt
    assert "is a cap, not a target" in prompt and "ending early is never `partial`" in prompt
    assert "Leftover budget" not in prompt and "With over an hour of the time limit left" not in prompt
    assert "hunt-end" not in prompt
    assert env.finished("code/001_hunt").detail["status"] == "done"
    report = read_text(report_path)
    assert "stopped on yield after rounds 3 and 4" in report and "- round 4, menus, 10m: 0 user-facing" in report
