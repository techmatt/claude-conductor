"""Prompt 84: planner notes of 2026-11-05 to 11-07 (aicomplete, fractals). A request target
the bridge lands again restarts the identical-to-HEAD wait; requests can carry edits as
old/new line blocks; `progress` and `wait_for` returns carry the clock and the time left to
the cap; `wait_for` with `fresh` skips lines already read; executors don't get conductor's
venv on PATH; generated docs are no trim due; `session.md` lists the repos' paths; a commit
resting on an open design default and audit notes needing a decision show on the report's
line; the taskbar shows running work."""

import io
import os
import re
import sys
import threading
import time
from datetime import timedelta

import pytest

from conductor import ask_server, audits, inbox, procs, questions, request, session, state, waitfor
from conductor.app import status
from conductor.util import now, read_text, stamp
from test_app_title import snap
from test_lean import record
from test_preamble import SHORT, render


def _request(env, name: str, files: dict[str, str | bytes], age: float = 0.0):
    d = env.control / "requests" / name
    d.mkdir(parents=True)
    for rel, text in files.items():
        p = d / rel
        p.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
        if age and rel == "request.md":
            os.utime(p, (time.time() - age, time.time() - age))
    return d


# ------------------------------------------------------------------ the request race
def test_a_target_landing_after_request_md_restarts_the_identical_wait(env, monkeypatch):
    """The race (six times across two projects): request.md was written over 30s ago, the
    target's copy lands now still identical to HEAD. The wait runs from the newest write in
    the folder, so the changed copy landing next is applied, not lost as `identical`."""
    monkeypatch.setattr(request, "SAME_WAIT_SECONDS", 30.0)
    head = (env.control / "state.md").read_bytes()
    d = _request(env, "20261107-0944-process005", {"state.md": head,
                                                   "request.md": "---\ntargets: [state.md]\nmessage: s\n---\n"},
                 age=45)
    waiting = request.ready(d, env.control)
    assert isinstance(waiting, request.Waiting) and "identical to HEAD so far" in waiting.why
    env.d.tick()
    assert d.is_dir() and not env.events("request")
    (d / "state.md").write_text("# state\n\nprocess 005\n", encoding="utf-8")     # the late copy
    env.d.tick()
    [ev] = env.events("request")
    assert not ev.detail["no_change"] and "identical" not in ev.detail
    assert read_text(env.control / "state.md") == "# state\n\nprocess 005\n"


def test_a_settled_identical_copy_still_applies_as_identical(env, monkeypatch):
    monkeypatch.setattr(request, "SAME_WAIT_SECONDS", 30.0)
    head = (env.control / "state.md").read_bytes()
    d = _request(env, "r1", {"state.md": head, "request.md": "---\ntargets: [state.md]\nmessage: s\n---\n"}, age=45)
    old = time.time() - 40
    os.utime(d / "state.md", (old, old))
    got = request.ready(d, env.control)
    assert isinstance(got, request.Request) and got.identical


# ------------------------------------------------------------------ edit blocks
EDIT = "<<<<<<< state.md\n{old}=======\n{new}>>>>>>>\n"


def _edit(old: str, new: str) -> str:
    return EDIT.format(old=old, new=new)


def test_a_request_can_carry_edits_instead_of_the_whole_file(env):
    (env.control / "state.md").write_text("# state\n\n## Queued\n- 004\n\n## Ruled\n- warm\n", encoding="utf-8")
    env.d.snapshot = None
    body = ("---\ntargets: [state.md]\nmessage: \"state: review 005\"\n---\n"
            + _edit("- 004\n", "- 004\n- 005\n") + _edit("- warm\n", ""))
    _request(env, "20261107-1010-edits", {"request.md": body})
    env.d.tick()
    [ev] = env.events("request")
    assert ev.detail["targets"] == ["state.md"] and not ev.detail["no_change"]
    assert read_text(env.control / "state.md") == "# state\n\n## Queued\n- 004\n- 005\n\n## Ruled\n"
    assert env.events("commit")[-1].detail["subject"] == "state: review 005"


@pytest.mark.parametrize("state_text, why", [
    ("# state\n", "edit 1 for state.md: its old lines are not found"),
    ("- 004\n- 004\n", "edit 1 for state.md: its old lines are found 2 times"),
])
def test_an_edit_that_misses_refuses_the_whole_request(env, state_text, why):
    (env.control / "state.md").write_text(state_text, encoding="utf-8")
    env.d.snapshot = None
    _request(env, "r2", {"request.md": "---\ntargets: [state.md]\nmessage: m\n---\n" + _edit("- 004\n", "- 005\n")})
    env.d.tick()
    [failed] = env.events("request_failed")
    assert why in failed.detail["error"] and "nothing was applied" in failed.detail["error"]
    assert read_text(env.control / "state.md") == state_text


