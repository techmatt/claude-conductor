"""Pickers in the app: the snapshot, Activity's wording, the Repos tab's button."""

import json

from conductor.app import model, theme
from conductor.app.model import Project
from conductor.app.repos_panel import NO_REMOTE
from test_app import _settings


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
    assert set(snap.picks) == {"code/001_x"} and [q.state for q in snap.picks["code/001_x"]] == ["waiting"]
    assert snap.to_pick == 1
    log.append("pick_open", item="code/001_x", name="m", manifest=ref, url="http://x/", opened=True, by="app")
    assert p.snapshot().picks["code/001_x"][0].state == "opened"
    _choices(env.control, "m", ref, changed=6, notes=2)
    snap = p.snapshot()
    [info] = snap.picks["code/001_x"]
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
    assert any(head.removesuffix(" · " + NO_REMOTE).endswith(" · 1 to pick") for _, head, _, _ in panel._plan(view.snap))
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
