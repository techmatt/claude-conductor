"""`conductor mcp`, the planner's server (docs/mcp.md), driven by tests/mcp_client.py as the
desktop app would drive it. A call runs on a thread while the test ticks the daemon."""

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git  # noqa: E402
from mcp_client import Client, call_planner, call_status, planner_command  # noqa: E402

from conductor import planner_read  # noqa: E402
from conductor.util import read_text, stamp  # noqa: E402


def alive(env) -> None:
    """A test's daemon takes no lock; this process stands in as the live daemon."""
    (env.cfg.state_dir / "daemon.json").write_text(
        json.dumps({"pid": os.getpid(), "started": stamp(), "beat": stamp()}), encoding="utf-8")


def call(env, tool: str, args: dict) -> tuple[str, bool]:
    """`tool` through the server while this thread ticks the daemon, as a live one would."""
    box = {}
    t = threading.Thread(target=lambda: box.update(r=call_planner(tool, {"project": "test", **args}, [env.control])))
    t.start()
    end = time.time() + 90
    while t.is_alive() and time.time() < end:
        env.d.tick()
        time.sleep(0.05)
    t.join(5)
    return box["r"]


def test_status_for_a_live_project_names_the_running_item_and_its_progress(env):
    env.queue("001_long", env.hold())
    env.queue("002_next", "FAKE ok")
    env.tick_until(lambda: env.events("start"))
    env.d.log.append("progress", item="code/001_long", note="halfway through the tiles", eta="5:40pm")
    # 138: an outline-only update after it leaves the noted line showing (aicomplete 288 showed none)
    env.d.log.append("progress", item="code/001_long", outline=[{"title": "tiles", "eta": "5:30pm"}])
    alive(env)
    text, is_error = call_status("test", [env.control])
    env.release()
    assert not is_error
    lines = text.splitlines()
    assert lines[0].startswith("test — daemon alive (pid ") and "heartbeat" in lines[0] and "last event #" in lines[0]
    assert lines[1] == "code:"
    assert lines[2].startswith("  running 001_long ")
    assert "progress" in lines[3] and "halfway through the tiles (ETA 5:40pm)" in lines[3]
    assert lines[4].startswith("    outline 0/1 · next: tiles by 5:30pm")
    assert lines[5].startswith("  queued 002_next")
    assert "proposed: none" in text and len(text) <= 1500


def test_status_says_plainly_a_daemon_is_down(env):
    (env.cfg.state_dir / "daemon.json").write_text(
        json.dumps({"pid": 999999, "started": stamp(), "beat": stamp()}), encoding="utf-8")
    text, is_error = call_status("test", [env.control])
    assert not is_error
    assert text.startswith("test — daemon DOWN: it died (pid 999999") and "Nothing launches" in text
    listing, _ = call_status(None, [env.control])
    assert listing.startswith("test: daemon DOWN, 0 running, 0 queued")
    other, is_error = call_status("nope", [env.control])
    assert other == "no project 'nope'; known: test"


def test_a_start_returns_the_rules_and_the_digest_in_one_call(env):
    alive(env)
    text, is_error = call(env, "start_session", {})
    assert not is_error
    rules, digest = text.split("\n\n=====\n\n")
    assert rules.startswith("# planner") and "## Vocab" in read_text(planner_read.ROOT / "planner.md")
    assert digest.startswith("# Test session 1") and "signals/planner-" in digest
    assert env.events("checkpoint")[-1].detail.get("by") == "planner"


