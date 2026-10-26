"""The desktop app: its Qt-free model and actions, plus an offscreen Qt smoke test."""

import json
import os
import sqlite3
import subprocess
import sys

import psutil
import pytest
from conductor import questions
from conductor.app import actions, model, theme
from conductor.app.model import Project, ReadOnlyLog, render_stream_json
from conftest import CONTROL_GITIGNORE, git, init_repo


def bare_control(tmp_path):
    """A control folder no daemon has ever run in: no events.db, no runtime folders."""
    init_repo(tmp_path / "code", {"README.md": "code\n"})
    control = tmp_path / "control"
    init_repo(control, {"conductor.toml": 'project = "bare"\n\n[repos]\ncode = "../code"\n',
                        ".gitignore": CONTROL_GITIGNORE})
    return control


# ---------------------------------------------------------------- read-only log
def test_missing_events_db_stays_missing(tmp_path):
    control = bare_control(tmp_path)
    p = Project(control)
    assert p.poll_events() == []
    snap = p.snapshot()
    assert snap.state == "idle" and not snap.daemon.alive
    assert p.trail("code/001_x").events == []
    assert p.log.read_mark() == 0 and p.log.max_id() == 0
    p.close()
    assert not (control / "events.db").exists()
    assert not list(control.glob("events.db*"))


def test_read_only_log_cannot_write_and_sees_new_events(env):
    env.d.log.append("warning", note="one")
    ro = ReadOnlyLog(env.cfg.events_db)
    assert [e.detail["note"] for e in ro.query(kinds=["warning"])][-1] == "one"
    with pytest.raises(sqlite3.OperationalError):
        ro.conn.execute("INSERT INTO events (ts, kind) VALUES ('x', 'y')")
    env.d.log.append("warning", note="two")
    assert ro.last("warning").detail["note"] == "two"
    ro.close()


def test_poll_events_is_incremental(env):
    p = Project(env.control)
    first = p.poll_events()
    assert first and p.last_id == first[-1].id
    assert p.poll_events() == []
    ev = env.d.log.append("warning", note="later")
    assert [e.id for e in p.poll_events()] == [ev.id]
    p.close()


# ---------------------------------------------------------------- snapshot
def test_snapshot_states(env):
    p = Project(env.control)
    assert p.snapshot().state == "idle"
    env.queue("001_x", "FAKE sleep 0")
    snap = p.snapshot()
    assert [i.name for i in snap.repos[0].queued] == ["001_x"]
    assert snap.state == "red" and "daemon down" in snap.why      # queued work, no daemon
    q = questions.create(env.control, "code/001_x", "Which?", ["a", "b"], "a", False, 5)
    env.d.log.append("red", item="code/000_y", reasons=["boom"], after_red="stop")
    snap = p.snapshot()
    assert snap.repos[0].red is not None and [x.id for x in snap.open_questions] == [q.id]
    assert snap.state == "red"
    assert model.overall(["idle", "asking", "running"]) == "asking"
    assert model.overall([]) == "idle"
    assert Project._state(model.DaemonInfo(True), [model.RepoState("code", env.repo)], [q]) == ("asking", "1 open question(s)")
    p.close()


def test_proposed_scan(env):
    d = env.control / "proposed"
    d.mkdir(exist_ok=True)
    (d / "002_b.md").write_text("---\nrepo: code\n---\nDo b.\n", encoding="utf-8")
    (d / "001_a.md").write_text("---\nrepo: nope\n---\nDo a.\n", encoding="utf-8")
    (d / "003_c.md").write_text("no front matter\n", encoding="utf-8")
    got = {x.name: x for x in model.scan_proposed(env.cfg)}
    assert list(got) == ["001_a", "002_b", "003_c"]
    assert got["002_b"].errors == [] and got["002_b"].summary == "Do b."
    assert any("nope" in e for e in got["001_a"].errors)
    assert got["003_c"].errors


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


# ---------------------------------------------------------------- a finished item's trail
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
    text = model.git_trail(p.cfg, "code/001_x", p.events)
    assert "merged into main" in text and "a.txt" in text
    p.close()


def test_git_trail_prefers_the_branch(env):
    git(env.repo, "checkout", "-q", "-b", "conductor/002_y")
    (env.repo / "y.txt").write_text("y\n", encoding="utf-8")
    git(env.repo, "add", "y.txt")
    git(env.repo, "commit", "-q", "-m", "add y")
    git(env.repo, "checkout", "-q", "main")
    text = model.git_trail(env.cfg, "code/002_y", [])
    assert "still exists" in text and "add y" in text and "y.txt" in text
    assert "nothing committed survives" in model.git_trail(env.cfg, "code/003_z", [])


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


# ---------------------------------------------------------------- actions
def test_signal_files(tmp_path):
    a = actions.approve(tmp_path, "001_x")
    b = actions.cancel(tmp_path, "code/002_y")
    assert a != b and a.suffix == b.suffix == ".signal" and a.name.startswith("app-")
    assert a.read_bytes() == b"by: app\napprove: 001_x\n"
    assert not list((tmp_path / "signals").glob("*.tmp"))
    names = ["001_a", "002_b", "003_c"]
    down = actions.swap(tmp_path, "proposed", names, 0, 1)
    assert down.read_text(encoding="utf-8").splitlines()[1] == "reorder: proposed 002_b 001_a"
    up = actions.swap(tmp_path, "code", names, 2, -1)
    assert up.read_text(encoding="utf-8").splitlines()[1] == "reorder: code 003_c 002_b"
    assert actions.swap(tmp_path, "code", names, 0, -1) is None
    assert actions.stop_daemon(tmp_path).read_text(encoding="utf-8").endswith("stop: daemon\n")
    assert actions.clear(tmp_path, "code").read_text(encoding="utf-8").endswith("clear: code\n")
    assert actions.pick(tmp_path, "code/picks/m.json").read_bytes() == b"by: app\npick: code/picks/m.json\n"
    assert actions.reviewed(tmp_path, "code/001_x", by="cli").read_bytes() == b"by: cli\nreviewed: code/001_x\n"


