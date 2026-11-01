"""The desktop app: its Qt-free model and actions, plus an offscreen Qt smoke test."""

import os
import sqlite3
import subprocess
import sys

import psutil
import pytest
from conductor import questions
from conductor.app import actions, model, theme
from conductor.app.model import Project, ReadOnlyLog
from conftest import CONTROL_GITIGNORE, init_repo


def bare_control(tmp_path):
    """A control folder no daemon has ever run in: no events.db, no runtime folders."""
    init_repo(tmp_path / "code", {"README.md": "code\n"})
    control = tmp_path / "control"
    init_repo(control, {"conductor.toml": 'project = "bare"\n\n[repos]\ncode = "../code"\n',
                        ".gitignore": CONTROL_GITIGNORE})
    return control


def test_missing_events_db_stays_missing(tmp_path):
    control = bare_control(tmp_path)
    p = Project(control)
    assert p.poll_events() == []
    snap = p.snapshot()
    assert snap.state == "idle" and not snap.daemon.alive
    assert p.trail("code/001_x").events == []
    assert p.log.read_mark() == 0 and p.log.max_id() == 0
    p.close()
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


def test_run_detach_starts_once(tmp_path, monkeypatch, capsys):
    # `conductor run --detach` spawns as the app's button does, waits for daemon.json,
    # and leaves a live daemon alone.
    from conductor import cli, procs, state
    control = bare_control(tmp_path)
    spawned, live = [], []
    monkeypatch.setattr(procs, "spawn_daemon", lambda c, log: spawned.append((c, log)) or live.append(1))
    monkeypatch.setattr(state, "daemon_info", lambda cfg: state.DaemonInfo(pid=4242, alive=bool(live)) if live
                        else state.DaemonInfo(alive=False))
    assert cli.main(["run", "--detach", str(control)]) == 0
    assert spawned == [(control, control / ".conductor" / "daemon.out.log")]
    assert "daemon started (pid 4242)" in capsys.readouterr().out
    assert cli.main(["run", "--detach", str(control)]) == 0
    assert len(spawned) == 1 and "already running (pid 4242)" in capsys.readouterr().out


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


def test_console_python_under_pythonw(tmp_path, monkeypatch):
    from conductor import procs
    for name in ("python.exe", "pythonw.exe"):
        (tmp_path / name).write_bytes(b"")
    monkeypatch.setattr(procs.sys, "executable", str(tmp_path / "pythonw.exe"))
    want = "python.exe" if procs.WINDOWS else "pythonw.exe"
    assert procs.console_python() == str(tmp_path / want)


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
