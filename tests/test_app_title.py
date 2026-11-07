"""The app's icon and its live window title (`app/icon.py`, `app/status.py`)."""

import struct
from types import SimpleNamespace

from conductor.app import status


def snap(asks: int = 0, open_asks: int = 0, reds: int = 0, picks: int = 0, running: int = 0, staged: int = 0,
         staging: int = 0, queued: int = 0, alive: bool = True, paused: tuple = (), unreviewed: int = 0):
    qs = [SimpleNamespace(block=True)] * asks + [SimpleNamespace(block=False)] * open_asks
    stages = [SimpleNamespace(phase="staged")] * staged + [SimpleNamespace(phase="staging")] * staging
    repos = [SimpleNamespace(name=f"r{i}", red=object() if i < reds else None, running=[object()] * (running if i == 0 else 0),
                             staged=stages if i == 0 else [], queued=[object()] * queued if i == 0 else [],
                             unreviewed=unreviewed if i == 0 else 0)
             for i in range(max(reds, 1))]
    return SimpleNamespace(open_questions=qs, repos=repos, to_pick=picks, daemon=SimpleNamespace(alive=alive),
                           paused=dict.fromkeys(paused))


def test_title_ranks_needs_over_running_over_idle():
    assert status.summarize([]).title() == "Conductor"
    assert status.summarize([snap(staged=1, queued=2)]).title() == "Conductor"
    run = status.summarize([snap(running=2), snap(running=1)])
    assert [run.title(t) for t in range(5)] == [f"{g} Conductor · 3 running" for g in "◐◓◑◒◐"]
    need = status.summarize([snap(asks=1, running=2), snap(reds=2, picks=1)])
    # What runs shows beside what needs Matt (84: "pick · 1 to review" hid fractals 033 running).
    assert need.kind == "need" and need.title(0) == need.title(3) == "● Conductor · 1 question · 2 red · pick · 2 running"
    assert status.summarize([snap(asks=2)]).title() == "● Conductor · 2 questions"
    assert status.summarize([snap(reds=1, picks=3)]).title() == "● Conductor · red · 3 picks"
    # A stage still staging has a live session; one waiting with its plan does not.
    assert status.summarize([snap(staging=1, staged=2)]).title() == "◐ Conductor · 1 running"
    # A daemon down with work queued (or running) needs Matt; with nothing to do it is idle.
    assert status.summarize([snap(queued=1, alive=False)]).title() == "● Conductor · daemon down"
    assert status.summarize([snap(alive=False)]).title() == "Conductor"


def test_something_waiting_for_review_marks_orange_whatever_else_holds():
    # Nothing running: the orange dot where the ticker would be, and a still orange badge.
    wait = status.summarize([snap(unreviewed=2), snap(open_asks=1)])
    assert wait.title() == "🟠 Conductor · 2 to review · 1 open question" and wait.badge == "wait"
    # Running too: the ticker turns, and so does the badge, orange.
    both = status.summarize([snap(running=1, unreviewed=1)])
    assert both.title(1) == "◓ Conductor · 1 running · 1 to review" and both.badge == "turn"
    # Running alone: no badge, the title's `N running` says it (85 dropped 84's gold one); quiet: none.
    assert status.summarize([snap(running=1)]).badge is None and status.summarize([snap()]).badge is None
    # Needs Matt stays red; a blocking question is waiting too, so it is counted once there.
    need = status.summarize([snap(asks=1, unreviewed=1)])
    assert need.title() == "● Conductor · 1 question · 1 to review" and need.badge == "need" and need.waiting == 2
    # Pausing never hides it.
    assert status.summarize([snap(unreviewed=1, paused=("r0",))]).badge == "wait"
    # A tab's hub: red over orange over gold over slate.
    assert [status.tab_kind(snap(**kw)) for kw in ({"asks": 1}, {"unreviewed": 1, "running": 1}, {"running": 1}, {})] \
        == ["need", "waiting", "running", "idle"]