def test_app_signals_land_as_daemon_events(env):
    """End to end: the app's own writer, the daemon's parser, and the app's read-only view."""
    def propose(name: str) -> None:
        (env.control / "proposed" / f"{name}.md").write_text(f"---\nrepo: code\n---\nFAKE report done {name}\n",
                                                            encoding="utf-8")

    def take(path) -> None:
        env.tick_until(lambda: not path.exists())
        assert not path.with_name(path.name + ".bad").exists(), env.events("invalid")[-1].detail

    env.queue("001_bad", "FAKE report red")                      # a red holds `code`
    env.tick_until(lambda: env.finished("code/001_bad"))
    for name in ("002_p", "003_q", "004_r"):
        propose(name)
    take(actions.approve(env.control, "002_p"))
    assert (env.control / "queue" / "code" / "002_p.md").is_file()
    take(actions.swap(env.control, "proposed", ["003_q", "004_r"], 0, 1))
    assert sorted(p.stem for p in (env.control / "proposed").glob("*.md")) == ["003_r", "004_q"]
    take(actions.cancel(env.control, "proposed/003_r"))
    take(actions.cancel(env.control, "code/002_p"))
    take(actions.clear(env.control, "code"))
    take(actions.stop_daemon(env.control))
    assert env.d.stop_requested and env.d.stop_requested["by"] == "app"

    [ap] = env.events("approve")
    assert (ap.item, ap.detail["by"]) == ("code/002_p", "app")
    [ro] = env.events("reorder")
    assert (ro.repo, ro.detail["before"], ro.detail["after"], ro.detail["by"]) == \
        ("proposed", ["003_q", "004_r"], ["003_r", "004_q"], "app")
    c1, c2 = env.events("cancel")
    assert (c1.item, c1.detail["from"], c1.detail["by"]) == ("proposed/003_r", "proposed", "app")
    assert (c2.item, c2.detail["from"], c2.detail["by"]) == ("code/002_p", "queue", "app")
    assert env.events("clear")[-1].detail["by"] == "app"
    assert not env.events("invalid")
    # The app's read-only model sees every one of them.
    p = Project(env.control)
    seen = {e.kind for e in p.poll_events()}
    p.close()
    assert {"approve", "reorder", "cancel", "clear"} <= seen


def test_answer_writes_the_same_file_as_the_cli(tmp_path):
    q = questions.create(tmp_path, "code/001_x", "Name?", [], "a", False, 5)
    actions.answer(tmp_path, "001__1", "  b  ")
    # The same file, plus a `by:` line the executor never sees (read_answer drops it).
    assert q.answer_path.read_text(encoding="utf-8") == "---\nby: app\n---\nb\n"
    assert questions.read_answer(q, settle=0) == "b" and questions.answer_by(q) == "app"
    with pytest.raises(actions.AnswerError):
        actions.answer(tmp_path, q.id, "again")
    with pytest.raises(actions.AnswerError):
        actions.answer(tmp_path, "nope__9", "x")


def test_start_daemon_detached(tmp_path):
    import time

    from conductor import procs
    control = bare_control(tmp_path)
    p = actions.start_daemon(control)
    try:
        end = time.time() + 60
        while time.time() < end and not model.daemon_info(Project(control).cfg).alive:
            assert p.poll() is None, (control / ".conductor" / "daemon.out.log").read_text(errors="replace")
            time.sleep(0.2)
        info = model.daemon_info(Project(control).cfg)
        # A venv's python.exe on Windows is a launcher that runs the real interpreter.
        family = {p.pid} | {c.pid for c in psutil.Process(p.pid).children(recursive=True)}
        assert info.alive and info.pid in family
        # Unbuffered: the daemon's echo reaches the log while it runs (found live: it
        # stayed empty, block-buffered, until the daemon exited).
        out = control / ".conductor" / "daemon.out.log"
        while time.time() < end and "daemon started" not in out.read_text(errors="replace"):
            time.sleep(0.2)
        assert "daemon started" in out.read_text(errors="replace")
    finally:
        procs.kill_tree(p.pid)


# ---------------------------------------------------------------- review fixes
def test_signal_names_sort_in_click_order(tmp_path):
    paths = [actions.clear(tmp_path, "code") for _ in range(12)]
    assert sorted(paths) == paths                     # the daemon applies them in name order


def test_a_failed_read_is_no_change_and_a_replaced_log_resets(env, monkeypatch):
    p = Project(env.control)
    n = len(p.poll_events())
    monkeypatch.setattr(p.log, "top", lambda: None)   # busy for a moment
    assert p.poll_events() == [] and len(p.events) == n and p.generation == 0
    monkeypatch.setattr(p.log, "top", lambda: 0)      # fewer events than seen: a new log
    assert len(p.poll_events()) == n and len(p.events) == n and p.generation == 1
    p.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows paths")
