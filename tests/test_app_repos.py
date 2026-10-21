"""The Repos tab's columns: Note takes the spare width, buttons line up by kind, dragged
widths stay, and a staged head looks staged until it launches."""

from types import SimpleNamespace

from conductor.app import model, theme
from conductor.app.model import Project
from conductor.items import Item
from test_app import _settings, bare_control, qapp  # noqa: F401  (qapp is a fixture)

LONG = "Done: proved the rune altar renders in the room at both sizes, with the egg prop beside it"


def _snap(tmp_path, *, staged=False, held=False):
    head, nxt = (Item("code", n, tmp_path / f"{n}.md") for n in ("002_b", "003_c"))
    fin = [model.Finished("code/000_z", "000_z", "done", "2026-10-21T09:00:00-07:00", False),
           model.Finished("code/000_y", "000_y", "done", "2026-10-21T08:00:00-07:00", True)]
    rs = model.RepoState("code", tmp_path, queued=[head, nxt], finished=fin, finished_total=2,
                         staged=[model.Activity("code/002_b", "002_b", "staged", None)] if staged else [],
                         action="hold" if held else "wait", reason=LONG)
    return SimpleNamespace(repos=[rs], picks={}, checkpoint=None)


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
