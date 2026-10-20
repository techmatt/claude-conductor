"""`conductor run | status | inbox | events | questions | audits | answer | tell | amend | checkpoint | open | run-target | pick | init | app`."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import config as config_mod
from . import audits, items, questions, state
from .events import EventLog
from .inbox import describe, progress_line, render
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


def _daemon_alive(cfg: config_mod.Config) -> dict | None:
    """The live daemon's lock record (`pid`, `started`), or None."""
    from . import procs
    import json
    lock = cfg.state_dir / "daemon.json"
    try:
        info = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) and procs.alive(info.get("pid"), info.get("create_time")) else None


def cmd_status(args) -> int:
    cfg, log = _open(args.control)
    daemon = "not running"
    if info := _daemon_alive(cfg):
        daemon = f"running (pid {info['pid']}, since {clock(info['started'])})"
    print(f"{cfg.project} — daemon {daemon}")
    view = state.scan(cfg, log)
    decisions = state.decide(cfg, view)
    open_qs = [q for q in questions.scan(cfg.control) if q.is_open]
    for repo in cfg.repos:
        print(f"\n{repo}  ({cfg.repos[repo]})")
        for item_id, rec in view.running.items():
            if rec["repo"] != repo:
                continue
            where = f", on {rec['branch']}" if rec.get("branch") else ""
            if rec.get("phase") == "merge":
                hold = log.last("hold", item=item_id)
                why = f" — {hold.detail.get('reason')}" if hold and hold.detail.get("merge") else ""
                print(f"  merging  {rec['name']}  {rec['outcome']['status']}{where}{why}")
                continue
            age = (now() - parse_stamp(rec["started"])).total_seconds()
            blocked = [q.id for q in open_qs if q.item == item_id and q.block]
            kill = (f"blocked on {blocked[0]} (kill time moves)" if blocked
                    else f"kill at {clock(rec['deadline'])}")
            extra = (", readonly" if rec.get("readonly") else "") + (f", port {rec['port']}" if rec.get("port") else "")
            print(f"  running  {rec['name']}  {duration(age)}, pid {rec.get('pid')}{where}{extra}, {kill}")
            if p := log.last("progress", item=item_id):
                print(f"           progress {clock(p.ts)}: {progress_line(p)}")
        for item_id, rec in view.staged.items():
            if rec["repo"] == repo:
                age = (now() - parse_stamp(rec["started"])).total_seconds()
                what = (f"staging  {rec['name']}  {duration(age)}, pid {rec.get('pid')}" if rec["phase"] == "staging"
                        else f"staged   {rec['name']}  plan ready, resumes when it can run")
                print(f"  {what}")
        for i, item in enumerate(view.queued.get(repo, [])):
            note = ""
            if i == 0 and decisions[repo].action in ("hold", "wait", "run", "resume", "stage"):
                note = f"  — {decisions[repo].action}" + (f": {decisions[repo].reason}" if decisions[repo].reason else "")
            print(f"  queued   {item.name}{note}")
        if ev := view.red_hold.get(repo):
            print(f"  held     after red {ev.item} (#{ev.id}); signal `clear: {repo}` to release")
        if not (view.queued.get(repo) or view.red_hold.get(repo)
                or any(r["repo"] == repo for r in [*view.running.values(), *view.staged.values()])):
            print("  idle")
        if notes := view.unused_notes(cfg, repo):
            print(f"  {audits.pending(len(notes), [n.item for n in notes])}")
    proposed = sorted(cfg.path("proposed").glob("*.md")) if cfg.path("proposed").is_dir() else []
    if proposed:
        print(f"\nproposed: {', '.join(p.stem for p in proposed)}")
    if open_qs:
        print(f"\nquestions open: {', '.join(q.id for q in open_qs)}")
    if cp := view.checkpoint:
        print(f"\n{describe(cp)}, {clock(cp.ts)}; "
              f"{sum(1 for i in view.finishes if view.settled(i))} earlier item(s) settled and hidden")
    unread = log.max_id() - log.read_mark()
    print(f"\ninbox: {unread} event(s) since read mark #{log.read_mark()}")
    return 0


def cmd_questions(args) -> int:
    cfg = config_mod.load(_control(args.control))
    shown = [q for q in questions.scan(cfg.control) if args.all or q.is_open]
    if not shown:
        print("no open questions" if not args.all else "no questions")
    for q in shown:
        if q.is_open:
            how = "blocking" if q.block else f"default at {clock(q.deadline)}: {q.default}"
        else:
            how = f"{q.result.get('outcome')} {clock(q.result['at'])}: {q.result.get('text') or q.result.get('reason')}"
        print(f"{q.id}  ({q.item}, asked {clock(q.asked)}) — {how}")
        for ln in q.body.rstrip().splitlines():
            print(f"    {ln}")
        print()
    return 0