def test_sqlite_uri_keeps_unc_and_drive_paths_in_the_path():
    # SQLite rejects any URI authority but localhost: a UNC server goes in the path.
    assert model.sqlite_uri(r"\\nas\work\proj control\events.db") == \
        "file:////nas/work/proj%20control/events.db?mode=ro"
    assert model.sqlite_uri(r"Z:\proj\events.db") == "file:///Z:/proj/events.db?mode=ro"


def test_git_trail_from_commit_events(env):
    (env.repo / "c.txt").write_text("c\n", encoding="utf-8")
    git(env.repo, "add", "c.txt")
    git(env.repo, "commit", "-q", "-m", "add c")
    sha = git(env.repo, "rev-parse", "HEAD").strip()
    ev = model.Event(1, "2026-01-01T17:25:00+00:00", "commit", "code/005_c", "code", [], {"sha": sha, "scope": "code"})
    text = model.git_trail(env.cfg, "code/005_c", [ev])
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


@pytest.mark.slow
def test_an_app_answer_is_logged_by_app(env):
    """Found proving phase 4 live: the app's answer event had no `by`, unlike every
    signal it writes. The executor still gets the bare answer text."""
    # The suite's 1.8s default can expire before the answer lands under full-suite load
    # (then the answer is logged `late`): give this question a minute.
    toml = env.control / "conductor.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace("ask_default_minutes = 0.03", "ask_default_minutes = 1"),
                    encoding="utf-8", newline="\n")
    env.configure('[repos]\ncode = "../code"\n')
    env.queue("001_ask", 'FAKE ask {"question": "Which?", "default": "a"}')
    env.tick_until(lambda: questions.scan(env.control))
    [q] = questions.scan(env.control)
    actions.answer(env.control, q.id, "b")
    env.tick_until(lambda: env.finished("code/001_ask"))
    [ans] = env.events("answer")
    assert ans.detail["by"] == "app" and ans.detail["text"] == "b"
    text = (env.control / "reports" / "code__001_ask.md").read_text(encoding="utf-8")
    assert "Answer to code__001_ask__1:\nb\n" in text and "by: app" not in text


def test_a_late_answer_is_reported_late(tmp_path):
    q = questions.create(tmp_path, "code/001_x", "Name?", [], "a", False, 5)
    questions.resolve(q, "default", "a", "server")
    late = actions.answer(tmp_path, q.id, "b")
    assert not late.is_open and "late" in actions.answer_note(late)
    q2 = questions.create(tmp_path, "code/002_y", "Name?", [], "a", False, 5)
    assert actions.answer_note(actions.answer(tmp_path, q2.id, "b")).startswith("answered")


def test_question_scan_is_cached_but_sees_changes(env):
    p = Project(env.control)
    q = questions.create(env.control, "code/001_x", "Q?", [], "a", False, 5)
    assert [x.is_open for x in p.scan_questions()] == [True]
    questions.resolve(q, "default", "a", "server")
    assert [x.is_open for x in p.scan_questions()] == [False]
    p.close()


def test_console_python_under_pythonw(tmp_path, monkeypatch):
    from conductor import procs
    for name in ("python.exe", "pythonw.exe"):
        (tmp_path / name).write_bytes(b"")
    monkeypatch.setattr(procs.sys, "executable", str(tmp_path / "pythonw.exe"))
    want = "python.exe" if procs.WINDOWS else "pythonw.exe"
    assert procs.console_python() == str(tmp_path / want)


# ---------------------------------------------------------------- Qt smoke test
def _settings(tmp_path):
    from PySide6.QtCore import QSettings
    return QSettings(str(tmp_path / "app.ini"), QSettings.Format.IniFormat)


@pytest.mark.slow
def test_window_renders_every_panel(qapp, env, tmp_path):
    from conductor.app.window import MainWindow
    env.queue("001_x", "FAKE commit a.txt hello")
    env.tick_until(lambda: env.finished("code/001_x"))
    env.queue("002_y", "FAKE sleep 0")
    env.d.log.append("red", item="code/001_x", reasons=["boom"], after_red="stop")
    questions.create(env.control, "code/002_y", "Which?", ["a", "b"], "a", False, 5)
    (env.control / "proposed" / "003_z.md").write_text("---\nrepo: code\n---\nDo z.\n", encoding="utf-8")
    bare = bare_control(tmp_path / "b")
    win = MainWindow([env.control, bare], settings=_settings(tmp_path), tray=False, interval_ms=60_000)
    win.show()
    win.refresh()
    assert len(win.views) == 2
    view = win.views[env.control.resolve()]
    assert view.snap.state == "red"
    for name, panel in view.panels.items():
        view.tabs.setCurrentWidget(panel)
        assert not panel.grab().isNull(), name
    assert view.events_panel.model.rowCount() > 0
    assert len(view.questions_panel.cards) == 1
    assert view.proposed_panel.table.rowCount() == 1
    view.open_item("code/001_x")
    trail = view.events_panel.trail
    trail.tabs.setCurrentIndex(trail.TABS.index("Git"))
    trail.wait_git()                                  # git runs off the UI thread
    assert "a.txt" in trail.text["Git"].toPlainText()
    # The focus lands on the ▶ line even after an event whose text spans lines.
    env.d.log.append("answer", item="code/001_x", question="q1", text="line one\nline two\nline three")
    last = env.d.log.append("warning", item="code/001_x", note="after")
    win.refresh()
    trail.tabs.setCurrentIndex(0)
    trail.show_event(last)
    focus = trail.text["Events"].textCursor().block()
    assert focus.text().startswith("▶")
    # Wrapped lines hang under the `what` column: a header's own wrap, and the extra
    # lines of a multi-line answer, start where its text does.
    column = focus.blockFormat().leftMargin()
    assert column > 0 and focus.blockFormat().textIndent() == -column
    doc = trail.text["Events"].document()
    two = next(b for b in (doc.findBlockByNumber(n) for n in range(doc.blockCount())) if b.text() == "line two")
    assert two.blockFormat().leftMargin() > 0 and two.blockFormat().textIndent() == 0
    detail = focus.next()
    assert detail.text().startswith(trail.INDENT) and detail.blockFormat().textIndent() == -detail.blockFormat().leftMargin() < 0
    view.events_panel.reds.setChecked(True)
    assert {e.kind for e in view.events_panel.model.rows} <= {"red", "invalid", "tamper", "kill",
                                                               "conflict", "paths_violation", "request_failed"}
    # Remembered across launches; the bare project never got an events.db.
    win2 = MainWindow([], settings=_settings(tmp_path), tray=False, interval_ms=60_000)
    assert set(win2.views) == {env.control.resolve(), bare.resolve()}
    for w in (win, win2):
        w._quitting = True
        w.close()
    assert not (bare / "events.db").exists()