def test_edit_blocks_are_checked_against_the_targets():
    with pytest.raises(request.RequestError, match="needs a `=======` line"):
        request.parse_edits("<<<<<<< state.md\n- a\n")
    with pytest.raises(request.RequestError, match="no old lines"):
        request.parse_edits("<<<<<<< state.md\n=======\n- b\n>>>>>>>\n")
    edits, rest = request.parse_edits("why\n" + _edit("- a\n- b\n", "- c\n"))
    assert edits == {"state.md": [("- a\n- b\n", "- c\n")]} and rest.strip() == "why"


def test_a_target_sent_both_ways_or_unlisted_is_refused(env):
    _request(env, "both", {"state.md": "x\n", "request.md": "---\ntargets: [state.md]\nmessage: m\n---\n"
                                                         + _edit("x\n", "y\n")})
    _request(env, "stray", {"request.md": "---\ntargets: [docs/plan.md]\nmessage: m\n---\n" + _edit("x\n", "y\n")})
    env.d.tick()
    errors = sorted(e.detail["error"] for e in env.events("request_failed"))
    assert any("sent both as a file and as edits: ['state.md']" in e for e in errors)
    assert any("edits for files not listed as targets: ['state.md']" in e for e in errors)


# ------------------------------------------------------------------ the progress clock
def _server(env, item: str = "code/001_a") -> ask_server.Server:
    return ask_server.Server(env.control, item, 10, None, out=io.StringIO(), workdir=env.control)


def test_progress_returns_the_clock_and_the_time_left_to_the_cap(env):
    """130 ran ~1.5h ahead of the clock telling time by turns; 017 ~45 min, locking early."""
    record(env, "001_a", phase="exec", deadline=stamp(now() + timedelta(hours=3, minutes=17, seconds=30)))
    text, err = _server(env).progress({"note": "rendering"})
    assert not err and re.fullmatch(r"Logged; carry on\. Now \d{1,2}:\d\d[ap]m, 3h17m left to the cap\.", text)
    record(env, "001_a", phase="exec", deadline=stamp(now() - timedelta(minutes=5)))
    assert "past the cap" in _server(env).progress({"note": "x"})[0]
    record(env, "001_a", phase="exec", deadline=stamp(now() + timedelta(hours=1)), paused_at=stamp())
    assert "(paused: the cap moves on)" in _server(env).progress({"note": "x"})[0]


def test_wait_for_returns_carry_the_clock_too(env):
    record(env, "001_a", phase="exec", deadline=stamp(now() + timedelta(minutes=50, seconds=30)))
    (env.control / "marker").write_text("x", encoding="utf-8")
    text, _ = _server(env).wait_for({"path": "marker"}, threading.Event())
    assert "marker exists (after 0s). Now " in text and text.endswith(", 50m left to the cap.")


def test_the_preamble_estimates_in_round_trips_but_never_tells_time_by_them(env):
    full = " ".join(render(env, SHORT * 3).split())
    assert "Estimate remaining work in round trips (~15 s)" in full
    assert "never tell time by them: `progress` and `wait_for` returns give the clock and time left" in full
    assert "20–40 s" not in full and "run `date` before every" not in full


# ------------------------------------------------------------------ wait_for fresh
def _log_wait(server, log: str, fresh: bool) -> str:
    return server.wait_for({"log": log, "line": "RUN_DONE", "fresh": fresh, "minutes": 0.01, "every": 0.05},
                           threading.Event())[0]


def test_a_fresh_log_wait_skips_a_relaunched_jobs_old_done_line(env):
    """130: a relaunched job's stale RUN_DONE ended three waits at once."""
    record(env, "001_a", phase="exec")
    log = env.control / "run.log"
    log.write_text("start\nRUN_DONE\n", encoding="utf-8")
    server = _server(env)
    assert "has the line: RUN_DONE" in _log_wait(server, "run.log", fresh=False)    # lines already there count
    assert _log_wait(server, "run.log", fresh=True).startswith("Nothing yet")       # read already: skipped
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("relaunch\nRUN_DONE\n")
    assert "has the line: RUN_DONE" in _log_wait(server, "run.log", fresh=True)
    assert _log_wait(server, "run.log", fresh=True).startswith("Nothing yet")
    # First fresh call on a log no wait has read: only what comes after it.
    (env.control / "other.log").write_text("RUN_DONE\n", encoding="utf-8")
    assert _log_wait(_server(env), "other.log", fresh=True).startswith("Nothing yet")