def test_an_item_queued_returns_its_start_and_a_rewrite_what_changed(env):
    alive(env)
    text, is_error = call(env, "queue_item", {"repo": "code", "name": "fix_menu", "body": "do it",
                                             "meta": {"depends_on": "000_first"}})
    assert not is_error, text
    assert text.startswith("accepted: queued as code/001_fix_menu; held: unknown dependency 000_first")
    path = env.control / "queue" / "code" / "001_fix_menu.md"
    assert read_text(path) == "---\nrepo: code\ndepends_on: 000_first\n---\ndo it\n"
    text, _ = call(env, "queue_item", {"repo": "code", "name": "fix_menu", "body": "do it differently",
                                      "meta": {"depends_on": "000_first"}})
    assert text.startswith("accepted: rewritten: it runs the new text (body +1 −1 line(s))"), text
    text, is_error = call(env, "queue_item", {"repo": "code", "name": "later", "body": "x", "held": True})
    assert text.startswith("accepted: proposed for code: waits for Matt") and (env.control / "proposed" / "002_later.md").is_file()


def test_a_refused_signal_returns_its_reason_and_the_accepted_form(env):
    alive(env)
    text, is_error = call(env, "signal", {"lines": ["cancel: code"]})
    assert not is_error and text.startswith("REFUSED: ") and "; use `cancel: " in text


@pytest.mark.slow
def test_an_edit_state_miss_returns_the_current_lines_and_the_retry_applies(env):
    (env.control / "state.md").write_text("# state\n\n## Queued and running\n\n- 004 queued\n- 005 queued\n",
                                          encoding="utf-8", newline="\n")
    git(env.control, "commit", "-qam", "state")
    alive(env)
    text, is_error = call(env, "edit_state", {"file": "state.md", "edits": [{"old": "- 004 queud", "new": "- 004 done"}]})
    assert is_error and "edit 1 for state.md: its old lines are not found" in text and "Nothing was written" in text
    assert "   5| - 004 queued" in text
    assert not list((env.control / "requests").glob("*")) if (env.control / "requests").is_dir() else True
    text, is_error = call(env, "edit_state", {"file": "state.md", "edits": [{"old": "- 004 queued", "new": "- 004 done"}],
                                              "message": "state: 004 done"})
    assert not is_error and text.startswith("accepted: committed "), text
    assert "- 004 done\n- 005 queued" in read_text(env.control / "state.md")
    assert git(env.control, "log", "-1", "--format=%s").strip() == "state: 004 done"
    text, is_error = call(env, "edit_state", {"file": "habits.md", "content": "# test — planner habits\n\n- be brief\n"})
    assert text.startswith("accepted: committed "), text
    assert "- be brief" in read_text(env.control / "session.md") if (env.control / "session.md").exists() else True


def test_a_report_reads_header_first_then_one_section(env):
    (env.control / "reports").mkdir(exist_ok=True)
    (env.control / "reports" / "code__007_menus.md").write_text(
        "status: done\nblocks: none\ncarry: none\n\n# 007: menus\n\n## Fixed\n\n- the back button\n\n"
        "## Deferred\n\n- the font (Matt's call)\n", encoding="utf-8")
    text, is_error = call(env, "read", {"what": "report", "name": "7"})
    assert not is_error and text.startswith("status: done\nblocks: none\ncarry: none\n# 007: menus\n\nSections:\n- Fixed (")
    assert "- Deferred (" in text and "the back button" not in text
    text, _ = call(env, "read", {"what": "report", "name": "code/007_menus", "section": "deferred"})
    assert text == "## Deferred\n\n- the font (Matt's call)"


def test_with_the_daemon_stopped_a_write_says_so_and_writes_nothing(env):
    text, is_error = call(env, "queue_item", {"repo": "code", "name": "fix_menu", "body": "do it"})
    assert is_error and text.startswith("test's daemon is DOWN: it is stopped. Start it: ") and text.endswith("Nothing was written.")
    assert not list((env.control / "queue" / "code").glob("*.md"))
    text, is_error = call(env, "signal", {"lines": ["pause: code"]})
    assert is_error and "DOWN" in text and not list((env.control / "signals").glob("planner-*"))
    text, is_error = call(env, "read", {"what": "state"})          # a read still works
    assert not is_error and text == "# state"