def test_memory_line_shows_only_when_it_needs_matt(qapp, env, tmp_path, monkeypatch):
    from conductor.app import model
    from conductor.app.window import MainWindow
    level = ["ok"]
    monkeypatch.setattr(model.memory, "line", lambda cfg: (f"memory 1.0 of 2.0 GiB · {level[0]}", level[0]))
    win = MainWindow([env.control], settings=_settings(tmp_path), tray=False, interval_ms=60_000)
    view = win.views[env.control.resolve()]
    for lv, colour in (("ok", None), ("pressure", theme.AMBER), ("critical", theme.RED), ("ok", None)):
        level[0] = lv
        win.refresh()
        assert view.memory_label.isHidden() == (colour is None), lv
        assert (colour in view.memory_label.text() and lv in view.memory_label.text()) if colour \
            else view.memory_label.text() == ""
    win._quitting = True
    win.close()


def test_a_staged_head_is_one_repos_row(qapp, tmp_path):
    from types import SimpleNamespace

    from conductor.app.repos_panel import ReposPanel
    from conductor.items import Item

    def plan(**kw):
        head, nxt = (Item("code", n, tmp_path / f"{n}.md") for n in ("002_b", "003_c"))
        stage = model.Activity("code/002_b", "002_b", "staged", None)
        rs = model.RepoState("code", tmp_path, staged=[stage], queued=[head, nxt], **kw)
        [(_, head, _, rows)] = ReposPanel._plan(None, SimpleNamespace(repos=[rs], picks={}, checkpoint=None))
        return head, [(key, texts[0] if key[0] == "group" else texts[1], parent) for key, texts, _, _, parent in rows]

    # Once, as its queue row (so it keeps Cancel), its state naming both facts; in flight
    # with the queued item after it, each counted once in the summary.
    flight = ("group", "code", "flight")
    assert plan(action="wait", reason="repo busy") == ("1 staged · 1 queued", [
        (flight, "In flight (2)", None),
        (("queued", "code/002_b"), "staged · next", flight),
        (("queued", "code/003_c"), "queued", flight)])
    head, held = plan(action="hold", reason="lock busy")
    assert head == "held · 1 staged · 1 queued" and held[0][0][0] == "hold" and held[0][2] is None
    assert held[2:] == [(("queued", "code/002_b"), "staged · blocked by hold", flight),
                        (("queued", "code/003_c"), "queued", flight)]
    run = model.Activity("code/001_a", "001_a", "running", None)
    rs = model.RepoState("code", tmp_path, running=[run], queued=[Item("code", "002_b", tmp_path / "b.md")],
                         finished=[model.Finished("code/000_z", "000_z", "done", "2026-10-19T10:00:00-07:00", False)],
                         finished_total=1)
    [(_, head, _, rows)] = ReposPanel._plan(None, SimpleNamespace(repos=[rs], picks={}, checkpoint=None))
    assert head == "1 running · 1 queued · 1 unreviewed"
    assert [(k, t[0], t[1], p) for k, t, _, _, p in rows] == [
        (flight, "In flight (2)", "", None),
        (("run", "code/001_a"), "001_a", "running", flight),
        (("queued", "code/002_b"), "002_b", "queued · next", flight),
        (("group", "code", "finished"), "Finished (1 unreviewed)", "", None),
        (("done", "code/000_z"), "000_z", "done · unreviewed", ("group", "code", "finished"))]
    # A readonly item running beside another: its state says so, its note names its port,
    # and the summary counts both as running.
    ro = model.Activity("code/002_ro", "002_ro", "running", None, readonly=True, port=51234)
    rs = model.RepoState("code", tmp_path, running=[run, ro])
    [(_, head, _, rows)] = ReposPanel._plan(None, SimpleNamespace(repos=[rs], picks={}, checkpoint=None))
    assert head == "2 running"
    assert [(t[1], t[3]) for k, t, _, _, _ in rows if k[0] == "run"] == [("running", ""),
                                                                        ("running · readonly", "port 51234")]
    empty = model.RepoState("code", tmp_path)
    assert ReposPanel._plan(None, SimpleNamespace(repos=[empty], picks={}, checkpoint=None)) == [
        ("code", "idle", str(tmp_path), [])]


