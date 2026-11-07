"""The Repos tab: Note takes the spare width, buttons line up by kind, dragged widths stay, a
staged head looks staged until it launches; reviewed reports, the running row, the header."""

import json
from types import SimpleNamespace

import pytest
from conductor.app import model, theme
from conductor.app.model import Project
from conductor.items import Item
from test_app import _settings, bare_control

LONG = "Done: proved the rune altar renders in the room at both sizes, with the egg prop beside it"


def _snap(tmp_path, *, staged=False, held=False):
    head, nxt = (Item("code", n, tmp_path / f"{n}.md") for n in ("002_b", "003_c"))
    fin = [model.Finished("code/000_z", "000_z", "done", "2026-10-21T09:00:00-07:00", False),
           model.Finished("code/000_y", "000_y", "done", "2026-10-21T08:00:00-07:00", True)]
    rs = model.RepoState("code", tmp_path, queued=[head, nxt], finished=fin, finished_total=2,
                         staged=[model.Activity("code/002_b", "002_b", "staged", None)] if staged else [],
                         action="hold" if held else "wait", reason=LONG)
    return SimpleNamespace(repos=[rs], picks={}, checkpoint=None, tools={})


def _panel(tmp_path, settings=None):
    from conductor.app.repos_panel import ReposPanel
    panel = ReposPanel(Project(bare_control(tmp_path / "p")), lambda _: None, settings)
    panel.resize(2400, 600)        # the offscreen font is wide: room for Note to fit
    panel.show()
    return panel


def test_note_takes_the_spare_width_and_buttons_line_up(qapp, tmp_path):
    panel = _panel(tmp_path)
    panel.update_snapshot(_snap(tmp_path))
    tree, h = panel.tree, panel.tree.header()
    holders = {k: tree.itemWidget(w, 4) for k, w in panel._rows.items() if tree.itemWidget(w, 4)}
    # The button column is as wide as its widest row, no wider.
    assert h.sectionSize(4) == max(x.sizeHint().width() for x in holders.values()) + 4
    # Note gets the rest; a long one elides and reads whole in its tooltip.
    widths = [h.sectionSize(c) for c in range(5) if not tree.isColumnHidden(c)]
    assert h.sectionSize(3) == tree.viewport().width() - sum(widths) + h.sectionSize(3)
    assert h.sectionSize(3) > max(h.sectionSize(c) for c in (0, 1))
    head = panel._rows[("queued", "code/002_b")]
    assert head.toolTip(3) == head.text(3) == f"wait: {LONG}"
    # A reviewed row's Terminal sits where an unreviewed row's does, Mark reviewed's room kept.
    new, old = holders[("done", "code/000_z")], holders[("done", "code/000_y")]
    assert list(old.buttons) == ["Terminal"] and list(new.buttons) == ["Mark reviewed", "Terminal"]
    assert old.buttons["Terminal"].x() == new.buttons["Terminal"].x() > 0


def test_a_narrow_window_elides_the_name_before_the_buttons_leave(qapp, tmp_path):
    panel = _panel(tmp_path)
    snap = _snap(tmp_path)
    snap.repos[0].queued[0].name = "002_" + "a_very_long_item_name_" * 3
    panel.update_snapshot(snap)
    h, tree = panel.tree.header(), panel.tree
    wide = h.sectionSize(0)
    others = sum(h.sectionSize(c) for c in (1, 2, 4))
    panel.resize(0, 600)                            # settled below by its own width
    tree.setFixedWidth(others + panel.NAME_MIN + panel.NOTE_MIN + 40)
    panel._fit()
    assert panel.NAME_MIN <= h.sectionSize(0) < wide and h.sectionSize(3) >= panel.NOTE_MIN
    assert sum(h.sectionSize(c) for c in range(5)) <= tree.viewport().width()   # nothing off the edge
    h.resizeSection(0, wide)                        # a dragged width stays: the tree scrolls instead
    panel._fit()
    assert h.sectionSize(0) == wide