@pytest.mark.slow
def test_amend_answer_and_park_return_their_receipts(env):
    from test_ask import ask_line
    env.queue("001_ask", ask_line(question="Which store?", options=["sqlite", "postgres"], block=True))
    env.tick_until(lambda: env.events("ask"))
    alive(env)
    text, is_error = call(env, "amend", {"item": "1", "text": "Also: say hello."})
    assert not is_error and text.startswith("accepted: amendment #") and "(by planner)" in text, text
    text, is_error = call(env, "answer", {"question": "001_ask__1", "text": "sqlite"})
    assert not is_error and text.startswith("accepted: answered: the executor has it"), text
    text, is_error = call(env, "park", {"repo": "code", "line": "README: say hello"})
    assert text.startswith("accepted: 1 parked edit(s) waiting for code"), text
    assert read_text(env.control / "parked" / "code.md") == "- README: say hello\n"
    env.tick_until(lambda: env.finished("code/001_ask"))


def test_notes_reach_conductors_own_files_and_nothing_else(tmp_path, monkeypatch):
    from conductor import planner_mcp
    monkeypatch.setattr(planner_read, "ROOT", tmp_path)
    (tmp_path / "planner-notes.md").write_text("# Planner notes\n\nLast report reviewed by the planner: 136_x\n\n"
                                                "## For HANDOFF Open\n", encoding="utf-8")
    server = planner_mcp.PlannerServer([])
    assert server.call("note", {"kind": "reviewed_mark", "text": "137_y"}) == \
        "planner-notes.md: Last report reviewed by the planner: 137_y"
    server.call("note", {"kind": "planner_note", "text": "a dead daemon toasts"})
    assert read_text(tmp_path / "planner-notes.md").endswith(
        "Last report reviewed by the planner: 137_y\n\n## For HANDOFF Open\n\n- a dead daemon toasts\n")
    text = server.call("note", {"kind": "conductor_prompt", "project": "fractals", "name": "toast_dead", "body": "# x\n"})
    [p] = (tmp_path / "prompts" / "inbox").glob("*-fractals-toast_dead.md")
    assert text == f"wrote prompts/inbox/{p.name} (no receipt: Matt runs it with \"run next prompt\")"
    with pytest.raises(planner_mcp.Refused):
        server.call("note", {"kind": "prompt_support", "name": "../evil.md", "body": "x"})


def test_a_conductor_report_reads_header_first_with_what_is_past_the_mark(tmp_path, monkeypatch):
    from conductor import planner_mcp
    monkeypatch.setattr(planner_read, "ROOT", tmp_path)
    (tmp_path / "reports").mkdir()
    (tmp_path / "planner-notes.md").write_text("Last report reviewed by the planner: 139_stats\n", encoding="utf-8")
    for name in ("139_stats", "140_mcp_gaps"):
        (tmp_path / "reports" / f"{name}_report.md").write_text(
            f"status: done\n\n# {name}\n\n## Findings\n\n- one\n\n## Decisions\n\n- two\n", encoding="utf-8")
    server = planner_mcp.PlannerServer([])
    text = server.call("read", {"what": "conductor-report", "name": "140"})
    assert text.startswith("status: done\n# 140_mcp_gaps\n\nSections:\n- Findings (") and "- one" not in text
    assert text.endswith("Past the mark (139_stats): 140_mcp_gaps")
    assert server.call("read", {"what": "conductor-report", "name": "140", "section": "decisions"}).startswith(
        "## Decisions\n\n- two")
    assert server.call("read", {"what": "conductor-report"}).startswith("Past the mark (139_stats): 140_mcp_gaps")
    with pytest.raises(planner_mcp.Refused, match="no conductor report '141'; newest: "):
        server.call("read", {"what": "conductor-report", "name": "141"})