def test_a_remembered_project_that_fails_to_load_stays_remembered(qapp, env, tmp_path):
    from conductor.app.window import MainWindow
    settings = _settings(tmp_path)
    missing = (tmp_path / "unmounted").resolve()
    settings.setValue("controls", [str(missing), str(env.control)])
    win = MainWindow([], settings=settings, tray=False, interval_ms=60_000)
    assert set(win.views) == {env.control.resolve()}
    win._remember()
    assert str(missing) in win.remembered() and str(env.control.resolve()) in win.remembered()
    win._quitting = True
    win.close()


# ---------------------------------------------------------------- reviewed reports, Activity
def test_finished_items_keep_every_unreviewed_one(env):
    from conductor import state
    log = env.d.log
    for n in range(1, 14):
        log.append("finish", item=f"code/{n:03d}_x", status="done" if n % 2 else "partial", carry="none")
    for n in range(3, 14):
        log.append("reviewed", item=f"code/{n:03d}_x", status="done")
    got, total = model.finished_items(state.scan(env.cfg, log), "code")
    # The ten newest (013 … 004), then the older unreviewed ones (002, 001); 003 is reviewed.
    assert total == 13
    assert [f.name for f in got] == [f"{n:03d}_x" for n in range(13, 3, -1)] + ["002_x", "001_x"]
    assert [f.name for f in got if not f.reviewed] == ["002_x", "001_x"]
    p = Project(env.control)
    [rs] = p.snapshot().repos
    assert rs.unreviewed == 2 and rs.finished_total == 13
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


def test_mark_reviewed_from_the_repos_tab(qapp, env, tmp_path):
    from conductor.app.window import MainWindow
    env.queue("001_x", "FAKE report done")
    env.tick_until(lambda: env.finished("code/001_x"))
    win = MainWindow([env.control], settings=_settings(tmp_path), tray=False, interval_ms=60_000)
    view = win.views[env.control.resolve()]
    win.refresh()
    assert view.tabs.tabText(0) == "Repos (1 unreviewed)"
    panel = view.repos_panel
    row = panel._rows[("done", "code/001_x")]
    assert row.text(1) == "done · unreviewed"
    assert row.foreground(0).color().name() == theme.AMBER          # unreviewed: needs Matt
    button = panel.tree.itemWidget(row, 4).button
    button.click()
    assert not button.isEnabled()
    env.tick_until(lambda: env.events("reviewed"))
    assert env.events("reviewed")[0].detail["by"] == "app"
    win.refresh()
    assert view.tabs.tabText(0) == "Repos"
    row = panel._rows[("done", "code/001_x")]
    assert row.text(1) == "done · reviewed" and set(panel.tree.itemWidget(row, 4).buttons) == {"Terminal"}
    assert row.foreground(0).color().name() != theme.AMBER          # reviewed: no longer needs Matt
    # Events: Activity by default; Advanced shows every kind.
    ev = view.events_panel
    assert not ev.advanced.isChecked()
    activity = {e.kind for e in ev.model.rows}
    assert "worktree" not in activity and {"start", "finish", "reviewed"} <= activity
    ev.advanced.setChecked(True)
    assert "worktree" in {e.kind for e in ev.model.rows}
    win._quitting = True
    win.close()


def test_running_row_progress_tell_and_open_in_terminal(qapp, env, tmp_path, monkeypatch):
    from conductor import procs
    from conductor.app.window import MainWindow
    from PySide6.QtCore import QUrl
    from conductor.app.widgets import MessageDialog
    env.queue("001_x", 'FAKE progress {"note": "reading", "eta": "5m"}\nFAKE until-message 30')
    env.tick_until(lambda: env.events("progress"))
    win = MainWindow([env.control], settings=_settings(tmp_path), tray=False, interval_ms=60_000)
    view = win.views[env.control.resolve()]
    win.refresh()
    repos = view.repos_panel
    row = repos._rows[("run", "code/001_x")]
    assert row.text(3).startswith("reading (ETA 5m) · ")
    # Nothing selected: the detail follows the running item, Conversation first.
    detail, chat = repos.detail, repos.detail.conversation
    assert detail.item == "code/001_x" and detail.tabs.currentWidget() is chat
    assert chat.tell_btn.isEnabled() and chat.amend_btn.isEnabled()
    assert [t.title for t in chat.turns] == ["Started", "Progress"]
    tell = repos.tree.itemWidget(row, 4).button
    assert tell.text() == "Tell…"
    monkeypatch.setattr(MessageDialog, "exec", lambda d: (d.box.setPlainText("finish now\nplease"), 1)[1])
    chat.tell_btn.click()                                   # the same signal as the row's Tell…
    env.tick_until(lambda: env.finished("code/001_x"))
    assert env.events("tell")[0].detail["text"] == "finish now / please"
    assert env.events("delivered")
    win.refresh()
    holder = repos.tree.itemWidget(repos._rows[("done", "code/001_x")], 4)
    assert set(holder.buttons) == {"Mark reviewed", "Terminal"}
    # The reply sits under its tell; a finished item takes neither Tell nor Amend.
    [msg] = [t for t in chat.turns if t.title.startswith("Message")]
    assert msg.under[-1][0] == "reply" and "got it: finish now / please" in msg.under[-1][2]
    assert "executor replied" in chat.view.toPlainText() and chat.turns[-1].report
    assert not chat.tell_btn.isEnabled() and not chat.amend_btn.isEnabled()
    chat.view.anchorClicked.emit(QUrl("report"))
    assert detail.tabs.currentWidget() is detail.text["Report"]
    # A selection survives a rebuild (Mark reviewed changes the row's look).
    repos.tree.setCurrentItem(repos._rows[("done", "code/001_x")])
    assert repos.selected == "code/001_x"
    holder.buttons["Mark reviewed"].click()
    env.tick_until(lambda: env.events("reviewed"))
    win.refresh()
    assert repos.tree.currentItem() is repos._rows[("done", "code/001_x")] and detail.item == "code/001_x"
    holder = repos.tree.itemWidget(repos._rows[("done", "code/001_x")], 4)
    opened = []
    monkeypatch.setattr(procs, "open_console", lambda argv, cwd: opened.append((argv, cwd)))
    holder.buttons["Terminal"].click()
    sid = env.finished("code/001_x").detail["session_id"]
    assert opened and opened[0][0][-2:] == ["--resume", sid]
    view.open_item("code/001_x")
    trail = view.events_panel.trail
    assert trail.open_btn.isEnabled() and trail.session_id() == sid
    trail.show_event(env.events("daemon_start")[0])         # not an item: nothing to open
    assert not trail.open_btn.isEnabled()
    win._quitting = True
    win.close()


