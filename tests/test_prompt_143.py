"""Prompt 143: what the fractals and aicomplete sessions found after 142: a `[rules]` parked
edit no item carried (`test_parked.py`), executor memory notes with no route, `edit_state`'s
whole-line `old` (`test_planner_mcp.py`), API reds with no report, small read and status
fixes, the preamble's thread budget, the `quiet` lock and prompt warnings."""

import json
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mcp_client import call_planner  # noqa: E402

from conductor import daemon_api, memory_notes, planner_mcp, planner_read, quiet, state, talk  # noqa: E402
from conductor.events import EventLog  # noqa: E402
from conductor.util import read_text, split_front_matter  # noqa: E402
from test_planner_mcp import alive  # noqa: E402


def call(env, tool: str, args: dict) -> tuple[str, bool]:
    box = {}
    t = threading.Thread(target=lambda: box.update(r=call_planner(tool, {"project": "test", **args}, [env.control])))
    t.start()
    end = time.time() + 90
    while t.is_alive() and time.time() < end:
        env.d.tick()
        time.sleep(0.05)
    t.join(5)
    return box["r"]


def test_the_planner_changes_an_executor_memory_note_through_the_daemon(env):
    mem = memory_notes.memory_dir(env.repo)
    assert mem.parts[-3:] == ("projects", memory_notes.folder_name(env.repo), "memory")
    assert memory_notes.folder_name(Path(r"C:\Code\fractal-wallpapers")) == "C--Code-fractal-wallpapers"
    alive(env)
    text, err = call(env, "memory_note", {"repo": "code", "note": "reports-to-drive", "op": "write",
                                          "body": "---\nname: reports-to-drive\n---\n\nCopy each report to Drive.\n"})
    assert text.startswith("REFUSED: a new note needs `description`"), text
    text, err = call(env, "memory_note", {"repo": "code", "note": "reports-to-drive", "op": "write",
                                          "description": "copy each report to Drive",
                                          "body": "---\nname: reports-to-drive\n---\n\nCopy each report to Drive.\n"})
    assert not err and text.startswith("accepted: code memory note reports-to-drive: written; MEMORY.md line set"), text
    assert read_text(mem / "MEMORY.md") == "- [Reports to drive](reports-to-drive.md) — copy each report to Drive\n"
    text, err = call(env, "memory_note", {"repo": "code", "note": "reports-to-drive", "op": "edit",
                                          "edits": [{"old": "e", "new": "E"}]})
    assert text.startswith("REFUSED: edit 1: `old` occurs") and "times in the note" in text, text
    text, err = call(env, "memory_note", {"repo": "code", "note": "reports-to-drive", "op": "edit",
                                          "edits": [{"old": "to Drive.", "new": "to Drive, same name."}]})
    assert not err and "1 edit(s)" in text, text
    assert read_text(mem / "reports-to-drive.md").endswith("Copy each report to Drive, same name.\n")
    [ev] = env.events("memory_note")[1:]
    assert ev.detail["via"] == "mcp" and ev.detail["op"] == "edit" and ev.repo == "code"
    text, err = call(env, "memory_note", {"repo": "code", "note": "reports-to-drive", "op": "delete"})
    assert not err and "deleted; MEMORY.md line dropped" in text, text
    assert not (mem / "reports-to-drive.md").exists() and read_text(mem / "MEMORY.md") == ""
    assert not list((env.control / memory_notes.ROOT).glob("*.md"))      # each request deleted once applied


def test_queue_item_and_amend_warn_what_the_executor_cannot_do():
    assert "WARNING: this asks the executor to write under ~/.claude" in planner_mcp.warn(
        r"Delete the last-line rule from C:\Users\me\.claude\projects\x\memory\note.md")
    assert "memory_note" in planner_mcp.warn("update the memory note about reports")
    meta = {"repo": "code", "readonly": True}
    assert "add `remote: true`" in planner_mcp.warn("Read 2000 seeds with farm-seeds on the remote workers.", meta)
    assert planner_mcp.warn("Read 2000 seeds with farm-seeds.", meta | {"remote": True}) == ""
    assert planner_mcp.warn("Read the tiles.", meta) == ""


def test_status_leaves_conductors_own_queue_writes_out(env):
    from conductor.app import actions
    actions.write_signal(env.control, "session: start", by="planner")
    env.tick_until(lambda: env.d.log.last("checkpoint", has="session"))
    env.d.log.append("retro", item="code/002_post_audit_001", paths=["queue/code/002_post_audit_001.md"])
    env.d.log.append("enqueue", item="code/002_post_audit_001", template="self_audit")
    env.d.log.append("enqueue", item="code/003_mine")
    head, lines = planner_read.session_writes(env.d.log)
    assert head.startswith("planner writes since session") and ": 1 (" in head
    assert [ln.split(" ", 1)[1] for ln in lines] == ["queued code/003_mine (bridge)"]


def test_a_missing_report_says_why_and_resume_prompts_never_make_a_name_ambiguous(env):
    reports = env.control / "reports"
    reports.mkdir(exist_ok=True)
    for n in (1, 2, 3):
        (reports / f"code__297_sweep.api{n}.md").write_text("resume\n", encoding="utf-8")
    (reports / "code__297_sweep.prompt.md").write_text("prompt\n", encoding="utf-8")
    env.d.log.append("finish", item="code/297_sweep", repo="code", status="red",
                     reasons=["API error (rate_limit_error); 3 retries spent"])
    text, err = planner_read.read(env.cfg, "report", "297")
    assert err and text == "no report for code/297_sweep: missing; red: API error (rate_limit_error); 3 retries spent"
    (reports / "code__297_sweep.md").write_text("status: red\nblocks: x\ncarry: none\n\n## A\n\nx\n", encoding="utf-8")
    text, err = planner_read.read(env.cfg, "report", "297")
    assert not err and text.startswith("status: red"), text


