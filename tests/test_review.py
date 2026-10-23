"""`review:` writes `review.md`: unreviewed reports inlined, conductor's own past the planner's marker."""

import pytest

from conductor import config, executor, inbox, review, session, state


def send(env, text: str, name: str) -> None:
    env.signal(name, text)
    env.tick_until(lambda: not (env.control / "signals" / name).exists())


def conductor_root(tmp_path):
    root = tmp_path / "conductor"                      # a stand-in for conductor's own repo
    (root / "reports").mkdir(parents=True)
    (root / "planner-notes.md").write_text("Last report reviewed by the planner: 12_x\n", encoding="utf-8")
    for name, text in [("12_x", "status: done\nold news"), ("13_z", "status: partial\nthe thirteenth body")]:
        (root / "reports" / f"{name}_report.md").write_text(text + "\n", encoding="utf-8")
    return root


@pytest.mark.slow
def test_review_all_bundles_every_unreviewed_report_and_marks_nothing(env, tmp_path, monkeypatch):
    monkeypatch.setattr(session, "CONDUCTOR_ROOT", conductor_root(tmp_path))
    env.queue("001_seen", "FAKE report done")
    env.queue("002_a", "FAKE report partial")
    env.queue("003_b", "FAKE report done")
    env.tick_until(lambda: env.finished("code/003_b"))
    send(env, "reviewed: 001", "rev.signal")
    amend = executor.RunPaths.for_name(env.d.cfg, "code", "002_a").amend
    amend.write_text("an amendment\n", encoding="utf-8")
    executor.RunPaths.for_name(env.d.cfg, "code", "003_b").report.unlink()
    send(env, "by: planner\nreview: all", "planner-7.signal")
    text = (env.control / "review.md").read_text(encoding="utf-8")
    assert text.startswith("# Review — ") and "Written for `signals/planner-7.signal` at " in text
    summary = text.split("## Unreviewed reports", 1)[1].split("\n## ", 1)[0]
    assert summary.index("code/002_a **partial**") < summary.index("code/003_b **done**")
    assert "001_seen" not in text
    a = text.split("## code/002_a", 1)[1].split("\n## ", 1)[0]
    assert "- report: `reports/code__002_a.md`" in a and f"- amendments: `{env.d.cfg.rel(amend)}`" in a
    assert "an amendment" not in a and "audit notes" not in a and "status: partial" in a
    assert "the report file is missing" in text.split("## code/003_b", 1)[1]
    cond = text.split("## Conductor reports", 1)[1]
    assert "### 13_z" in cond and "the thirteenth body" in cond and "12_x" not in cond.split("\n", 2)[2]
    [ev] = env.events("review")
    assert ev.paths == ["signals/planner-7.signal", "review.md"] and ev.detail["which"] == "all"
    assert (ev.detail["items"], ev.detail["conductor"], ev.detail["by"]) == (["code/002_a", "code/003_b"], ["13_z"], "planner")
    assert "every unreviewed report: 2 reports, 1 conductor report" in inbox.describe(ev)
    assert len(env.events("reviewed")) == 1
    assert [e.item for e in state.scan(env.d.cfg, env.d.log).unreviewed()] == ["code/002_a", "code/003_b"]


@pytest.mark.slow
def test_review_one_item_and_refusals(env, tmp_path):
    env.queue("001_a", "FAKE report done")
    env.queue("002_b", "FAKE report partial")
    env.queue("003_long", env.hold() + "\nFAKE report done")
    env.tick_until(lambda: any(e.item == "code/003_long" for e in env.events("start")))
    send(env, "review: code/2", "one.signal")
    text = (env.control / "review.md").read_text(encoding="utf-8")
    assert "Written for `signals/one.signal`" in text and "`review: code/002_b`" in text
    assert "## code/002_b" in text and "001_a" not in text and "## Conductor reports" not in text
    [ev] = env.events("review")
    assert (ev.item, ev.detail["which"], ev.detail["items"]) == ("code/002_b", "code/002_b", ["code/002_b"])
    for n, value in enumerate(["003", "009", "nowhere/001", "all of it"]):
        send(env, f"review: {value}", f"r{n}.signal")
        assert (env.control / "signals" / f"r{n}.signal.bad").is_file(), value
    errors = [e.detail["error"] for e in env.events("invalid")]
    assert "has not finished" in errors[0] and "no finished item" in errors[1] and "not a repo" in errors[2]
    assert len(env.events("review")) == 1 and not env.events("reviewed")
    env.release()
    env.tick_until(lambda: env.finished("code/003_long"))


def test_review_md_is_runtime():
    assert "review.md" in config.RUNTIME