def test_outline_pinned_and_requested_from_the_conversation(qapp, env, tmp_path):
    from conductor.app.window import MainWindow
    outline = [{"title": "Read", "eta": "10:40am", "done": True}, {"title": "Re-cut images", "eta": "11:05am"},
               {"title": "Report", "eta": "11:20am"}]
    env.queue("001_o", f"FAKE progress {json.dumps({'outline': outline})}\n"
                       f"FAKE reply-with 30 {json.dumps({'outline': outline[:2] + [dict(outline[2], eta='11:30am')]})}")
    env.tick_until(lambda: env.events("progress"))
    win = MainWindow([env.control], settings=_settings(tmp_path), tray=False, interval_ms=60_000)
    view = win.views[env.control.resolve()]
    win.refresh()
    chat = view.repos_panel.detail.conversation
    assert chat.outline_btn.isEnabled() and chat.pin.isVisibleTo(chat)
    pin = chat.pin.text()
    assert "1/3 done" in pin and "✓" in pin and "<b>Re-cut images</b>" in pin and "· next" in pin
    assert pin.index("Re-cut images") < pin.index("Report") and "11:20am" in pin
    chat.outline_btn.click()
    [sig] = (env.control / "signals").glob("app-*.signal")
    assert sig.read_text(encoding="utf-8") == "by: app\noutline: code/001_o\n"
    env.tick_until(lambda: env.finished("code/001_o"))
    win.refresh()
    assert "11:30am" in chat.pin.text() and "11:20am" not in chat.pin.text()
    assert not chat.outline_btn.isEnabled()
    [req] = [t for t in chat.turns if t.title.startswith("Outline request")]
    assert req.under[-1][0] == "reply" and "finish ~11:30am" in req.under[-1][2]
    win._quitting = True
    win.close()


@pytest.mark.slow
def test_a_checkpoint_in_the_app(qapp, env, tmp_path):
    from conductor.app.window import MainWindow
    env.queue("001_old", "FAKE report done")
    env.queue("002_new", "FAKE report done")
    env.tick_until(lambda: env.finished("code/002_new"))
    env.d.log.append("reviewed", item="code/001_old", status="done")
    win = MainWindow([env.control], settings=_settings(tmp_path), tray=False, interval_ms=60_000)
    view = win.views[env.control.resolve()]
    win.refresh()
    ev = view.events_panel
    assert ev.current.isChecked() and ev.current.text() == "since checkpoint (none yet)"
    everything = len(ev.model.rows)
    view.new_checkpoint("session start")
    [sig] = (env.control / "signals").glob("app-*.signal")
    assert sig.read_text(encoding="utf-8") == "by: app\ncheckpoint: session start\n"
    env.tick_until(lambda: env.events("checkpoint"))
    [cp] = env.events("checkpoint")
    win.refresh()
    # Events: from the checkpoint on by default (its own line and the warning for 002).
    assert ev.current.text() == f"since checkpoint #{cp.id}"
    assert [e.kind for e in ev.model.rows] == ["checkpoint", "warning"]
    assert model.activity_text(ev.model.rows[0]) == f'new checkpoint #{cp.id} "session start" (by app)'
    ev.current.setChecked(False)
    assert len(ev.model.rows) == everything + 2
    # Repos: 002 (unreviewed) is current; 001 sits in the collapsed Earlier group.
    panel = view.repos_panel
    group = panel._rows[("group", "code", "earlier")]
    finished = panel._rows[("group", "code", "finished")]
    assert ("done", "code/002_new") in panel._rows and ("done", "code/001_old") not in panel._rows
    assert panel._rows[("old", "code/001_old")].parent() is group and not group.isExpanded()
    assert panel._rows[("done", "code/002_new")].parent() is finished and finished.isExpanded()
    assert group.text(0) == "Earlier checkpoints (1)" and group.text(1) == ""
    assert finished.text(0) == "Finished since checkpoint (1 unreviewed)"
    group.setExpanded(True)
    finished.setExpanded(False)
    env.d.log.append("reviewed", item="code/002_new", status="done")
    win.refresh()                                      # a rebuild keeps each group as it was left
    assert panel._rows[("group", "code", "earlier")].isExpanded()
    assert not panel._rows[("group", "code", "finished")].isExpanded()
    assert panel._rows[("group", "code", "finished")].text(0) == "Finished since checkpoint (1)"
    assert f"Current: checkpoint #{cp.id}" in view.checkpoint_btn.toolTip()
    win._quitting = True
    win.close()


