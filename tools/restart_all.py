"""Restart the conductor app and every live daemon ("restart all").

    uv run python tools/restart_all.py --check     # what runs now (all of it is adopted across a restart)
    uv run python tools/restart_all.py             # restart, interrupting nothing
    uv run python tools/restart_all.py --force     # stop and start each daemon now, not at its safe point

The control folders are `*-control` folders beside this repo that hold a `conductor.toml`.
A restart writes `restart: daemon` to each live daemon (71): the daemon restarts itself at
its next safe point, as for a code change (`docs/restart.md`), so running executors and
stages are adopted and the project's tools keep running. It waits up to `RESTART_WAIT`
for every daemon's new pid, showing what a daemon still waits on (its warning naming the
blockers: a setup, a merge executor resolving, an open picker...). One that hasn't
restarted by then restarts on its own once safe; `--force` stops and starts it now
(`stop: daemon`, then `procs.spawn_daemon`), which kills a setup, a resolve or a picker
and stops the tools. Then the app is stopped and started detached with the folders it was
launched with (it reopens its remembered projects too; output in `.conductor/app.out.log`
of the first). Only the app's own processes are killed, never a session console it
opened. No app running: it starts one on the newest control folder.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import psutil

from conductor import config, procs, questions, state
from conductor.app import actions
from conductor.events import EventLog

ROOT = Path(__file__).resolve().parent.parent
STOP_WAIT = 90
RESTART_WAIT = 600          # for every daemon's safe point
SHOW_EVERY = 30             # seconds between lines naming what a daemon waits on
BY = "restart_all"


def controls() -> list[Path]:
    """Every sibling control folder, the one whose `conductor.toml` was first committed last."""
    def born(p: Path) -> int:
        out = subprocess.run(["git", "log", "--reverse", "--format=%ct", "--", "conductor.toml"],
                             cwd=p, capture_output=True, text=True, check=False)
        return int(out.stdout.split()[0]) if out.stdout.split() else 0
    found = [p for p in ROOT.parent.glob("*-control") if (p / "conductor.toml").is_file()]
    return sorted(found, key=born)


def _app_procs() -> list[psutil.Process]:
    """Every `conductor app` process: uv's shim and the venv launcher each show up with the
    same command line as the app itself."""
    found = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        cmd = p.info["cmdline"] or []
        if "app" in cmd and any(Path(c).stem == "conductor" or c == "conductor" for c in cmd[:cmd.index("app")]):
            found.append(p)
    return found


def apps() -> list[psutil.Process]:
    """Running apps: the `conductor app` process whose parent isn't one too."""
    found = _app_procs()
    pids = {p.pid for p in found}
    return [p for p in found if _parent(p) not in pids]


def _parent(p: psutil.Process) -> int | None:
    try:
        return p.ppid()
    except psutil.Error:
        return None


def activity(control: Path) -> list[str]:
    cfg = config.load(control)
    view = state.scan(cfg, EventLog(cfg.events_db))
    lines = [f"running: {i}" for i in view.running] + [f"staged: {i}" for i in view.staged]
    lines += [f"open question {q.id}" + (" (blocking)" if q.block else "")
              for q in questions.scan(control) if q.is_open and not q.design]     # a design question holds nothing up
    return lines


def check(live: list[Path], running_apps: list[psutil.Process]) -> int:
    for control in live:
        lines = activity(control)
        print(f"{control.name}: daemon pid {state.daemon_info(config.load(control)).pid}"
              + ("".join(f"\n  {ln}" for ln in lines) if lines else ", idle"))
    print(f"app: {'running, pid ' + ', '.join(str(p.pid) for p in running_apps) if running_apps else 'not running'}")
    return 0


