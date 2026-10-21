"""`review:` writes `review.md`: unreviewed reports inlined, conductor's own past HANDOFF's marker."""

import pytest

from conductor import config, executor, inbox, review, session, state


def send(env, text: str, name: str) -> None:
    env.signal(name, text)
    env.tick_until(lambda: not (env.control / "signals" / name).exists())


def conductor_root(tmp_path):
    root = tmp_path / "conductor"                      # a stand-in for conductor's own repo
    (root / "reports").mkdir(parents=True)
    (root / "HANDOFF.md").write_text("Last report reviewed by the planner: 12_x\n", encoding="utf-8")
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


def write_handoff(root, text: str) -> None:
    (root / "reports").mkdir(parents=True, exist_ok=True)
    (root / "HANDOFF.md").write_text(text, encoding="utf-8")
    for name in ("12_x", "13_z"):
        (root / "reports" / f"{name}_report.md").write_text("status: done\n", encoding="utf-8")


def test_no_marker_line_lists_no_conductor_reports(tmp_path):
    write_handoff(tmp_path, "# Handoff\n\nNothing about reports here.\n")
    assert session.reports_past_marker(tmp_path) == (None, [])
    [head] = session.conductor_reports(tmp_path)
    assert head.startswith("No `Last report reviewed by the planner:` line found in ") and "nothing listed" in head


@pytest.mark.parametrize("line", ["- Last report reviewed by the planner: 12_x",
                                  "**Last report reviewed by the planner:** 12_x",
                                  "- **Last report reviewed by the planner: 12_x**"])
def test_a_decorated_marker_line_still_counts(tmp_path, line):
    write_handoff(tmp_path, f"## Status\n\n{line}\n")
    marker, found = session.reports_past_marker(tmp_path)
    assert marker == "12_x" and [name for name, _ in found] == ["13_z"]


def test_render_without_handoff(env, tmp_path):
    bundle = review.render(env.d.cfg, state.scan(env.d.cfg, env.d.log), "signals/x", root=tmp_path)
    assert "- none" in bundle.text and "HANDOFF.md not found" in bundle.text
    assert (bundle.items, bundle.conductor) == ([], [])