def test_run_buttons_in_the_header(qapp, env, tmp_path, monkeypatch):
    from conductor.app.window import MainWindow
    toml = env.control / "conductor.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + '\n[run.game]\nrepo = "code"\ncommand = "x"\n'
                    'url = "http://127.0.0.1:9/"\n', encoding="utf-8", newline="\n")
    win = MainWindow([env.control], settings=_settings(tmp_path), tray=False, interval_ms=60_000)
    view = win.views[env.control.resolve()]
    win.refresh()
    b = view.run_buttons["game"]
    assert b.text() == "Run game ● down" and view.snap.targets == {"game": "down"}
    assert view.stop_buttons["game"].isHidden()          # Stop shows only while it is up
    b.click()
    [sig] = (env.control / "signals").glob("app-*.signal")
    assert sig.read_text(encoding="utf-8") == "by: app\nrun: game\n"
    sig.unlink()
    monkeypatch.setattr(model.targets, "status", lambda *a: "up")
    win.refresh()
    s = view.stop_buttons["game"]
    assert not s.isHidden() and s.text() == "Stop game"
    s.click()
    [sig] = (env.control / "signals").glob("app-*.signal")
    assert sig.read_text(encoding="utf-8") == "by: app\nrun: game stop\n"
    win._quitting = True
    win.close()


def test_repos_header_counts_unused_audit_notes(qapp, env, tmp_path):
    from conductor.app import theme
    from conductor.app.window import MainWindow
    env.queue("001_a", "FAKE audit tidy the loader", audit="true")
    env.tick_until(lambda: env.finished("code/001_a"))
    win = MainWindow([env.control], settings=_settings(tmp_path), tray=False, interval_ms=60_000)
    view = win.views[env.control.resolve()]
    win.refresh()
    assert [rs.audit_notes for rs in view.snap.repos] == [1]
    assert any(head.endswith(" · audit pending: 1 audit note") for _, head, _, _ in view.repos_panel._plan(view.snap))
    top = view.repos_panel._rows[("repo", "code")]
    assert top.foreground(3).color().name().lower() == theme.AMBER.lower()
    win._quitting = True
    win.close()


# ---------------------------------------------------------------- pickers
def _choices(control, name, ref, changed=1, notes=1):
    from conductor.util import stamp
    pdir = control / "picks"
    pdir.mkdir(exist_ok=True)
    data = {"name": name, "title": "T", "manifest": ref, "saved_at": stamp(), "changed": changed, "notes": notes,
            "rows": [{"id": "hero", "key": "2", "changed": True, "note": "warmer"}]}
    (pdir / f"{name}.choices.json").write_text(json.dumps(data), encoding="utf-8")


def test_snapshot_picks_follow_open_and_save(env):
    log, ref = env.d.log, "code/picks/m.json"
    log.append("finish", item="code/001_x", status="done", carry="none", pick=ref)
    log.append("finish", item="code/002_y", status="done", carry="none")
    p = Project(env.control)
    snap = p.snapshot()
    [rs] = snap.repos
    assert {f.name: f.pick for f in rs.finished} == {"001_x": ref, "002_y": None}
    assert set(snap.picks) == {"code/001_x"} and snap.picks["code/001_x"].state == "waiting"
    assert snap.to_pick == 1
    log.append("pick_open", item="code/001_x", name="m", manifest=ref, url="http://x/", opened=True, by="app")
    assert p.snapshot().picks["code/001_x"].state == "opened"
    _choices(env.control, "m", ref, changed=6, notes=2)
    snap = p.snapshot()
    info = snap.picks["code/001_x"]
    assert (info.state, info.changed, info.notes, info.name) == ("saved", 6, 2, "m")
    assert snap.to_pick == 0
    p.close()


def test_pick_activity_wording(env):
    log, ref = env.d.log, "code/picks/m.json"
    evs = [log.append("finish", item="code/001_x", status="done", carry="none", pick=ref),
           log.append("pick_open", item="code/001_x", name="m", manifest=ref, url="http://127.0.0.1:5/t/m/",
                      opened=False, by="app"),
           log.append("pick_saved", item="code/001_x", paths=["picks/m.choices.json"], name="m", manifest=ref,
                      changed=6, notes=1, rows=9),
           log.append("pick_failed", manifest=ref, error="opening failed")]
    p = Project(env.control)
    p.poll_events()
    shown = {e.id for e in p.filter_events(advanced=False)}
    assert all(e.id in shown for e in evs)
    assert [model.activity_text(e) for e in evs] == [
        f"report 001_x written — done (pick: {ref})",
        "picker m opened in the browser: http://127.0.0.1:5/t/m/ (by app) — the browser did not open",
        "picker m saved: 6 of 9 changed, 1 note",
        f"picker {ref} failed: opening failed"]
    p.close()