def test_the_taskbar_badge_reads_on_the_smallest_icon(qapp):
    from conductor.app import icon
    for size in (16, 32):
        corner = (size * 3 // 4, size * 3 // 4)       # inside the badge, off the hub
        plain, wait = icon.image(size).pixelColor(*corner), icon.image(size, badge="wait").pixelColor(*corner)
        assert wait.red() > 200 and 80 < wait.green() < 160 and wait.blue() < 80, (size, wait.name())
        assert plain.name() != wait.name()
        assert icon.image(size, badge="need").pixelColor(*corner).green() < 120
    turns = {icon.image(32, badge="turn", tick=t).pixelColor(26, 21).name() for t in range(4)}
    assert len(turns) == 2              # the filled half moves round: bright, then dim, at one spot
    assert icon.icon(badge="turn", tick=1) is icon.icon(badge="turn", tick=5)      # cached per step


def test_the_taskbar_turns_orange_while_a_report_waits_and_clears_on_review(qapp, env, tmp_path):
    from PySide6.QtCore import QSettings

    from conductor.app.window import MainWindow, tray_hub
    win = MainWindow([env.control], settings=QSettings(str(tmp_path / "a.ini"), QSettings.Format.IniFormat),
                     tray=False, interval_ms=60_000)
    env.d.acquire()
    env.queue("001_x", "done\n")
    env.tick_until(lambda: env.finished("code/001_x"))
    win.refresh()
    assert win.windowTitle() == "🟠 Conductor · 1 to review" and win._badge == ("wait", 0)
    assert tray_hub(win.status, 1) == "orange" and win.tabs.tabBar().tabTextColor(0).name() == "#c2410c"
    env.queue("002_x", env.hold())
    env.tick_until(lambda: env.d.log.last("start", item="code/002_x"))
    win.refresh()
    assert win.windowTitle() == "◐ Conductor · 1 running · 1 to review" and win._badge == ("turn", 0)
    win._turn()
    assert win._badge == ("turn", 1) and [tray_hub(win.status, t) for t in (0, 1)] == ["orange", "orange_dim"]
    env.d.log.append("reviewed", item="code/001_x", status="done", by="planner")
    win.refresh()
    assert win.windowTitle() == "◓ Conductor · 1 running" and win._badge == (None, 0)
    env.release()
    win._quitting = True
    win.close()


def test_a_tab_turns_while_its_project_moves_and_not_when_paused():
    assert [status.tab_text("p", snap(running=1), t) for t in range(5)] == [f"{g} p" for g in "◐◓◑◒◐"]
    assert status.tab_text("p", snap(staging=1), 1) == "◓ p"            # a stage still staging moves
    assert status.tab_text("p", snap(staged=2, queued=1)) == "p"         # waiting is still
    assert status.tab_text("p", snap(asks=1, running=1)) == "◐ p"        # needing Matt doesn't stop it
    assert status.tab_text("p", snap(running=1, paused=("r0",))) == "p"  # its repo paused: still
    assert status.tab_text("p", None) == "p"


def test_the_icon_is_the_master_and_its_ico_has_every_size(qapp):
    from conductor.app import icon
    assert icon.svg() == icon.MASTER.read_text(encoding="utf-8")
    assert 'stroke-width="2.6"' not in icon.svg(small=True) and 'fill="#e5484d"' in icon.svg("red")
    data = icon.ICO.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert (reserved, kind, count) == (0, 1, len(icon.ICO_SIZES))
    for n, size in enumerate(icon.ICO_SIZES):
        w, h, _, _, _, _, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * n)
        assert w == h == size % 256
        assert data[offset:offset + 8] == b"\x89PNG\r\n\x1a\n" and offset + length <= len(data)
    red, gold = icon.image(16, "red").pixelColor(8, 9), icon.image(16, "gold").pixelColor(8, 9)    # the hub's centre
    assert red.red() > 180 and red.green() < 120 and gold.green() > 150


def test_the_window_title_follows_running_and_red(qapp, env, tmp_path):
    from PySide6.QtCore import QSettings

    from conductor.app.window import MainWindow, tray_hub
    win = MainWindow([env.control], settings=QSettings(str(tmp_path / "a.ini"), QSettings.Format.IniFormat),
                     tray=False, interval_ms=60_000)
    win.refresh()
    assert win.windowTitle() == "Conductor" and not win.turner.isActive()
    env.d.acquire()                 # the app sees a live daemon (else: "daemon down" with work)
    env.queue("001_x", env.hold())
    env.tick_until(lambda: env.d.log.last("start", item="code/001_x"))
    win.refresh()
    assert win.windowTitle() == "◐ Conductor · 1 running" and win.turner.isActive()
    win._turn()
    assert win.windowTitle() == "◓ Conductor · 1 running"
    assert [tray_hub(win.status, t) for t in (0, 1)] == ["gold", "dim"]
    env.d.log.append("red", item="code/001_x", reasons=["boom"], after_red="stop")
    win.refresh()
    assert win.windowTitle() == "● Conductor · red · 1 running"     # what runs still shows (84)
    win._turn()                     # the title holds still; the item still runs, so its tab turns on
    assert win.windowTitle() == "● Conductor · red · 1 running" and win.turner.isActive()
    assert tray_hub(win.status, 1) == "red"
    env.d._lock_file.unlink()
    win.refresh()
    assert win.windowTitle().startswith("● Conductor · red · daemon down")
    env.release()
    win._quitting = True
    win.close()


def test_a_running_projects_tab_turns_while_another_is_selected(qapp, env, env_template, tmp_path):
    import shutil

    from PySide6.QtCore import QSettings

    from conductor.app.window import MainWindow
    quiet = tmp_path / "quiet"
    for part in ("code", "control"):
        shutil.copytree(env_template / part, quiet / part)
    win = MainWindow([env.control, quiet / "control"],
                     settings=QSettings(str(tmp_path / "a.ini"), QSettings.Format.IniFormat),
                     tray=False, interval_ms=60_000)
    win.tabs.setCurrentIndex(1)         # the quiet project is the one on screen
    name, still = win.tabs.tabText(0), win.tabs.tabText(1)
    win.refresh()
    assert win.tabs.tabText(0) == name and not win.turner.isActive()
    env.d.acquire()
    env.queue("001_x", env.hold())
    env.tick_until(lambda: env.d.log.last("start", item="code/001_x"))
    win.refresh()
    assert win.turner.isActive() and win.tabs.tabText(0) == f"◐ {name}"
    win._turn()
    assert win.tabs.tabText(0) == f"◓ {name}" and win.tabs.tabText(1) == still
    env.release()
    env.tick_until(lambda: env.finished("code/001_x"))
    win.refresh()
    assert win.tabs.tabText(0) == name
    win._quitting = True
    win.close()