def restart_daemons(live: list[Path], wait: float = RESTART_WAIT) -> list[Path]:
    """`restart: daemon` to each, then wait for each new pid. Returns the ones not restarted."""
    pending: dict[Path, tuple[int, int]] = {}       # control -> (old pid, last event id before the signal)
    for control in live:
        cfg = config.load(control)
        log = EventLog(cfg.events_db)
        pending[control] = (state.daemon_info(cfg).pid, log.max_id())
        log.close()
        actions.write_signal(control, "restart: daemon", by=BY)
    end, shown = time.time() + wait, time.time()
    while pending and time.time() < end:
        for control, (old, since) in list(pending.items()):
            new = state.daemon_info(config.load(control))
            if new.alive and new.pid != old:
                print(f"{control.name}: daemon {old} restarted, {new.pid} now ({_adopted(control, since)})")
                del pending[control]
            elif not procs.alive(old, None) and not new.alive:
                print(f"{control.name}: daemon {old} exited and no new one is up: see .conductor/daemon.out.log")
                del pending[control]
        if pending and time.time() - shown >= SHOW_EVERY:
            shown = time.time()
            for control, (old, since) in pending.items():
                print(f"{control.name}: waiting for a safe point ({_waits_on(control, since)})")
        time.sleep(1)
    for control, (old, since) in pending.items():
        print(f"{control.name}: daemon {old} not restarted after {wait:.0f}s ({_waits_on(control, since)}); it "
              f"restarts on its own once safe. `--force` stops and starts it now")
    return list(pending)


def _events(control: Path, since: int, kind: str):
    log = EventLog(config.load(control).events_db)
    try:
        return log.query(since_id=since, kinds=[kind])
    finally:
        log.close()


def _adopted(control: Path, since: int) -> str:
    items = [ev.item for ev in _events(control, since, "adopt")]
    return f"adopted {', '.join(items)}" if items else "nothing running"


def _waits_on(control: Path, since: int) -> str:
    notes = [ev.detail.get("note", "") for ev in _events(control, since, "warning")
             if "restart: daemon" in ev.detail.get("note", "")]
    return notes[-1] if notes else "no blocker named yet"


def force_daemon(control: Path) -> None:
    cfg = config.load(control)
    d = state.daemon_info(cfg)
    actions.stop_daemon(control)
    deadline = time.time() + STOP_WAIT
    while procs.alive(d.pid, None) and time.time() < deadline:
        time.sleep(1)
    if procs.alive(d.pid, None):
        sys.exit(f"{control.name}: daemon {d.pid} still alive after {STOP_WAIT}s; nothing else restarted")
    actions.start_daemon(control)
    deadline = time.time() + STOP_WAIT      # the pid spawned is the venv's shim; daemon.json has the daemon's
    while not (new := state.daemon_info(cfg)).alive and time.time() < deadline:
        time.sleep(1)
    print(f"{control.name}: daemon {d.pid} stopped, " + (f"{new.pid} started" if new.alive else
          f"no new daemon after {STOP_WAIT}s: see .conductor/daemon.out.log"))


def restart_app(running_apps: list[psutil.Process], fallback: Path) -> None:
    folders: list[str] = []
    for p in running_apps:
        cmd = p.cmdline()
        folders += [f for f in cmd[cmd.index("app") + 1:] if f not in folders]
    # The app's own processes only: a `claude --resume` console it opened is Matt's session.
    mine = _app_procs() if running_apps else []
    for q in mine:
        try:
            q.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(mine, timeout=10)
    folders = folders or [str(fallback)]
    first = Path(folders[0]).resolve()
    new = procs.spawn_detached([procs.console_python(), "-m", "conductor", "app", *folders], ROOT,
                               first / ".conductor" / "app.out.log")
    print(f"app: {'restarted' if running_apps else 'started'}, pid {new.pid} ({', '.join(folders)})")


def main(argv: list[str]) -> int:
    all_controls = controls()
    live = [c for c in all_controls if state.daemon_info(config.load(c)).alive]
    running_apps = apps()
    if "--check" in argv:
        return check(live, running_apps)
    if "--force" in argv:
        for control in live:
            force_daemon(control)
        late = []
    else:
        late = restart_daemons(live)
    restart_app(running_apps, all_controls[-1])
    return 1 if late else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