def test_pick_button_in_the_repos_tab(qapp, env, tmp_path):
    from conductor.app.window import MainWindow
    ref = "code/picks/m.json"
    env.d.log.append("finish", item="code/001_x", status="done", carry="none", pick=ref)
    win = MainWindow([env.control], settings=_settings(tmp_path), tray=False, interval_ms=60_000)
    view = win.views[env.control.resolve()]
    win.refresh()
    assert view.tabs.tabText(0) == "Repos (1 unreviewed, 1 to pick)"
    panel = view.repos_panel
    row = panel._rows[("done", "code/001_x")]
    assert row.text(3).endswith(" · pick: waiting for Matt")
    assert any(head.endswith(" · 1 to pick") for _, head, _, _ in panel._plan(view.snap))
    holder = panel.tree.itemWidget(row, 4)
    assert list(holder.buttons) == ["Mark reviewed", "Pick…", "Terminal"]
    # The widest row's buttons fit their column, labels whole.
    assert holder.sizeHint().width() <= panel.tree.header().sectionSize(4)
    for b in holder.buttons.values():
        assert b.sizeHint().width() >= b.fontMetrics().horizontalAdvance(b.text())
    pick = holder.buttons["Pick…"]
    assert pick.property("tone") == "default"           # amber: it waits for Matt
    # Unreviewed: the row needs Matt, so its name and Mark reviewed are amber.
    assert row.foreground(0).color().name() == theme.AMBER and row.background(0).color().name() == theme.AMBER_BG
    assert holder.buttons["Mark reviewed"].property("tone") == "default"
    pick.click()
    [sig] = (env.control / "signals").glob("app-*.signal")
    assert sig.read_text(encoding="utf-8") == f"by: app\npick: {ref}\n"
    env.d.log.append("pick_open", item="code/001_x", name="m", manifest=ref, url="http://x/", opened=True, by="app")
    win.refresh()
    assert panel._rows[("done", "code/001_x")].text(3).endswith(" · pick: opened, not saved")
    _choices(env.control, "m", ref, changed=6, notes=2)
    win.refresh()
    row = panel._rows[("done", "code/001_x")]
    assert row.text(3).endswith(" (6 changed, 2 notes)") and " · pick: saved " in row.text(3)
    assert panel.tree.itemWidget(row, 4).buttons["Pick…"].property("tone") is None   # rebuilt on save
    assert view.tabs.tabText(0) == "Repos (1 unreviewed)"
    # Saved: the action is taken, so the amber goes, though the report is still unreviewed.
    assert row.foreground(0).color().name() != theme.AMBER
    assert panel.tree.itemWidget(row, 4).buttons["Mark reviewed"].property("tone") is None
    win._quitting = True
    win.close()


def test_a_second_launch_hands_its_folders_to_the_running_app(qapp, env, tmp_path):
    import time

    from conductor.app import single
    from conductor.app.window import MainWindow
    name = f"conductor-app-test-{os.getpid()}-{time.monotonic_ns()}"
    assert not single.forward([env.control], name=name)       # nobody listening yet
    settings = _settings(tmp_path)
    win = MainWindow([], settings=settings, tray=False, interval_ms=60_000)
    win.server = single.listen(win.open_forwarded, name=name)
    assert win.server is not None
    bare = bare_control(tmp_path / "b")
    # The second launch is its own process, as it is for real: the server only accepts
    # from the event loop, which this test pumps meanwhile.
    code = ("import sys; from pathlib import Path; from PySide6.QtCore import QCoreApplication; "
            "from conductor.app import single; app = QCoreApplication([]); "
            "sys.exit(0 if single.forward([Path(a) for a in sys.argv[2:]], name=sys.argv[1]) else 3)")
    second = subprocess.Popen([sys.executable, "-c", code, name, str(env.control), str(bare)])
    deadline = time.monotonic() + 20
    while (second.poll() is None or len(win.views) < 2) and time.monotonic() < deadline:
        qapp.processEvents()
    assert second.wait(5) == 0
    assert set(win.views) == {env.control.resolve(), bare.resolve()}
    assert win.tabs.currentWidget() is win.views[bare.resolve()]
    assert set(win.remembered()) == {str(env.control.resolve()), str(bare.resolve())}
    assert win.isVisible()
    win.server.close()
    win._quitting = True
    win.close()


def test_a_long_question_wraps_to_the_window(qapp, env, tmp_path):
    from PySide6.QtCore import QPoint
    from conductor.app.window import MainWindow
    words = " ".join(f"word{n}" for n in range(80))
    default = "keep every screenshot and delete only the duplicated originals " * 3
    opts = [default.strip(), "delete everything under scratch/shots and start again from nothing",
            "move the whole folder to Drive", "ask me again tomorrow"]
    q = questions.create(env.control, "code/001_x", f"Long body: {words}", opts, opts[0], False, 10)
    questions.create(env.control, "code/001_x", "Done?", ["yes"], "yes", False, 1)
    win = MainWindow([env.control], settings=_settings(tmp_path), tray=False, interval_ms=60_000)
    win.show()
    win.refresh()
    view = win.views[env.control.resolve()]
    panel = view.questions_panel
    view.tabs.setCurrentWidget(panel)
    for width in (1250, 760):                      # the default size, then narrowed
        win.resize(width, 780)
        for _ in range(5):
            qapp.processEvents()
        port = panel.scroll.viewport()
        card = panel.cards[q.id]
        assert card.width() <= port.width(), width
        right = card.send.mapTo(port, QPoint(card.send.width(), 0)).x()
        assert right <= port.width(), width
        assert card.status.width() <= port.width() and card.status.wordWrap()
    win._quitting = True
    win.close()