def test_a_red_item_shows_its_reasons_and_setup_log(env, tmp_path):
    env.queue("001_red", "FAKE report none")
    env.tick_until(lambda: env.finished("code/001_red"))
    [ev] = env.events("finish")
    reasons = "; ".join(ev.detail["reasons"])
    assert ev.detail["status"] == "red" and reasons
    setup = executor.RunPaths.for_name(env.d.cfg, "code", "001_red").setup
    setup.write_text("npm ci\n", encoding="utf-8")
    text = review.render(env.d.cfg, state.scan(env.d.cfg, env.d.log), "signals/x", root=tmp_path).text
    summary = text.split("## Unreviewed reports", 1)[1].split("\n## ", 1)[0]
    assert f"code/001_red **red** ({reasons}), finished" in summary
    section = text.split("## code/001_red", 1)[1]
    assert f"- red: {reasons}" in section and f"- setup log: `{env.d.cfg.rel(setup)}`" in section


def write_notes(root, text: str) -> None:
    (root / "reports").mkdir(parents=True, exist_ok=True)
    (root / "planner-notes.md").write_text(text, encoding="utf-8")
    for name in ("12_x", "13_z"):
        (root / "reports" / f"{name}_report.md").write_text("status: done\n", encoding="utf-8")


def test_no_marker_line_lists_no_conductor_reports(tmp_path):
    write_notes(tmp_path, "# Planner notes\n\nNothing about reports here.\n")
    assert session.reports_past_marker(tmp_path) == (None, [])
    assert session.conductor_reports(tmp_path) == []
    assert session.reports_head(tmp_path, None, []).startswith("No `Last report reviewed by the planner:` line found in ")


def test_a_marker_the_ledger_names_orders_by_its_done_lines(tmp_path):
    """A cleanup pass's marker has no number: every report used to count as past it."""
    write_notes(tmp_path, "Last report reviewed by the planner: cleanup_2026-10-23\n")
    for name in ("cleanup_2026-10-23", "14_new", "cleanup_2026-10-24"):
        (tmp_path / "reports" / f"{name}_report.md").write_text("status: partial\n", encoding="utf-8")
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "ledger.md").write_text(
        "9:00am start 12_x\n9:10am done 12_x → reports/12_x_report.md\n9:20am done 13_z → reports/13_z_report.md\n"
        "9:30am done cleanup_2026-10-23 → reports/cleanup_2026-10-23_report.md\n9:40am start 14_new\n"
        "9:50am done 14_new → reports/14_new_report.md\n10:00am done cleanup_2026-10-24 → r\n", encoding="utf-8")
    marker, found = session.reports_past_marker(tmp_path)
    assert marker == "cleanup_2026-10-23" and [name for name, _ in found] == ["14_new", "cleanup_2026-10-24"]
    assert session.conductor_reports(tmp_path)[0] == (
        f"- conductor's 14_new **partial** — `{tmp_path / 'reports' / '14_new_report.md'}`")
    write_notes(tmp_path, "Last report reviewed by the planner: cleanup_2026-01-01\n")     # not in the ledger
    assert session.reports_past_marker(tmp_path) == ("cleanup_2026-01-01", [])


def test_the_inbox_lists_conductor_reports_past_the_marker(env, tmp_path):
    root = tmp_path / "conductor"
    write_notes(root, "Last report reviewed by the planner: 12_x\n")
    text = inbox.render(env.d.cfg, env.d.log, state.scan(env.d.cfg, env.d.log), root=root)
    section = text.split("## Unreviewed reports", 1)[1].split("\n## ", 1)[0]
    assert f"- conductor's 13_z **done** — `{root / 'reports' / '13_z_report.md'}`" in section
    assert "12_x" not in section and "process all moves `Last report reviewed by the planner:`" in section
    assert "## Unreviewed reports" not in inbox.render(env.d.cfg, env.d.log, state.scan(env.d.cfg, env.d.log))


@pytest.mark.parametrize("line", ["- Last report reviewed by the planner: 12_x",
                                  "**Last report reviewed by the planner:** 12_x",
                                  "- **Last report reviewed by the planner: 12_x**"])
def test_a_decorated_marker_line_still_counts(tmp_path, line):
    write_notes(tmp_path, f"## Status\n\n{line}\n")
    marker, found = session.reports_past_marker(tmp_path)
    assert marker == "12_x" and [name for name, _ in found] == ["13_z"]


def test_render_without_notes(env, tmp_path):
    bundle = review.render(env.d.cfg, state.scan(env.d.cfg, env.d.log), "signals/x", root=tmp_path)
    assert "- none" in bundle.text and "planner-notes.md not found" in bundle.text
    assert (bundle.items, bundle.conductor) == ([], [])
