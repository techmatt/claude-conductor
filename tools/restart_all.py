"""Restart the conductor app and every live daemon ("restart all").

    uv run python tools/restart_all.py --check     # what a restart would interrupt; exit 1 if anything
    uv run python tools/restart_all.py             # restart

The control folders are `*-control` folders beside this repo that hold a `conductor.toml`.
`--check` lists, per live daemon, its running and staged items and open questions, and
whether the app is up. A restart stops each live daemon with a `stop: daemon` signal,
waits for it to exit and starts it detached (`procs.spawn_daemon`, as the app's Start
button does: it outlives this shell and the Claude Code session that ran it). Running
executors are adopted by the new daemon, not killed. Then the app is stopped and started
detached with the folders it was launched with (it reopens its remembered projects too;
output in `.conductor/app.out.log` of the first). No app running: it starts one on the
newest control folder.
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


def controls() -> list[Path]:
    """Every sibling control folder, the one whose `conductor.toml` was first committed last."""
    def born(p: Path) -> int:
        out = subprocess.run(["git", "log", "--reverse", "--format=%ct", "--", "conductor.toml"],
                             cwd=p, capture_output=True, text=True, check=False)
        return int(out.stdout.split()[0]) if out.stdout.split() else 0
    found = [p for p in ROOT.parent.glob("*-control") if (p / "conductor.toml").is_file()]
    return sorted(found, key=born)


def apps() -> list[psutil.Process]:
    """Running apps: the `conductor app` process whose parent isn't one too (uv's shim and
    the venv launcher each show up with the same command line)."""
    found = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        cmd = p.info["cmdline"] or []
        if "app" in cmd and any(Path(c).stem == "conductor" or c == "conductor" for c in cmd[:cmd.index("app")]):
            found.append(p)
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
              for q in questions.scan(control) if q.is_open]
    return lines


def check(live: list[Path], running_apps: list[psutil.Process]) -> int:
    busy = False
    for control in live:
        lines = activity(control)
        busy |= bool(lines)
        print(f"{control.name}: daemon pid {state.daemon_info(config.load(control)).pid}"
              + ("".join(f"\n  {ln}" for ln in lines) if lines else ", idle"))
    print(f"app: {'running, pid ' + ', '.join(str(p.pid) for p in running_apps) if running_apps else 'not running'}")
    return 1 if busy else 0


def restart_daemon(control: Path) -> None:
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
        for q in [p, *p.children(recursive=True)]:
            try:
                q.kill()
            except psutil.Error:
                pass
    psutil.wait_procs(running_apps, timeout=10)
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
    for control in live:
        restart_daemon(control)
    restart_app(running_apps, all_controls[-1])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