def test_a_repo_file_reads_by_path_and_the_rest_is_refused_with_the_reason(env):
    (env.repo / "docs").mkdir()
    (env.repo / "docs" / "rulings.md").write_text("# Rulings\n\n## Fonts\n\n- serif\n", encoding="utf-8")
    (env.repo / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0")
    (env.repo / ".gitignore").write_text("out/\n", encoding="utf-8")
    git(env.repo, "add", "-A")
    git(env.repo, "commit", "-qm", "docs")
    (env.repo / "out").mkdir()
    (env.repo / "out" / "log.txt").write_text("noise\n", encoding="utf-8")
    (env.repo / "scratch.md").write_text("mine\n", encoding="utf-8")
    c = Client(command=planner_command([env.control]))
    try:
        c.start()

        def read(name, **kw):
            return c.call("read", {"project": "test", "what": "repo", "name": name, **kw})
        assert read("code/docs/rulings.md") == ("# Rulings\n\n## Fonts\n\n- serif", False)
        assert read("code/docs/rulings.md", section="fonts") == ("## Fonts\n\n- serif", False)
        assert read("code") == ("code/ (tracked):\n.gitignore\nREADME.md\ndocs/\nshot.png", False)
        text, err = read("code/../control/state.md")
        assert err and "is outside code" in text
        text, err = read("code/out/log.txt")
        assert err and text == "code/out/log.txt is git-ignored: only tracked files read"
        text, err = read("code/scratch.md")
        assert err and text == "code/scratch.md is untracked: only tracked files read"
        text, err = read("code/shot.png")
        assert err and text.startswith("code/shot.png is an image: the planner looks at it itself, over the file bridge")
    finally:
        c.close()


@pytest.mark.slow
def test_a_section_moves_to_a_new_file_in_one_call_and_a_miss_names_the_sections(env):
    (env.control / "state.md").write_text("# state\n\n## Now\n\n- 004 queued\n\n## Paged\n\n- a long\n- record\n\n"
                                          "## Planner habits\n\n- be brief\n", encoding="utf-8", newline="\n")
    git(env.control, "commit", "-qam", "state")
    alive(env)
    text, is_error = call(env, "edit_state", {"file": "state.md", "sections": [{"op": "move", "section": "pag"}]})
    assert is_error and "must be a relative path" in text      # no `to`: refused, nothing written
    text, is_error = call(env, "edit_state", {"file": "state.md", "sections": [
        {"op": "move", "section": "paged", "to": "paged.md"}, {"op": "move", "section": "Planner habits", "to": "habits.md"}]})
    assert not is_error and text.startswith("accepted: committed "), text
    assert read_text(env.control / "state.md") == "# state\n\n## Now\n\n- 004 queued\n\n"
    assert read_text(env.control / "paged.md") == "## Paged\n\n- a long\n- record\n"
    assert read_text(env.control / "habits.md") == "## Planner habits\n\n- be brief\n"
    assert git(env.control, "log", "-1", "--format=%s").strip() == \
        "state: move *Paged* to paged.md; move *Planner habits* to habits.md"
    assert git(env.control, "status", "--porcelain").strip() == ""
    text, is_error = call(env, "edit_state", {"file": "state.md", "sections": [{"op": "delete", "section": "Later"}]})
    assert is_error and text == ("section op 1: no `## ` section 'Later' in state.md; nothing was written. "
                                 "Its sections: Now")
    text, is_error = call(env, "edit_state", {"file": "state.md", "sections": [
        {"op": "replace", "section": "now", "body": "- 004 done"}]})
    assert not is_error and read_text(env.control / "state.md") == "# state\n\n## Now\n\n- 004 done\n\n"


@pytest.mark.slow
def test_each_server_write_carries_the_mark_and_stats_counts_it_beside_a_bridge_write(env):
    from test_ask import ask_line

    from conductor import stats
    alive(env)
    assert not call(env, "start_session", {})[1]
    text, is_error = call(env, "queue_item", {"repo": "code", "name": "ask", "body": ask_line(
        question="Which store?", options=["sqlite", "postgres"], block=True)})
    assert not is_error, text
    env.tick_until(lambda: env.events("ask"))
    assert not call(env, "amend", {"item": "1", "text": "Also: say hello."})[1]
    assert not call(env, "answer", {"question": "001_ask__1", "text": "sqlite"})[1]
    assert not call(env, "signal", {"lines": ["checkpoint: by the server"]})[1]
    env.signal("planner-20261119-120000.signal", "by: planner\ncheckpoint: over the bridge\n")
    env.tick_until(lambda: len(env.events("checkpoint")) == 3)
    vias = {e.kind if e.kind != "checkpoint" else e.detail.get("label"): e.detail.get("via")
            for e in env.d.log.query(kinds=["checkpoint", "enqueue", "amended", "answer"])}
    assert vias == {"start": "mcp", "enqueue": "mcp", "amended": "mcp", "answer": "mcp",
                    "by the server": "mcp", "over the bridge": None}, vias
    receipt = read_text(env.control / "receipts" / "queue" / "code" / "001_ask.md")
    assert 'via: "mcp"' in receipt and not list((env.control / "receipts" / ".mcp").iterdir())
    [s] = stats.sessions(env.d.log.query(), stats.week_of(stats.now()), set())
    # mcp: the session's own signal, the item, the amendment, the answer and the checkpoint signal
    assert (s.counts["mcp"], s.counts["bridge"]) == (5, 1), s.counts
    row = next(ln for ln in stats.report(env.d.log, "test", 1).splitlines() if ln.startswith("  #"))
    assert row.split()[-2:] == [str(s.counts["mcp"]), "1"]
    env.release()
    env.tick_until(lambda: env.finished("code/001_ask"))


@pytest.mark.slow
def test_a_tool_changed_on_disk_is_live_at_the_next_call_with_no_relaunch(env, tmp_path):
    import shutil
    copy = tmp_path / "copy" / "src" / "conductor"
    shutil.copytree(Path(planner_read.__file__).parent, copy, ignore=shutil.ignore_patterns("__pycache__", "tests"))
    c = Client(command=planner_command([env.control]), env={**os.environ, "PYTHONPATH": str(copy.parent)})
    try:
        c.start()
        before, _ = c.call("status", {})
        assert before.startswith("test: daemon DOWN")
        target = copy / "planner_read.py"
        target.write_text(read_text(target).replace('return f"{cfg.project}: {daemon}, ',
                                                    'return f"CHANGED {cfg.project}: {daemon}, '), encoding="utf-8")
        after, _ = c.call("status", {})
        assert after.startswith("CHANGED test: daemon DOWN"), after
        assert c.proc.poll() is None                # the same launcher all along
    finally:
        c.close()


@pytest.mark.slow
def test_the_selftest_prints_a_passing_line_for_every_tool(env):
    import io

    from conductor import mcp_launcher, planner_mcp
    alive(env)
    out, box = io.StringIO(), {}
    t = threading.Thread(target=lambda: box.update(code=mcp_launcher.selftest(env.control, out)))
    t.start()
    end = time.time() + 120
    while t.is_alive() and time.time() < end:
        env.d.tick()
        time.sleep(0.05)
    t.join(5)
    lines = out.getvalue().splitlines()
    assert box["code"] == 0, out.getvalue()
    assert [ln.split()[1] for ln in lines[:-1]] == planner_mcp.TOOL_NAMES
    assert all(ln.startswith("ok ") for ln in lines[:-1]) and lines[-1].startswith("10 of 10 ok")


def test_reads_are_capped_and_paged():
    text = "".join(f"line {i:05d} " + "x" * 90 + "\n" for i in range(200))     # ~20 KB
    pieces = planner_read.parts(text)
    assert len(pieces) == 3 and all(len(p.encode()) <= planner_read.CAP for p in pieces) and "".join(pieces) == text
    first = planner_read.paged(text, 1, 'read(what="inbox", part={part})')
    assert first.endswith('[part 1 of 3; next: read(what="inbox", part=2)]')
    assert planner_read.paged(text, 3, "x").endswith("[part 3 of 3; that was the last]")