def cmd_audits(args) -> int:
    cfg, log = _open(args.control)
    view = state.scan(cfg, log)
    claimed = view.claimed_notes()
    for repo in cfg.repos:
        notes = audits.notes(cfg, repo, view.finishes, view.audit_used, include_used=True)
        free = [n for n in notes if n.used_by is None and n.item not in claimed]
        print(f"{repo}: {len(free)} unused audit note(s)")
        for n in notes:
            if n in free or args.all:
                lines = len(n.path.read_text(encoding="utf-8", errors="replace").splitlines())
                how = (f"used by {n.used_by.item} (#{n.used_by.id})" if n.used_by else
                       "claimed by a running cleanup" if n.item in claimed else "unused")
                print(f"  {n.item}  finished {n.finish.detail.get('status')} {clock(n.finish.ts)} — "
                      f"{cfg.rel(n.path)} ({lines} lines) · {how}")
    return 0


def cmd_answer(args) -> int:
    cfg = config_mod.load(_control(args.control))
    q = questions.find(cfg.control, args.id)
    if q is None:
        print(f"conductor: no single question matches {args.id!r} (see `conductor questions --all`)",
              file=sys.stderr)
        return 1
    if q.answer_path.exists():
        print(f"conductor: {q.id} already has an answer file; not overwriting it", file=sys.stderr)
        return 1
    questions.write_answer(q, " ".join(args.text))
    if q.is_open:
        print(f"answered {q.id}; the executor gets it within a few seconds")
    else:
        print(f"{q.id} was already {q.result.get('outcome')}; the answer is logged as late and changes nothing")
    return 0


def _named(ids, ref: str) -> list[str]:
    """The ids `ref` names: `repo/NNN_name`, `repo/NNN`, `NNN_name` or `NNN`."""
    repo, slash, name = ref.rpartition("/")
    return [i for i in ids if (not slash or i.split("/", 1)[0] == repo)
            and items.number_matches(i.split("/", 1)[1], name)]


def cmd_tell(args) -> int:
    from .app import actions             # Qt-free: the app's signal writer
    cfg, log = _open(args.control)
    view = state.scan(cfg, log)
    hits = _named([i for i, rec in view.running.items() if rec.get("phase", "exec") == "exec"], args.item)
    if len(hits) != 1:
        what = "no running item" if not hits else f"{args.item!r} is ambiguous among"
        print(f"conductor: {what} {args.item!r}" if not hits else f"conductor: {what} {hits}", file=sys.stderr)
        return 1
    try:
        path = actions.tell(cfg.control, hits[0], " ".join(args.text), by="cli")
    except ValueError as exc:
        print(f"conductor: {exc}", file=sys.stderr)
        return 1
    print(f"wrote signals/{path.name}: the daemon logs `tell`, and the executor gets the message "
          "after its next tool call (`delivered`)")
    return 0


def cmd_amend(args) -> int:
    from .app import actions
    cfg, log = _open(args.control)
    view = state.scan(cfg, log)
    live = {*view.running, *view.staged, *(it.id for q in view.queued.values() for it in q)}
    hits = sorted(set(_named(live, args.item)))
    if len(hits) != 1:
        done = _named(view.finishes, args.item)
        why = (f"{args.item!r} is ambiguous among {hits}" if hits else
               f"{done[0]} has finished; a correction to finished work is a new item" if done else
               f"no queued, staged or running item {args.item!r}")
        print(f"conductor: {why}", file=sys.stderr)
        return 1
    item_id = hits[0]
    if view.running.get(item_id, {}).get("phase", "exec") != "exec" or item_id in view.finishes:
        print(f"conductor: {item_id} has finished executing; a correction to finished work is a new item",
              file=sys.stderr)
        return 1
    text = " ".join(args.text)
    if len(args.text) == 1 and Path(args.text[0]).is_file():
        text = Path(args.text[0]).read_text(encoding="utf-8")
    try:
        path = actions.amend(cfg.control, item_id, text, by="cli")
    except ValueError as exc:
        print(f"conductor: {exc}", file=sys.stderr)
        return 1
    how = ("after its next tool call" if item_id in view.running else
           "in the resume message (the stage is kept)" if item_id in view.staged else "in its prompt at launch")
    print(f"wrote {cfg.rel(path)}: the daemon logs `amended`, and the executor gets it {how}")
    return 0


