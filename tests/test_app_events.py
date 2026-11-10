"""The Events tab's views and a finished item's trail."""

import json

from conductor.app import item_model, model
from conductor.app.model import Project
from conductor.app.streamlog import render_stream_json
from conftest import git


def test_filter_events(env):
    log = env.d.log
    log.append("enqueue", item="code/001_x")
    log.append("red", item="code/001_x", reasons=["r"])
    log.append("warning", note="n")
    p = Project(env.control)
    p.poll_events()
    assert {e.kind for e in p.filter_events(item="001")} == {"enqueue", "red"}
    assert [e.kind for e in p.filter_events(reds=True)] == ["red"]
    assert [e.kind for e in p.filter_events(kinds=["warning"])][-1:] == ["warning"]
    assert p.filter_events(repo="other") == []
    p.close()


def test_trail_of_a_merged_item(env):
    env.queue("001_x", "FAKE commit a.txt hello")
    env.tick_until(lambda: env.finished("code/001_x"))
    p = Project(env.control)
    p.poll_events()
    tr = p.trail("code/001_x")
    assert tr.where == "done"
    assert "FAKE commit a.txt hello" in tr.prompt
    assert {"enqueue", "start", "merge", "finish"} <= {e.kind for e in tr.events}
    assert tr.report.startswith("status:")
    assert "══ result" in tr.log
    text = item_model.git_trail(p.cfg, "code/001_x", p.events)
    assert "merged into main" in text and "a.txt" in text
    p.close()


def test_git_trail_prefers_the_branch(env):
    git(env.repo, "checkout", "-q", "-b", "conductor/002_y")
    (env.repo / "y.txt").write_text("y\n", encoding="utf-8")
    git(env.repo, "add", "y.txt")
    git(env.repo, "commit", "-q", "-m", "add y")
    git(env.repo, "checkout", "-q", "main")
    text = item_model.git_trail(env.cfg, "code/002_y", [])
    assert "still exists" in text and "add y" in text and "y.txt" in text
    assert "nothing committed survives" in item_model.git_trail(env.cfg, "code/003_z", [])


def test_render_stream_json(tmp_path):
    log = tmp_path / "x.log"
    lines = [
        {"type": "system", "subtype": "init", "session_id": "s1", "model": "m", "cwd": "/w"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Looking."},
                                                      {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "a\nb", "is_error": False}]}},
        {"type": "result", "subtype": "success", "is_error": False, "num_turns": 2, "duration_ms": 3000,
         "total_cost_usd": 0.5, "result": "Report written to r.md"},
    ]
    log.write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n", encoding="utf-8")
    text = render_stream_json(log)
    for want in ("session s1", "Looking.", "→ Bash: ls", "← a ⏎ b", "2 turns", "$0.50", "Report written", "not json"):
        assert want in text


def test_git_trail_from_commit_events(env):
    (env.repo / "c.txt").write_text("c\n", encoding="utf-8")
    git(env.repo, "add", "c.txt")
    git(env.repo, "commit", "-q", "-m", "add c")
    sha = git(env.repo, "rev-parse", "HEAD").strip()
    ev = model.Event(1, "2026-01-01T17:25:00+00:00", "commit", "code/005_c", "code", [], {"sha": sha, "scope": "code"})
    text = item_model.git_trail(env.cfg, "code/005_c", [ev])
    assert "1 commit(s) logged" in text and "c.txt" in text and "add c" in text


def test_trail_of_a_cancelled_item_has_its_prompt(env):
    # A red hold first, so the item cannot launch before the cancel is read (under load
    # a tick could start it, and cancel never kills an executor).
    env.queue("001_bad", "FAKE report red")
    env.tick_until(lambda: env.finished("code/001_bad"))
    env.queue("002_x", "FAKE report done UNIQUE-PROMPT", stage="false")
    env.signal("c.signal", "cancel: code/002_x")
    env.tick_until(lambda: env.events("cancel"))
    p = Project(env.control)
    p.poll_events()
    tr = p.trail("code/002_x")
    assert tr.where == "cancelled" and "UNIQUE-PROMPT" in tr.prompt
    p.close()


def test_activity_view_hides_mechanics(env):
    log = env.d.log
    for kind, detail in (("enqueue", {}), ("worktree", {"action": "created"}), ("start", {"pid": 1}),
                         ("merge", {"sha": "abc"}), ("commit", {"sha": "abc", "scope": "code"}),
                         ("finish", {"status": "partial", "carry": "x moves"}), ("hold", {"reason": "r"}),
                         ("reviewed", {"by": "planner"}), ("red", {"reasons": ["boom"]})):
        log.append(kind, item="code/004_x", **detail)
    log.append("warning", note="something")
    log.append("request", request="r1", message="state: v0.1\n\nbody", targets=["state.md"])
    p = Project(env.control)
    p.poll_events()
    shown = p.filter_events(advanced=False)
    assert "daemon_start" in {e.kind for e in shown}
    kinds = [e.kind for e in shown if e.kind != "daemon_start"]
    assert kinds == ["start", "finish", "reviewed", "red", "warning", "request"]
    assert [e.kind for e in p.filter_events(advanced=False, item="004")] == ["start", "finish", "reviewed", "red"]
    assert len(p.filter_events()) > len(shown)
    text = {e.kind: model.activity_text(e) for e in shown}
    assert text["start"] == "prompt 004_x started"
    assert text["finish"] == "report 004_x written — partial (carry: x moves)"
    assert text["reviewed"] == "report 004_x reviewed (by planner)"
    assert text["request"] == "doc update committed: state: v0.1"
    p.close()
