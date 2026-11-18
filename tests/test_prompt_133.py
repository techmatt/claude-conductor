"""Conductor 133: a held design default is revisited once when the item writes its report
with over an hour left (aicomplete 271), and the preamble says plainly which shell edits
the hooks refuse."""

import json
from datetime import timedelta

from conductor import questions, talk
from conductor.util import now, stamp

ITEM = "code/001_x"


def _run(tmp_path, hours_left: float):
    rdir = tmp_path / "running" / "code"
    rdir.mkdir(parents=True, exist_ok=True)
    report = tmp_path / "reports" / "001_x_report.md"
    (rdir / "001_x.run.json").write_text(json.dumps({
        "item": ITEM, "repo": "code", "name": "001_x", "report": str(report),
        "deadline": stamp(now() + timedelta(hours=hours_left))}), encoding="utf-8")
    return report


def _write(path, tool="Write"):
    return {"tool_name": tool, "tool_input": {"file_path": str(path), "content": "status: done\n"}}


def _ctx(out):
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else None


def test_a_held_design_default_is_revisited_once_at_the_report(tmp_path):
    report = _run(tmp_path, 1.5)
    assert talk.hook(tmp_path, ITEM, _write(report)) is None              # no design question: nothing
    q = questions.create(tmp_path, ITEM, "Defer the rescale?", ["defer", "do it"], "defer", False, 30, design=True)
    assert talk.hook(tmp_path, ITEM, _write(tmp_path / "other.md")) is None    # not the report
    ctx = _ctx(talk.hook(tmp_path, ITEM, _write(report, "Edit")))
    assert f"design question `{q.id}`" in ctx and ("about 1 h 29 min" in ctx or "about 1 h 30 min" in ctx)
    assert "take the next in-scope step on that part now" in ctx
    assert talk.hook(tmp_path, ITEM, _write(report)) is None              # once


def test_no_revisit_with_under_an_hour_left_or_once_matt_ruled(tmp_path):
    report = _run(tmp_path, 0.5)
    q = questions.create(tmp_path, ITEM, "Defer?", ["defer"], "defer", False, 30, design=True)
    assert talk.hook(tmp_path, ITEM, _write(report)) is None
    _run(tmp_path, 2)
    questions.resolve(q, "answered", "deferring is fine", "matt")
    assert talk.hook(tmp_path, ITEM, _write(report)) is None