def cmd_open(args) -> int:
    from .app import actions
    cfg, log = _open(args.control)
    view = state.scan(cfg, log)
    hits = _named(view.finishes, args.item)
    live = _named([*view.running, *view.staged], args.item)
    if live or len(hits) != 1:
        why = (f"{live[0]} is running or staged; resuming a live session would fork it" if live
               else f"no finished item {args.item!r}" if not hits else f"{args.item!r} is ambiguous among {hits}")
        print(f"conductor: {why}", file=sys.stderr)
        return 1
    sid = view.finishes[hits[0]].detail.get("session_id")
    if not sid:
        print(f"conductor: {hits[0]}'s finish event names no session", file=sys.stderr)
        return 1
    try:
        cwd, argv = actions.open_session(cfg, hits[0], sid)
    except OSError as exc:
        print(f"conductor: {exc}", file=sys.stderr)
        return 1
    print(f"opened a console in {cwd}: {' '.join(argv)}")
    return 0


def cmd_run_target(args) -> int:
    """Start (or stop) a run target here and now, waiting until it is ready and opened;
    the same events as the `run:` signal, with `by: cli`. No daemon needed."""
    import time
    from .targets import Runner
    cfg, log = _open(args.control)
    runner = Runner(cfg, log)
    first = log.max_id()
    try:
        if args.stop:
            runner.stop(args.name, by="cli")
        else:
            runner.request(args.name, by="cli")
            while runner.pending:
                time.sleep(0.5)
                runner.poll()
    except ValueError as exc:
        print(f"conductor: {exc}", file=sys.stderr)
        return 1
    failed = False
    for ev in log.query(since_id=first):
        if ev.kind.startswith("run_") and ev.detail.get("target") == args.name:
            print(describe(ev))
            failed |= ev.kind == "run_failed"
    return 1 if failed else 0


def cmd_pick(args) -> int:
    """Open a picker. With the daemon running, a `pick:` signal (the daemon's server
    serves it); without one, serve it here until Ctrl+C, with the same events."""
    from . import picker, picks
    cfg = config_mod.load(_control(args.control))
    log = EventLog(cfg.events_db)

    def refuse(exc: ValueError) -> int:
        # Red like the signal's `invalid`, so the inbox, a toast and the app show it too.
        log.append("pick_failed", manifest=args.ref, error=str(exc), by="cli")
        print(f"conductor: {exc}", file=sys.stderr)
        return 1

    try:
        try:
            manifest = picks.load_manifest(cfg, args.ref)
        except ValueError as exc:
            return refuse(exc)
        if _daemon_alive(cfg) and not args.until_saved:
            from .app import actions
            path = actions.write_signal(cfg.control, f"pick: {manifest.ref}", by="cli")
            print(f"wrote signals/{path.name}: the daemon opens the picker and logs pick_open")
            return 0
        log.on_append = lambda ev: print(f"{clock(ev.ts)} #{ev.id} {describe(ev)}")
        try:
            return picker.serve(cfg, log, manifest.ref, by="cli", until_saved=args.until_saved)
        except ValueError as exc:
            log.on_append = None
            return refuse(exc)
    finally:
        log.close()


def cmd_init(args) -> int:
    from .scaffold import InitError, init
    try:
        sha, warnings = init(Path(args.folder), args.project, args.repo or [])
    except InitError as exc:
        print(f"conductor: {exc}", file=sys.stderr)
        return 1
    for w in warnings:
        print(f"warning: {w}")
    print(f"created {Path(args.folder).resolve()} ({sha[:10]}); start it with: conductor run {args.folder}")
    return 0


def cmd_inbox(args) -> int:
    cfg, log = _open(args.control)
    sys.stdout.write(render(cfg, log, state.scan(cfg, log)))
    return 0


def cmd_checkpoint(args) -> int:
    from .app import actions
    cfg = config_mod.load(_control(args.control))
    path = actions.checkpoint(cfg.control, " ".join(args.label), by="cli")
    print(f"wrote signals/{path.name}: the daemon logs `checkpoint` (its event id is its number) "
          "and a warning for each report still unreviewed")
    return 0


def _since(log: EventLog, text: str) -> tuple[int | None, object]:
    """`parse_since`, plus `checkpoint` (the latest) and `checkpoint N`: from that
    checkpoint's own event on."""
    words = text.split()
    if words and words[0].lower() in ("checkpoint", "cp"):
        if len(words) == 1:
            if (cp := log.last("checkpoint")) is None:
                raise ValueError("no checkpoint yet")
            return cp.id - 1, None
        return int(words[1].lstrip("#")) - 1, None
    return parse_since(text)


def cmd_events(args) -> int:
    cfg, log = _open(args.control)
    try:
        since_id, since_ts = _since(log, args.since) if args.since else (None, None)
    except ValueError as exc:
        print(f"conductor: --since {args.since!r}: {exc}", file=sys.stderr)
        return 1
    for ev in log.query(since_id=since_id, since_ts=since_ts, item=args.item,
                        kinds=args.kind or None, repo=args.repo):
        paths = f"  [{', '.join(ev.paths)}]" if ev.paths and args.paths else ""
        print(f"#{ev.id:<5} {clock(ev.ts):>14}  {ev.kind:<14} {describe(ev)}{paths}")
    return 0


