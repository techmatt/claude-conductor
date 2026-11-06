"""Prompt 82: fractals' notes of 2026-11-05. A load message is information (finish the unit in
flight, never relaunch); files are edited with Write/Edit, never a heredoc; every background
command carries a timeout sized to its estimate. The cancelled-dependency test is in
`test_daemon.py`."""

from conductor import daemon_memory
from test_preamble import SHORT, render
from test_prompt_68 import busy_box
from test_prompt_80 import held, load_tells, samples


def test_both_preambles_carry_the_new_lines(env):
    full = " ".join(render(env, SHORT * 3).split())
    assert "edit files with Write/Edit only, never a shell heredoc" in full
    assert "A load message is information: finish the unit in flight, start the next one smaller" in full
    assert "never kill and relaunch work for load alone" in full
    assert "Every background call sets a `timeout` (a kill limit; 10 minutes if left out) sized to its estimate" in full
    light = " ".join(render(env, SHORT).split())
    assert "Scripts and file edits: Write/Edit, never a heredoc" in light
    assert "on a load message, finish the unit, then go smaller" in light
    assert "`timeout` sized to the estimate" in light
    assert len(render(env, SHORT).split()) <= 385


def test_the_load_message_is_information_not_an_order(env, monkeypatch):
    held(env)
    busy_box(env, monkeypatch, 95, own=60)
    samples(env, 4)
    [tell] = load_tells(env)
    text = tell.detail["text"]
    assert text.startswith("Conductor, for information:") and "Cut their threads" not in text
    assert "Let the unit in flight finish" in text and "never kill and relaunch running work for load alone" in text
    assert daemon_memory.LOAD_CAUTION in text


def test_a_report_header_takes_every_pick_line(tmp_path):
    """fractals 022 named three pickers, 028 four: each `pick:` line is one."""
    from conductor import executor
    r = tmp_path / "r.md"
    r.write_text("status: done\nblocks: none\ncarry: none\nslow: none\nfriction: none\n"
                 "pick: a/r1/manifest.json\npick: `b/r1/manifest.json`\npick: a/r1/manifest.json\n"
                 "pick: went with the warmer one\n\n# Body\npick: c/manifest.json\n", encoding="utf-8")
    header, problem = executor.read_header(r)
    assert problem is None and header["pick"] == "a/r1/manifest.json"
    assert header["picks"] == ["a/r1/manifest.json", "b/r1/manifest.json"]


def test_one_pick_entry_per_picker_in_the_repos_tab(qapp, env, tmp_path):
    from conductor import inbox, picks
    from conductor.app.window import MainWindow
    from test_app import _settings
    from test_app_picks import _choices
    refs = ["code/p/style_teal_r1/manifest.json", "code/p/style_rose_r1/manifest.json", "code/p/style_eye_r1/manifest.json"]
    ev = env.d.log.append("finish", item="code/001_x", status="done", carry="none", pick=refs[0], picks=refs)
    assert picks.refs(ev.detail) == refs and "pick: " + ", ".join(refs) in inbox.describe(ev)
    _choices(env.control, "style_rose_r1", refs[1])
    win = MainWindow([env.control], settings=_settings(tmp_path), tray=False, interval_ms=60_000)
    view = win.views[env.control.resolve()]
    win.refresh()
    assert view.snap.to_pick == 2 and view.tabs.tabText(0) == "Repos (1 unreviewed, 2 to pick)"
    panel = view.repos_panel
    row = panel._rows[("done", "code/001_x")]
    assert "picks: style_teal_r1 waiting for Matt; style_rose_r1 saved" in row.text(3)
    pick = panel.tree.itemWidget(row, 4).buttons["Pick…"]
    assert pick.property("tone") == "default"                   # two still wait
    labels = [a.text() for a in pick.menu().actions()]
    assert [t.split(" — ")[0] for t in labels] == ["style_teal_r1", "style_rose_r1", "style_eye_r1"]
    pick.menu().actions()[2].trigger()
    [sig] = (env.control / "signals").glob("app-*.signal")
    assert sig.read_text(encoding="utf-8") == f"by: app\npick: {refs[2]}\n"
    win._quitting = True
    win.close()