def test_a_log_rewritten_shorter_is_read_from_the_top(tmp_path):
    log = tmp_path / "x.log"
    log.write_text("a much longer first run\nof lines\n", encoding="utf-8")
    t = waitfor.LogTarget(log, re.compile("RUN_DONE"))
    assert t.check() is None and t.consumed == log.stat().st_size
    log.write_text("RUN_DONE\n", encoding="utf-8")
    assert "RUN_DONE" in t.check()
    assert waitfor.fresh_start(log, {log: 10_000}) == 0 and waitfor.fresh_start(log, {}) == log.stat().st_size


# ------------------------------------------------------------------ executor PATH
def test_executors_do_not_get_conductors_venv(tmp_path, monkeypatch):
    """fractals 005: plain `python` was conductor's venv (no Pillow, ruff), so `builder check`
    skipped four checks."""
    venv = tmp_path / "venv"
    (venv / "Scripts").mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(venv))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "python312"))
    other = str(tmp_path / "miniconda")
    monkeypatch.setenv("PATH", os.pathsep.join([str(venv / "Scripts"), other]))
    monkeypatch.setenv("VIRTUAL_ENV", str(venv))
    env = procs.child_env()
    path = next(v for k, v in env.items() if k.upper() == "PATH")
    assert path.split(os.pathsep) == [other] and "VIRTUAL_ENV" not in env


# ------------------------------------------------------------------ docs and digests
def test_a_generated_doc_is_no_trim_due(tmp_path):
    """aicomplete's `docs/cards.md`, generated by `cards_doc.py`, was marked due (133 asked)."""
    gen, plain = tmp_path / "cards.md", tmp_path / "rules.md"
    gen.write_text("# The player cards\n\nGenerated by `python scripts/cards/cards_doc.py` from "
                   "`content/cards.json`: do not\nedit by hand.\n" + "x" * 20_000, encoding="utf-8")
    plain.write_text("# Rules\n\nThe generator card makes energy.\n", encoding="utf-8")
    assert session.is_generated(gen) and not session.is_generated(plain)


def test_session_md_lists_each_repos_path(env):
    """fractals' start asked for `C:\\Code\\fractals`, which doesn't exist: one missing path
    fails the whole access request."""
    env.signal("planner-1.signal", "by: planner\nsession: start")
    env.tick_until(lambda: env.events("checkpoint"))
    text = read_text(env.control / "session.md")
    assert f"Repos: `{env.d.cfg.repos['code']}`" in text


def test_a_commit_on_an_open_design_default_and_decision_notes_show_on_its_line(env):
    q = questions.create(env.control, "code/017_x", "Lace or solid?", ["lace", "solid"], "lace", False, 10,
                         design=True)
    env.d.log.append("finish", item="code/017_x", status="done", carry="none", audit=True)
    notes = audits.note_path(env.d.cfg, "code", "017_x")
    notes.parent.mkdir(parents=True, exist_ok=True)
    notes.write_text("- Poorly structured: nothing to note.\n- Needs a decision:\n"
                     "  - split render.py or keep it whole (pick: split)\n"
                     "- Docs that would have saved time: the README's run line.\n", encoding="utf-8")
    text = inbox.render(env.d.cfg, env.d.log, state.scan(env.d.cfg, env.d.log))
    line = next(ln for ln in text.splitlines() if ln.startswith("- code/017_x **done**"))
    assert f"**{inbox.DESIGN_DEFAULT_NOTE}: `{q.id}`**" in line
    assert "**1 audit note(s) need a decision**: split render.py or keep it whole (pick: split)" in line
    questions.resolve(q, "answered", "lace", "server")
    text = inbox.render(env.d.cfg, env.d.log, state.scan(env.d.cfg, env.d.log))
    assert inbox.DESIGN_DEFAULT_NOTE not in text


@pytest.mark.parametrize("notes, want", [
    ("## Needs a decision\n\n- keep A or B (pick A)\n- one more\n\n## Docs that would have saved time\n- x\n",
     ["keep A or B (pick A)", "one more"]),
    ("**Needs a decision**: Nothing to note.\n- Slower than expected: none\n", []),
    ("- Refactor or clean up: tidy a.py\n", []),
])
def test_decision_notes_are_read_however_the_heading_is_marked(tmp_path, notes, want):
    p = tmp_path / "n.audit.md"
    p.write_text(notes, encoding="utf-8")
    assert audits.decisions(p) == want


# ------------------------------------------------------------------ the taskbar
def test_the_taskbar_shows_running_work_whatever_else_holds():
    """fractals 033 ran under "● Conductor · pick · 1 to review" and a plain taskbar icon."""
    st = status.summarize([snap(running=1, picks=1, unreviewed=1)])
    assert st.title() == "● Conductor · pick · 1 running · 1 to review" and st.badge == "need"
    alone = status.summarize([snap(running=2)])
    assert alone.badge is None and alone.title(0) == "◐ Conductor · 2 running"    # 85: the title says it
