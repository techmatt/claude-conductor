"""`conductor run | status | inbox | events`."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import config as config_mod
from . import state
from .events import EventLog
from .inbox import describe, render
from .util import clock, duration, now, parse_since, parse_stamp


def _control(arg: str | None) -> Path:
    """Explicit argument, else $CONDUCTOR_CONTROL, else the current folder."""
    return Path(arg or os.environ.get("CONDUCTOR_CONTROL") or ".").resolve()


def _open(arg: str | None) -> tuple[config_mod.Config, EventLog]:
    cfg = config_mod.load(_control(arg))
    return cfg, EventLog(cfg.events_db)


def cmd_run(args) -> int:
    from .daemon import Daemon, DaemonError
    cfg = config_mod.load(_control(args.control))
    print(f"conductor: {cfg.project} at {cfg.control} — Ctrl+C stops the daemon, not the executors")
    try:
        Daemon(cfg).run_forever()
    except DaemonError as exc:
        print(f"conductor: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_status(args) -> int:
    from . import procs
    import json
    cfg, log = _open(args.control)
    lock = cfg.state_dir / "daemon.json"
    daemon = "not running"
    if lock.is_file():
        info = json.loads(lock.read_text(encoding="utf-8"))
        if procs.alive(info.get("pid"), info.get("create_time")):
            daemon = f"running (pid {info['pid']}, since {clock(info['started'])})"
    print(f"{cfg.project} — daemon {daemon}")
    view = state.scan(cfg, log)
    decisions = state.decide(cfg, view)
    for repo in cfg.repos:
        print(f"\n{repo}  ({cfg.repos[repo]})")
        for item_id, rec in view.running.items():
            if rec["repo"] == repo:
                age = (now() - parse_stamp(rec["started"])).total_seconds()
                print(f"  running  {rec['name']}  {duration(age)}, pid {rec.get('pid')}, "
                      f"kill at {clock(rec['deadline'])}")
        for i, item in enumerate(view.queued.get(repo, [])):
            note = ""
            if i == 0 and decisions[repo].action in ("hold", "wait", "run"):
                note = f"  — {decisions[repo].action}" + (f": {decisions[repo].reason}" if decisions[repo].reason else "")
            print(f"  queued   {item.name}{note}")
        if ev := view.red_hold.get(repo):
            print(f"  held     after red {ev.item} (#{ev.id}); signal `clear: {repo}` to release")
        if not (view.queued.get(repo) or view.red_hold.get(repo)
                or any(r["repo"] == repo for r in view.running.values())):
            print("  idle")
    proposed = sorted(cfg.path("proposed").glob("*.md")) if cfg.path("proposed").is_dir() else []
    if proposed:
        print(f"\nproposed: {', '.join(p.stem for p in proposed)}")
    unread = log.max_id() - log.read_mark()
    print(f"\ninbox: {unread} event(s) since read mark #{log.read_mark()}")
    return 0


def cmd_inbox(args) -> int:
    cfg, log = _open(args.control)
    sys.stdout.write(render(cfg, log, state.scan(cfg, log)))
    return 0


def cmd_events(args) -> int:
    cfg, log = _open(args.control)
    since_id, since_ts = parse_since(args.since) if args.since else (None, None)
    for ev in log.query(since_id=since_id, since_ts=since_ts, item=args.item,
                        kinds=args.kind or None, repo=args.repo):
        paths = f"  [{', '.join(ev.paths)}]" if ev.paths and args.paths else ""
        print(f"#{ev.id:<5} {clock(ev.ts):>14}  {ev.kind:<14} {describe(ev)}{paths}")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        # Redirected output on Windows defaults to the ANSI code page.
        stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="conductor")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn, help_ in (("run", cmd_run, "run the daemon in the foreground"),
                            ("status", cmd_status, "queues, running items, holds"),
                            ("inbox", cmd_inbox, "print the inbox (does not move the read mark)"),
                            ("events", cmd_events, "query the event log")):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("control", nargs="?", help="control folder (default: $CONDUCTOR_CONTROL or .)")
        sp.set_defaults(fn=fn)
        if name == "events":
            sp.add_argument("--since", help="#id, 30m / 2h / 1d, 5:25pm, or an ISO time")
            sp.add_argument("--item", help="sandbox/001_x, 001_x or 001")
            sp.add_argument("--repo")
            sp.add_argument("--kind", action="append", help="repeatable")
            sp.add_argument("--paths", action="store_true", help="show the paths each event names")
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except config_mod.ConfigError as exc:
        print(f"conductor: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
