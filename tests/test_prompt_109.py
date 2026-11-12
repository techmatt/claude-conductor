"""Prompt 109: a report's `warnings:` line. Any item may raise warnings of any kind; they never
stop it or hold its queue, and they lead the inbox, `session.md` and `review.md` by item and
kind, counted per kind across recent reports. A repo declares its own checks in
`warning_checks`, which the preamble names."""

from datetime import datetime

from conductor import executor, inbox, notify
from conductor.items import Item

from test_signals import send


def test_the_header_reads_warnings_beside_slow_and_friction(tmp_path):
    report = tmp_path / "r.md"
    report.write_text("status: done\nblocks: none\ncarry: none\nslow: none\nwarnings: design: capstone cheap; "
                      "perf: 40s turns\nfriction: none\n\nwarnings: body line\n", encoding="utf-8")
    header, problem = executor.read_header(report)
    assert problem is None and header["warnings"] == "design: capstone cheap; perf: 40s turns"
    assert header["friction"] == "none"
    report.write_text("status: done\nblocks: none\ncarry: none\n# Findings\nWarnings: in the body\n", encoding="utf-8")
    assert "warnings" not in executor.read_header(report)[0]


def test_warning_parts_split_by_kind():
    assert inbox.warning_parts("design: same power on every board; **data**: 3 rows; games too short") == [
        ("design", "same power on every board"), ("data", "3 rows"), ("other", "games too short")]
    assert inbox.warnings_text({"warnings": "None."}) is None and inbox.warnings_text({}) is None


def render(env, body: str) -> str:
    item = Item("code", "001_a", env.control / "queue" / "code" / "001_a.md", num=1, body=body)
    paths = executor.RunPaths.for_item(env.cfg, item)
    run = {"port": 5200, "worktree": str(env.root / "wt"), "branch": "conductor/001_a", "base": "0123456789abcdef"}
    return executor.build_prompt(env.d.cfg, item, paths, 1.0, datetime(2026, 11, 12, 17), run)


def test_every_preamble_asks_for_warnings_and_names_the_repos_checks(env):
    short, long = "Fix the typo.", "Do the work. " * 400
    for body in (short, long):
        text = render(env, body)
        assert "warnings: none | <kind>:" in text and "*Warning checks*" in text
    env.configure('[repos.code]\npath = "../code"\nwarning_checks = ["uv run python tools/design_check.py"]\n')
    assert env.d.cfg.opts("code").warning_checks == ["uv run python tools/design_check.py"]
    for body in (short, long):
        assert "*Warning checks* and `uv run python tools/design_check.py`" in render(env, body)
    assert "never changes your status" in render(env, long)


def test_warnings_lead_the_digests_and_never_hold(env):
    env.queue("001_cards", "FAKE warnings design: capstone has a cheap source; perf: 40s turns\nFAKE report done")
    env.queue("002_tune", "FAKE warnings design: same power on every board\nFAKE report partial")
    env.queue("003_plain", "FAKE report done")
    env.tick_until(lambda: env.finished("code/003_plain"))      # nothing held the queue
    assert env.finished("code/001_cards").detail["warnings"] == "design: capstone has a cheap source; perf: 40s turns"
    assert not env.events("hold") and not env.events("red")
    raised = [t[:2] for t in env.toasts if "raised warnings" in t[0]]
    assert raised == [(f"{env.d.cfg.project}: code/001_cards raised warnings",
                       "design: capstone has a cheap source; perf: 40s turns"),
                      (f"{env.d.cfg.project}: code/002_tune raised warnings", "design: same power on every board")]

    send(env, "by: planner\nsession: start", "planner.signal")
    text = (env.control / "session.md").read_text(encoding="utf-8")
    warned = text.split("## Warnings\n", 1)[1].split("\n## ", 1)[0]
    assert text.index("## Warnings") < text.index("## Unreviewed reports")
    assert "- code/001_cards (done, finished " in warned
    assert "**design**: capstone has a cheap source; **perf**: 40s turns" in warned
    assert "- code/002_tune (partial, " in warned and "003_plain" not in warned
    assert "- repeated in `code`'s last 5 report(s): design ×2 (001, 002)" in warned
    assert "- `warnings:` kinds repeated in the last 3 report(s): design (001, 002)" in text

    send(env, "review: all", "review.signal")
    review = (env.control / "review.md").read_text(encoding="utf-8")
    assert review.index("## Warnings") < review.index("## Unreviewed reports")
    assert "**design**: same power on every board" in review

    # Reviewed, it leaves the top; the maintenance count stays.
    send(env, "reviewed: code/001_cards\nreviewed: code/002_tune", "done.signal")
    send(env, "session: again", "planner2.signal")
    text = (env.control / "session.md").read_text(encoding="utf-8")
    assert "## Warnings" not in text and "kinds repeated in the last 3 report(s): design (001, 002)" in text


def test_a_finish_without_warnings_does_not_toast():
    assert notify.message("p", "finish", "code/1", "code", {"warnings": "none"}) is None
    assert notify.message("p", "finish", "code/1", "code", {}) is None