def test_an_api_red_queues_no_retro_and_leaves_a_stub_report(env, monkeypatch):
    assert daemon_api.api_only(["API error (rate_limit_error); 3 retries spent",
                                "uncommitted changes left in the worktree (not merged)"])
    assert not daemon_api.api_only(["uncommitted changes left in the worktree (not merged)"])
    assert daemon_api.api_only(["launch failed: x"])
    report = env.control / "reports" / "code__297_sweep.md"
    report.parent.mkdir(exist_ok=True)
    env.d.log.append("progress", item="code/297_sweep", note="sweeping the troop lines",
                     outline=[{"title": "Troops", "done": True}, {"title": "Report", "eta": "4:10am"}])
    rec = {"item": "code/297_sweep", "name": "297_sweep", "report": str(report), "branch": "conductor/297_sweep",
           "api_exhausted": "API error (rate_limit_error); 3 retries spent"}
    commits = [("a" * 40, "t", "WIP: troops"), ("b" * 40, "t", "WIP: cards")]
    assert daemon_api.stub_report(env.d.log, rec, commits, dirty=True)
    text = read_text(report)
    assert text.startswith("status: red\nblocks: code/297_sweep until it is retried or redone\ncarry: none\n")
    assert "aaaaaaaa WIP: troops; bbbbbbbb WIP: cards" in text and "sweeping the troop lines" in text
    assert "outline 1/2 · next: Report by 4:10am" in text and "kept, with uncommitted changes" in text
    assert not daemon_api.stub_report(env.d.log, rec, commits, dirty=False)        # never over a report


def test_retries_spent_leave_a_stub_report_from_the_daemon(env):
    env.d.cfg.api_retry_minutes = [0.0]
    env.d.cfg.api_retry_night_minutes = 0.0         # run in the night shift's hours, it waited 90 min (145)
    env.queue("001_down", "FAKE commit a.txt one\nFAKE api-error 5")
    env.tick_until(lambda: env.finished("code/001_down"))
    fin = env.finished("code/001_down")
    assert fin.detail["status"] == "red" and daemon_api.api_only(fin.detail["reasons"])
    text = read_text(env.control / "reports" / "code__001_down.md")
    assert text.startswith("status: red\n") and "stub report, written by conductor" in text
    assert "retry: code/001_down" in text


def test_the_last_api_retry_waits_longer_at_night(env, monkeypatch):
    from conductor import nightshift
    from conductor.util import parse_stamp
    assert env.cfg.api_retry_night_minutes == 90.0
    from conductor.util import now
    monkeypatch.setattr(nightshift, "night", lambda cfg, at=None: (now() - timedelta(hours=1), now() + timedelta(hours=8)))
    env.d.cfg.api_retry_minutes = [2.0]             # one try: the last, so at night 90 minutes
    env.queue("001_cut", "FAKE api-error 5")
    env.tick_until(lambda: env.events("api_retry"))
    [ev] = env.events("api_retry")
    assert 89 <= (parse_stamp(ev.detail["at"]) - parse_stamp(ev.ts)).total_seconds() / 60 <= 91


def test_a_quiet_item_holds_the_box_and_a_stale_holder_holds_nothing(env, tmp_path):
    other = tmp_path / "other-control"
    (other / "running" / "app").mkdir(parents=True)
    rec = other / "running" / "app" / "060_timing.run.json"
    rec.write_text(json.dumps({"item": "app/060_timing", "phase": "exec"}), encoding="utf-8")
    quiet.claim(other, "fractals", "app/060_timing")
    env.queue("001_a", "FAKE ok")
    view = state.scan(env.cfg, env.d.log)
    d = state.decide(env.cfg, view)["code"]
    assert d.action == "wait" and d.reason.startswith("quiet: fractals's app/060_timing holds the box"), d
    assert quiet.blocks(env.control, "code/W1_watch", "watch") is None         # a watch only reads
    rec.write_text(json.dumps({"item": "app/060_timing", "phase": "merge"}), encoding="utf-8")
    assert quiet.holder() is None                                               # finished: holds nothing
    rec.unlink()
    assert state.decide(env.cfg, state.scan(env.cfg, env.d.log))["code"].action == "run"


def test_a_quiet_item_claims_the_box_at_launch_and_lets_it_go_at_its_finish(env):
    env.queue("001_timing", env.hold(), locks="[quiet]")
    env.tick_until(lambda: env.events("start"))
    assert quiet.holder()["item"] == "code/001_timing"
    env.release()
    env.tick_until(lambda: env.finished("code/001_timing"))
    assert not quiet.path().exists()


def test_a_small_pytest_call_stays_in_the_foreground():
    globs = ["*-m pytest*", "*pytest*"]
    ev = {"tool_name": "Bash", "tool_input": {"command": "uv run python -m pytest tests/test_tiles.py -q"}}
    assert talk.pre_tool(ev, 60, globs) == ev["tool_input"] | {"timeout": 60000}
    ev["tool_input"]["command"] = "uv run python -m pytest tests/test_tiles.py --slow"
    assert talk.pre_tool(ev, 60, globs)["run_in_background"] is True
    ev["tool_input"]["command"] = "uv run python -m pytest -q"
    assert talk.pre_tool(ev, 60, globs)["run_in_background"] is True