def test_a_dragged_width_is_kept_across_refreshes_and_restarts(qapp, tmp_path):
    settings = _settings(tmp_path)
    panel = _panel(tmp_path, settings)
    panel.update_snapshot(_snap(tmp_path))
    h = panel.tree.header()
    fitted = h.sectionSize(0)
    h.resizeSection(0, fitted + 90)                 # as a drag of its edge does
    panel._sig = None
    panel.update_snapshot(_snap(tmp_path))          # a rebuild and refit
    assert h.sectionSize(0) == fitted + 90
    again = _panel(tmp_path / "again", settings)
    again.update_snapshot(_snap(tmp_path))
    assert again.tree.header().sectionSize(0) == fitted + 90
    # A double-click on the edge hands it back to its contents.
    again._unpin(0)
    assert again.tree.header().sectionSize(0) == fitted and settings.value(again.WIDTHS_KEY) in (["0", "0", "0"], [0, 0, 0])


def test_a_staged_head_looks_staged_until_it_launches(qapp, tmp_path):
    from conductor.app.repos_panel import ReposPanel
    for held in (False, True):
        [(_, _, _, rows)] = ReposPanel._plan(None, _snap(tmp_path, staged=True, held=held))
        [look] = [look for key, _, _, look, _ in rows if key == ("queued", "code/002_b")]
        assert look == "stage", held
    panel = _panel(tmp_path)
    panel.update_snapshot(_snap(tmp_path, staged=True, held=True))
    assert panel._rows[("queued", "code/002_b")].foreground(1).color().name() == theme.TEAL


def test_a_dropped_stage_says_it_launches_fresh(env, monkeypatch):
    env.queue("001_x", "FAKE sleep 0")
    # decide() still says stage while the repo is busy; the daemon alone knows it will not.
    monkeypatch.setattr(model.state, "decide", lambda cfg, view: {"code": SimpleNamespace(action="stage", reason="repo busy")})
    p = Project(env.control)
    snap = p.snapshot()
    assert snap.repos[0].action == "stage" and snap.repos[0].stage_dropped == ""
    env.d.log.append("stage", item="code/001_x", session_id="s")
    note = "stage discarded: the stage changed its worktree; the item launches fresh"
    env.d.log.append("warning", item="code/001_x", note=note)
    rs = p.snapshot().repos[0]
    assert rs.stage_dropped == note
    from conductor.app.repos_panel import ReposPanel
    [(_, _, _, rows)] = ReposPanel._plan(None, SimpleNamespace(repos=[rs], picks={}, checkpoint=None))
    [texts] = [t for key, t, *_ in rows if key == ("queued", "code/001_x")]
    assert texts[1:] == ["queued · next", "", note]
    env.d.log.append("stage", item="code/001_x", session_id="s2")    # staged again: no longer dropped
    assert p.snapshot().repos[0].stage_dropped == ""
    p.close()


# ---------------------------------------------------------------- reviewed reports, the running row, the header
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
    view.project.probe.settle()        # probes run off the UI thread: the next refresh starts
    win.refresh()                      # one, and the refresh after it shows its answer
    view.project.probe.settle()
    win.refresh()
    s = view.stop_buttons["game"]
    assert not s.isHidden() and s.text() == "Stop game"
    s.click()
    [sig] = (env.control / "signals").glob("app-*.signal")
    assert sig.read_text(encoding="utf-8") == "by: app\nrun: game stop\n"
    win._quitting = True
    win.close()


def test_target_probe_never_blocks_a_snapshot(env, monkeypatch):
    import threading
    import time
    toml = env.control / "conductor.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + '\n[run.game]\nrepo = "code"\ncommand = "x"\n'
                    'url = "http://127.0.0.1:9/"\n', encoding="utf-8", newline="\n")
    gate = threading.Event()
    monkeypatch.setattr(model.targets, "status", lambda *a: gate.wait(5) and "up")
    project = model.Project(env.control)
    t = time.perf_counter()
    assert project.snapshot().targets == {"game": "down"}       # the record's answer meanwhile
    assert project.snapshot().targets == {"game": "down"}       # one probe out, not a second
    assert time.perf_counter() - t < 2
    gate.set()
    project.probe.settle()
    assert project.snapshot().targets == {"game": "up"}
    project.close()


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