def cmd_app(args) -> int:
    from .app import main as app_main     # PySide6 is imported only here
    folders = [_control(f) for f in args.folders]
    if not folders:
        # The usual default, but the current folder only when it is a control folder:
        # the app opens on its remembered projects either way.
        default = _control(None)
        if os.environ.get("CONDUCTOR_CONTROL") or (default / config_mod.CONFIG_NAME).is_file():
            folders = [default]
    return app_main([str(f) for f in folders])


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        # Redirected output on Windows defaults to the ANSI code page; under pythonw
        # there is no stream at all.
        if stream is not None:
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
            sp.add_argument("--since", help="#id, 30m / 2h / 1d, 5:25pm, an ISO time, "
                                            "'checkpoint' (the latest) or 'checkpoint N'")
            sp.add_argument("--item", help="sandbox/001_x, 001_x or 001")
            sp.add_argument("--repo")
            sp.add_argument("--kind", action="append", help="repeatable")
            sp.add_argument("--paths", action="store_true", help="show the paths each event names")
    sp = sub.add_parser("questions", help="list open asks")
    sp.add_argument("control", nargs="?", help="control folder (default: $CONDUCTOR_CONTROL or .)")
    sp.add_argument("--all", action="store_true", help="include resolved questions")
    sp.set_defaults(fn=cmd_questions)
    sp = sub.add_parser("audits", help="audit notes per repo not yet used by a cleanup")
    sp.add_argument("control", nargs="?", help="control folder (default: $CONDUCTOR_CONTROL or .)")
    sp.add_argument("--all", action="store_true", help="include notes already used or claimed")
    sp.set_defaults(fn=cmd_audits)
    sp = sub.add_parser("answer", help="answer a question (writes its answer file)")
    sp.add_argument("id", help="question id, e.g. sandbox__001_x__1 or 001__1")
    sp.add_argument("text", nargs="+", help="the answer")
    sp.add_argument("--control", help="control folder (default: $CONDUCTOR_CONTROL or .)")
    sp.set_defaults(fn=cmd_answer)
    sp = sub.add_parser("tell", help="send a message to a running executor")
    sp.add_argument("item", help="sandbox/001_x, 001_x or 001")
    sp.add_argument("text", nargs="+", help="the message")
    sp.add_argument("--control", help="control folder (default: $CONDUCTOR_CONTROL or .)")
    sp.set_defaults(fn=cmd_tell)
    sp = sub.add_parser("amend", help="add to a queued, staged or running item's instructions")
    sp.add_argument("item", help="sandbox/001_x, 001_x or 001")
    sp.add_argument("text", nargs="+", help="the amendment, or the path of a file holding it")
    sp.add_argument("--control", help="control folder (default: $CONDUCTOR_CONTROL or .)")
    sp.set_defaults(fn=cmd_amend)
    sp = sub.add_parser("checkpoint", help="start a new checkpoint (a session boundary) via a signal")
    sp.add_argument("label", nargs="*", help="e.g. session start")
    sp.add_argument("--control", help="control folder (default: $CONDUCTOR_CONTROL or .)")
    sp.set_defaults(fn=cmd_checkpoint)
    sp = sub.add_parser("open", help="open a finished item's session in a new console (claude --resume)")
    sp.add_argument("item", help="sandbox/001_x, 001_x or 001")
    sp.add_argument("--control", help="control folder (default: $CONDUCTOR_CONTROL or .)")
    sp.set_defaults(fn=cmd_open)
    sp = sub.add_parser("run-target", help="start a [run.<name>] target if needed and open its URL")
    sp.add_argument("name")
    sp.add_argument("--stop", action="store_true", help="stop it instead")
    sp.add_argument("--control", help="control folder (default: $CONDUCTOR_CONTROL or .)")
    sp.set_defaults(fn=cmd_run_target)
    sp = sub.add_parser("pick", help="open a picker manifest in the browser (via the daemon, or served here)")
    sp.add_argument("ref", help="<repo>/<manifest path>")
    sp.add_argument("--control", help="control folder (default: $CONDUCTOR_CONTROL or .)")
    sp.add_argument("--until-saved", action="store_true", help=argparse.SUPPRESS)
    sp.set_defaults(fn=cmd_pick)
    sp = sub.add_parser("init", help="create a new control folder and commit it")
    sp.add_argument("folder")
    sp.add_argument("--project", required=True)
    sp.add_argument("--repo", action="append", metavar="NAME=PATH", help="repeatable")
    sp.set_defaults(fn=cmd_init)
    sp = sub.add_parser("app", help="the desktop app (tray icon, one tab per control folder)")
    sp.add_argument("folders", nargs="*", help="control folders to show (added to the remembered list)")
    sp.set_defaults(fn=cmd_app)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except config_mod.ConfigError as exc:
        print(f"conductor: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
