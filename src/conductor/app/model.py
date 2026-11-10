"""The app's data layer. No Qt here, so it is testable headless.

Everything is read: `events.db` strictly read-only (a `mode=ro` URI; a missing file is
an empty log, never created) and the control folder's files. The app's only writes go
through `actions.py`.
"""

from __future__ import annotations

import functools
import os
import sqlite3
import threading
import urllib.parse
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .. import config as config_mod
from .. import git, items, memory, nightshift, picks, questions, state, targets
from ..daemon_restart import code_version
from ..events import RED_KINDS, Event, EventLog, live_invalids
from ..inbox import checkpoint_name, describe, eta_drift, outline_line, progress_line, reply_to, slow_text
from ..resume import label as resume_label
from ..state import DaemonInfo, daemon_info
from ..util import FrontMatterError, clock, now, parse_stamp, read_text, split_front_matter

# Overall state, most urgent first: the tray shows the first that applies anywhere.
PRIORITY = ("red", "asking", "running", "idle")
PHASES = {"exec": "running", "setup": "setting up", "merge": "merging", "concluding": "concluding", "done": "done"}
FINISHED_SHOWN = 10           # finished items per repo in Repos (every unreviewed one besides)


@functools.cache
def app_code() -> str | None:
    """The code version this app runs: read once, as the daemon reads its own."""
    return code_version()


def restart_due(snaps) -> bool:
    """A daemon runs other code than this app. Running items don't hold it back: "restart
    all" waits for each daemon's safe point and its executors are adopted (71)."""
    return any(s.code_differs for s in snaps)


def ask_how(d: dict) -> str:
    """How an `ask` event's question stands, in a few words."""
    if d.get("design"):
        return f"design, held for Matt; default {d.get('default')!r} applied"
    return "blocking" if d.get("block") else f"default {d.get('default')!r} at {d.get('deadline_clock')}"


def sqlite_uri(path: Path, mode: str = "ro") -> str:
    """A `file:` URI SQLite accepts for any local, mapped-drive or UNC path.

    Not `Path.resolve().as_uri()`: resolve() turns a mapped drive into its UNC form,
    and as_uri() puts a UNC server in the URI authority, which SQLite rejects
    ("invalid uri authority"). UNC goes in the path (`file:////server/share/...`).
    """
    p = os.path.abspath(path).replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p                         # C:/x -> /C:/x
    return "file://" + urllib.parse.quote(p, safe="/:") + f"?mode={mode}"


class ReadOnlyLog:
    """The query half of `EventLog` over a read-only connection.

    `sqlite3.connect("file:…?mode=ro", uri=True)` cannot create the file and cannot
    write it. A missing or unreadable `events.db` reads as an empty log; it is retried
    on every call, so a daemon started later is picked up.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.conn: sqlite3.Connection | None = None

    def _connect(self) -> bool:
        if self.conn is not None:
            return True
        if not self.path.is_file():
            return False
        try:
            conn = sqlite3.connect(sqlite_uri(self.path), uri=True, timeout=5)
            conn.execute("SELECT 1 FROM events LIMIT 1")
        except sqlite3.Error:
            return False
        self.conn = conn
        return True

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def _call(self, fn, default, *args, **kwargs):
        if not self._connect():
            return default
        try:
            return fn(self, *args, **kwargs)
        except sqlite3.Error:
            self.close()
            return default

    def query(self, **kwargs) -> list[Event]:
        return self._call(EventLog.query, [], **kwargs)

    def last(self, kind: str, item: str | None = None, repo: str | None = None, has: str | None = None) -> Event | None:
        return self._call(EventLog.last, None, kind, item=item, repo=repo, has=has)

    def max_id(self) -> int:
        return self._call(EventLog.max_id, 0)

    def top(self) -> int | None:
        """`max_id`, or None when `events.db` exists but could not be read just now
        (busy, locked, unreadable): unknown, which is not the same as empty."""
        if not self.path.is_file():
            return 0
        return self._call(EventLog.max_id, None)

    read_mark = EventLog.read_mark


@dataclass
class Activity:
    """A running, merging, staging or staged item, as the Repos panel shows it."""
    item: str
    name: str
    phase: str                  # running | setting up | merging | concluding | staging | staged
    started: str | None
    detail: str = ""
    progress: str = ""          # the latest `progress` line, with its time
    session_id: str | None = None
    readonly: bool = False      # runs beside the repo's other items, never merges
    port: int | None = None     # its CONDUCTOR_PORT
    alongside: str | None = None    # its `alongside` line; `beside`: what it started beside
    beside: list[str] = field(default_factory=list)
    resumed: str = ""           # a resume item's `resume of <orig>` / `post-audit of <orig>`

    def elapsed(self, at: datetime | None = None) -> float:
        if not self.started:
            return 0.0
        return ((at or now()) - parse_stamp(self.started)).total_seconds()


@dataclass
class Finished:
    """A finished item, as the Repos panel lists it under its queue."""
    item: str
    name: str
    status: str
    ts: str                     # its `finish` event's time
    reviewed: bool
    carry: str | None = None
    session_id: str | None = None
    pick: str | None = None     # the manifest ref its report's first `pick:` line named
    picks: list[str] = field(default_factory=list)     # every one, a line each (`picks.refs`)
    slow: str | None = None     # its report's `slow:`, unless `none` or absent
    resumed: str = ""           # a resume item's `resume of <orig>` / `post-audit of <orig>`


@dataclass
class RepoState:
    name: str
    path: Path
    running: list[Activity] = field(default_factory=list)
    staged: list[Activity] = field(default_factory=list)
    queued: list[items.Item] = field(default_factory=list)     # the scheduler's order, set-aside items last
    set_aside: dict[str, str] = field(default_factory=dict)   # queued id → why it holds itself alone
    action: str = "idle"        # state.decide's action for the queue head
    reason: str = ""
    stage_dropped: str = ""     # the head's stage was discarded or failed: it launches fresh
    red: Event | None = None    # the red event holding the queue until `clear`
    finished: list[Finished] = field(default_factory=list)     # newest first
    finished_total: int = 0
    earlier: list[Finished] = field(default_factory=list)      # settled by a checkpoint, newest first
    earlier_total: int = 0
    audit_notes: int = 0        # audit notes no cleanup has used yet
    no_remote: bool = False     # the repo has no git remote yet (91)

    @property
    def unreviewed(self) -> int:
        return sum(1 for f in self.finished if not f.reviewed)


@dataclass
class ProposedItem:
    name: str
    path: Path
    num: int
    repo: str | None
    summary: str
    errors: list[str]


@dataclass
class Snapshot:
    project: str
    control: Path
    daemon: DaemonInfo
    repos: list[RepoState]
    open_questions: list[questions.Question]
    resolved_questions: list[questions.Question]
    proposed: list[ProposedItem]
    state: str
    why: str
    error: str | None = None
    targets: dict[str, str] = field(default_factory=dict)      # run target → up | starting | down
    checkpoint: Event | None = None                            # the latest `checkpoint` event
    picks: dict[str, list[picks.PickInfo]] = field(default_factory=dict)  # finished item → its pickers (`picks.pending`)
    memory: tuple[str, str] = ("", "ok")                       # `memory.line`: text, level
    autopilot: tuple[str, str] = ("always", "")                # mode, what it starts next (`nightshift.plan`)
    rejected: list[Event] = field(default_factory=list)        # `events.live_invalids`: the header's "N rejected"
    repo_names: list[str] = field(default_factory=list)        # every configured repo, for Pause/Resume
    paused: dict[str, Event] = field(default_factory=dict)     # repo → its `pause` event (`View.paused`)
    tools: dict[str, str] = field(default_factory=dict)        # tool → running | down (`targets.tool_status`)
    code_differs: bool = False                                 # the daemon runs other code than this app
    item_done: set[str] = field(default_factory=set)            # open questions whose item finished (`questions.item_done`)

    @property
    def to_pick(self) -> int:
        """Pickers that wait for Matt: named by a current report, not saved since."""
        return sum(1 for ps in self.picks.values() for p in ps if p.state != "saved")


TARGET_PROBE_SECONDS = 0.3      # per target per refresh, off the UI thread (`TargetProbe`)


class TargetProbe:
    """Run targets' `targets.status`, off the UI thread. A closed localhost port costs a
    probe its whole timeout, twice over on Windows (`::1`, then `127.0.0.1`): on the UI
    thread that froze the app for ~0.4 s per target per refresh, typing included. Each
    call starts a fresh probe unless one for that target is still out, and answers with
    the newest result; before the first one lands, the record alone (`starting`/`down`)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._seen: dict[str, str] = {}
        self._out: dict[str, threading.Thread] = {}

    def status(self, cfg: config_mod.Config, t) -> str:
        with self._lock:
            known = self._seen.get(t.name)
            out = self._out.get(t.name)
            if out is None or not out.is_alive():
                out = threading.Thread(target=self._probe, args=(cfg, t), daemon=True,
                                       name=f"probe-{t.name}")
                self._out[t.name] = out
                out.start()
        if known is None:
            return "starting" if targets.process(cfg, t.name) else "down"
        return known

    def _probe(self, cfg: config_mod.Config, t) -> None:
        try:
            s = targets.status(cfg, t, TARGET_PROBE_SECONDS)
        except Exception:                   # noqa: BLE001 - a probe thread must never die loud
            s = "down"
        with self._lock:
            self._seen[t.name] = s

    def settle(self, timeout: float = 5.0) -> None:
        """Wait for probes still out (tests)."""
        with self._lock:
            out = list(self._out.values())
        for th in out:
            th.join(timeout)


def scan_proposed(cfg: config_mod.Config) -> list[ProposedItem]:
    d = cfg.path("proposed")
    out = []
    for p in sorted(d.glob("*.md")) if d.is_dir() else []:
        m = items.NAME_RE.match(p.stem)
        repo, errors, summary = None, [], ""
        try:
            meta, body = split_front_matter(read_text(p))
            repo = meta.get("repo")
            summary = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
        except (FrontMatterError, OSError, UnicodeDecodeError) as exc:
            errors.append(str(exc))
        if repo is not None and not errors:
            errors += items.parse(p, str(repo)).errors
            if repo not in cfg.repos:
                errors.append(f"repo {repo!r} is not in conductor.toml")
        elif not errors:
            errors.append("front-matter `repo:` is required")
        out.append(ProposedItem(p.stem, p, int(m.group(1)) if m and m.group(1).isdigit() else 0,
                                None if repo is None else str(repo), summary[:200], errors))
    return sorted(out, key=lambda x: (x.num, x.name))


def _finished(view: state.View, e: Event) -> Finished:
    return Finished(e.item, e.item.split("/", 1)[-1], str(e.detail.get("status")), e.ts,
                    view.reviewed(e.item), e.detail.get("carry"), e.detail.get("session_id"),
                    e.detail.get("pick") or None, picks.refs(e.detail), slow_text(e.detail), resume_label(e.detail))


def finished_items(view: state.View, repo: str, recent: int = FINISHED_SHOWN,
                   keep: Collection[str] = ()) -> tuple[list[Finished], int]:
    """A repo's current finished items (not settled by a checkpoint, or in `keep`: items
    whose picker still waits): the `recent` latest plus every older unreviewed or kept
    one, newest first; and how many current ones it has."""
    evs = sorted((e for e in view.finishes.values()
                  if e.repo == repo and (e.item in keep or not view.settled(e.item))),
                 key=lambda e: e.id, reverse=True)
    # Only the latest watch (docs/night-shift.md): one an hour would crowd out the work.
    watches = [e for e in evs if e.detail.get("template") == "watch"]
    evs = [e for e in evs if e.detail.get("template") != "watch" or e is watches[0]]
    shown = [e for n, e in enumerate(evs) if n < recent or not view.reviewed(e.item) or e.item in keep]
    return [_finished(view, e) for e in shown], len(evs)


def earlier_items(view: state.View, repo: str, recent: int = FINISHED_SHOWN,
                  skip: Collection[str] = ()) -> tuple[list[Finished], int]:
    """A repo's items settled by a checkpoint (finished and reviewed before the latest
    one), less those in `skip`: the `recent` latest, newest first; and how many there are."""
    evs = sorted((e for e in view.finishes.values()
                  if e.repo == repo and view.settled(e.item) and e.item not in skip),
                 key=lambda e: e.id, reverse=True)
    return [_finished(view, e) for e in evs[:recent]], len(evs)


# ---------------------------------------------------------------- Activity
# Workflow-level kinds the Events tab shows by default; every other kind (worktree,
# stage, merge, commit, hold, enqueue, read, ...) only with Advanced. A red, a warning
# or an invalid event is always activity, whatever its kind.
ACTIVITY_KINDS = {"start", "finish", "reviewed", "audit_used", "checkpoint", "ask", "answer", "default", "late", "question_closed",
                  "approve", "cancel", "reorder", "clear", "edited", "request", "request_failed", "daemon_restart",
                  "daemon_start", "daemon_stop", "progress", "tell", "amended", "delivered", "resolve",
                  "run_start", "run_ready", "run_open", "run_stop", "run_failed",
                  "pick_open", "pick_saved", "pick_failed", "opened", "open_refused", "notify", "closeout_push",
                  "pause", "resume", "watch_answer"}
ALWAYS_KINDS = RED_KINDS | {"warning", "push_failed"}


def is_activity(ev: Event) -> bool:
    return ev.kind in ACTIVITY_KINDS or ev.kind in ALWAYS_KINDS


# A message's sender, from the `by:` its event records. Only the app's buttons are Matt
# (`by: app`); `cli` is whoever typed a `conductor tell`, Claude Code sessions included.
SENDERS = {"app": "Matt", "planner": "Planner", "watch": "Watch", "conductor": "Conductor", "cli": "CLI"}


def sender(by: str | None) -> str:
    return SENDERS.get(by or "") or (by or "unknown").capitalize()


def progress_text(ev: Event) -> str:
    """A progress line in the thread: its note, and an outline it carries in the compact
    one-line form (the latest is pinned in full above the thread)."""
    text = progress_line(ev)
    if ev.detail.get("note") and ev.detail.get("outline"):
        text += "\n" + outline_line(ev, posted=False)
    return text


def activity_text(ev: Event) -> str:
    """An activity event worded plainly; any other kind falls back to `describe`."""
    d = ev.detail
    name = (ev.item or "").split("/", 1)[-1] or ev.repo or ""
    by = f" (by {d['by']})" if d.get("by") else ""
    match ev.kind:
        case "start":
            if d.get("error"):
                return f"prompt {name} failed to launch: {d['error']}"
            return f"prompt {name} started" + (" (resuming its staged session)" if d.get("resumed") else "")
        case "finish":
            text = f"report {name} written — {d.get('status')}"
            if resumed := resume_label(d):
                text += f" ({resumed})"
            if d.get("carry") not in (None, "none"):
                text += f" (carry: {d['carry']})"
            if slow := slow_text(d):
                text += f" (slow: {slow})"
            if refs := picks.refs(d):
                text += f" (pick: {', '.join(refs)})"
            return text
        case "reviewed":
            return f"report {name} reviewed{by}"
        case "audit_used":
            if not ev.item:
                return f"{len(d.get('notes', []))} audit note(s) of {name} marked used{by}"
            return f"{name} used {len(d.get('notes', []))} audit note(s)"
        case "checkpoint":
            return f"new {checkpoint_name(ev)}{by}"
        case "progress":
            return f"{name}{reply_to(ev)}: {progress_text(ev).replace(chr(10), ' · ')}"
        case "tell" if d.get("outline_request"):
            return f"outline request #{ev.id} to {name} from {sender(d.get('by'))}"
        case "tell" if d.get("pause") or d.get("resume"):
            return f"{'pause' if d.get('pause') else 'resume'} message #{ev.id} to {name} from {sender(d.get('by'))}"
        case "tell":
            answers = f" (as the answer to {d['answers']})" if d.get("answers") else ""
            return f"message #{ev.id} to {name} from {sender(d.get('by'))}{answers}: {d.get('text')}"
        case "delivered":
            if d.get("amend"):
                return f"amendment #{d['amend']} reached {name} (via {d.get('via')})"
            return f"message #{d.get('tell')} reached {name}"
        case "amended":
            return f"amendment #{ev.id} to {name} from {sender(d.get('by'))}, while {d.get('state')} ({d.get('delivery')}): {d.get('text')}"
        case "amend_refused":
            return f"amendment to {name} refused{by}: {d.get('reason')}"
        case "ask":
            return f"question asked by {name} ({ask_how(d)}): {d.get('text')}"
        case "answer":
            return f"question answered{by}: {d.get('text')}"
        case "default":
            return f"default taken (nobody answered): {d.get('text')}"
        case "late":
            return f"late answer, ignored: {d.get('text')}"
        case "question_closed":
            return f"question closed unanswered: {d.get('reason') or d.get('outcome')}"
        case "approve":
            return f"{name} approved{by}"
        case "cancel":
            return f"{name} cancelled{by}"
        case "reorder":
            return f"{ev.repo} reordered: {', '.join(d.get('after', []))}{by}"
        case "clear":
            return f"queue {ev.repo} cleared{by}"
        case "request":
            msg = (d.get("message") or "").splitlines()[0] if d.get("message") else d.get("request")
            return f"request applied, nothing changed: {msg}" if d.get("no_change") else f"doc update committed: {msg}"
        case "request_failed":
            return f"request failed: {d.get('error')}"
        case "daemon_start":
            return "daemon started"
        case "daemon_stop":
            return "daemon stopped" + by
        case "daemon_restart":
            return f"daemon restarting: {d.get('reason')}"
        case "run_start":
            return f"{d.get('target')} starting{by}"
        case "run_ready":
            return f"{d.get('target')} is up (after {d.get('waited_seconds')}s)"
        case "run_open":
            return f"{d.get('target')} opened in the browser: {d.get('url')}{by}"
        case "run_stop":
            return f"{d.get('target')} stopped{by}"
        case "pick_open":
            text = f"picker {d.get('name')} opened in the browser: {d.get('url')}{by}"
            return text + (" — the browser did not open" if d.get("opened") is False else "")
        case "pick_saved":
            notes = d.get("notes")
            return (f"picker {d.get('name')} saved: {d.get('changed')} of {d.get('rows')} changed, "
                    f"{notes} note{'' if notes == 1 else 's'}")
        case "pick_failed":
            return f"picker {d.get('manifest')} failed: {d.get('error')}"
        case "opened":
            return f"opened ({d.get('verb')}) {d.get('url') or d.get('path')}{by}"
        case "open_refused":
            return f"open {d.get('verb')} refused: {d.get('reason')}{by}"
        case "notify":
            return f"toast: {d.get('text')}{by}"
    return describe(ev)


def overall(states: list[str]) -> str:
    """The most urgent of several states (`idle` when there are none)."""
    return min(states, key=PRIORITY.index, default="idle")


class Project:
    """One control folder: its config, a read-only event log and what derives from them."""

    def __init__(self, control: Path):
        self.control = Path(control).resolve()
        self.cfg = config_mod.load(self.control)
        self._cfg_mtime = self._toml_mtime()
        self.log = ReadOnlyLog(self.cfg.events_db)
        self.events: list[Event] = []          # every event seen so far, in id order
        self.last_id = 0
        self.generation = 0                    # bumped when the log was replaced: views reset
        self._kinds: set[str] = set()
        self.checkpoint: Event | None = None   # the latest `checkpoint` event seen so far
        self._qcache: dict[Path, tuple[tuple, questions.Question | None]] = {}
        self.probe = TargetProbe()

    @property
    def name(self) -> str:
        return self.cfg.project

    def close(self) -> None:
        self.log.close()

    def _toml_mtime(self) -> float:
        try:
            return (self.control / config_mod.CONFIG_NAME).stat().st_mtime
        except OSError:
            return 0.0

    def reload_config(self) -> None:
        """Pick up a changed conductor.toml; a broken one keeps the previous config."""
        mtime = self._toml_mtime()
        if mtime != self._cfg_mtime:
            self._cfg_mtime = mtime
            try:
                self.cfg = config_mod.load(self.control)
            except config_mod.ConfigError:
                pass

    # ------------------------------------------------------------ events
    def poll_events(self) -> list[Event]:
        """Events newer than the last one seen (cheap: `id > last`). A read that fails
        (the log busy for a moment) is no change, never a replaced log."""
        top = self.log.top()
        if top is None:
            return []
        if top < self.last_id:                  # the log really was replaced: start over
            self.events, self.last_id, self._kinds, self.checkpoint = [], 0, set(), None
            self.generation += 1
        new = self.log.query(since_id=self.last_id)
        if new:
            self.events += new
            self.last_id = new[-1].id
            self._kinds |= {e.kind for e in new}
            self.checkpoint = next((e for e in reversed(new) if e.kind == "checkpoint"), self.checkpoint)
        return new

    def filter_events(self, repo: str | None = None, item: str | None = None,
                      kinds: list[str] | None = None, since: datetime | None = None,
                      until: datetime | None = None, reds: bool = False, advanced: bool = True,
                      events: list[Event] | None = None, from_id: int | None = None) -> list[Event]:
        """`advanced=False` keeps only Activity events (`is_activity`); `from_id` keeps
        events from that id on (a checkpoint's own event included)."""
        from ..events import item_matches
        out = []
        for ev in self.events if events is None else events:
            if from_id is not None and ev.id < from_id:
                continue
            if not advanced and not is_activity(ev):
                continue
            if repo and ev.repo != repo:
                continue
            if item and not (ev.item and (item in ev.item or item_matches(ev.item, item))):
                continue
            if kinds and ev.kind not in kinds:
                continue
            if reds and ev.kind not in RED_KINDS:
                continue
            if since or until:
                ts = parse_stamp(ev.ts)
                if (since and ts < since) or (until and ts > until):
                    continue
            out.append(ev)
        return out

    def kinds(self) -> list[str]:
        return sorted(self._kinds)

    # ------------------------------------------------------------ state
    def snapshot(self) -> Snapshot:
        self.reload_config()
        cfg = self.cfg
        daemon = daemon_info(cfg)
        qs = self.scan_questions()
        open_qs = [q for q in qs if q.is_open]
        resolved = sorted((q for q in qs if not q.is_open),
                          key=lambda q: str(q.result.get("at", "")), reverse=True)
        proposed = scan_proposed(cfg)
        repos: list[RepoState] = []
        error = None
        try:
            view = state.scan(cfg, self.log)
            decisions = state.decide(cfg, view)
        except (OSError, ValueError, KeyError) as exc:     # a record caught mid-write
            view, decisions, error = state.View(), {}, f"could not read the queue: {exc}"
        pending = self.scan_picks(view)
        # An item whose picker waits stays current and shown, however old or settled.
        waiting = {item for item, ps in pending.items() if any(p.state != "saved" for p in ps)}
        no_remote = set(git.remote_missing(cfg.repos))     # asked at most once a minute
        for repo, path in cfg.repos.items():
            aside = {i.id: view.set_aside[i.id] for i in view.queued.get(repo, []) if i.id in view.set_aside}
            rs = RepoState(repo, path, queued=view.queue(repo) + [i for i in view.queued.get(repo, []) if i.id in aside],
                           set_aside=aside, red=view.red_hold.get(repo))
            for rec in view.running.values():
                if rec.get("repo") == repo:
                    a = Activity(rec.get("item", "?"), rec.get("name", "?"), PHASES.get(rec.get("phase", "exec"), rec.get("phase")),
                                 rec.get("started"), rec.get("branch") or "", session_id=rec.get("session_id"),
                                 readonly=bool(rec.get("readonly")), port=rec.get("port"),
                                 alongside=rec.get("alongside"), beside=rec.get("beside") or [],
                                 resumed=resume_label(rec))
                    if a.phase == "running" and (p := self.log.last("progress", item=a.item)):
                        a.progress = f"{progress_line(p)} · {clock(p.ts)}"
                        if drift := eta_drift(self.log, a.item):
                            a.progress += f" · {drift}"
                    rs.running.append(a)
            for rec in view.staged.values():
                if rec.get("repo") == repo:
                    rs.staged.append(Activity(rec.get("item", "?"), rec.get("name", "?"), rec.get("phase", "staging"),
                                              rec.get("started"), rec.get("branch") or ""))
            if d := decisions.get(repo):
                rs.action, rs.reason = d.action, d.reason
            if rs.action == "stage" and rs.queued:
                # decide() says stage, but the daemon will not restage an item whose stage it dropped.
                head = rs.queued[0].id
                w, s = self.log.last("warning", item=head), self.log.last("stage", item=head)
                note = str(w.detail.get("note", "")) if w and (s is None or w.id > s.id) else ""
                if note.startswith(("stage discarded", "could not stage")):
                    rs.stage_dropped = note
            rs.finished, rs.finished_total = finished_items(view, repo, keep=waiting)
            rs.earlier, rs.earlier_total = earlier_items(view, repo, skip=waiting)
            rs.audit_notes = len(view.unused_notes(cfg, repo))
            rs.no_remote = repo in no_remote
            repos.append(rs)
        st, why = self._state(daemon, repos, open_qs)
        run = {name: self.probe.status(cfg, t) for name, t in cfg.targets.items()}
        try:
            mem = memory.line(cfg)
        except OSError as exc:
            mem = (f"memory: could not read ({exc})", "ok")
        try:
            pilot = (nightshift.autopilot(self.log), nightshift.plan(cfg, self.log, view))
        except (OSError, ValueError) as exc:
            pilot = (nightshift.autopilot(self.log), f"could not read the backlog: {exc}")
        return Snapshot(cfg.project, cfg.control, daemon, repos, open_qs, resolved, proposed, st, why, error, run,
                        view.checkpoint, pending, mem, pilot, live_invalids(self.log), list(cfg.repos), view.paused,
                        {name: targets.tool_status(cfg, name) for name in cfg.tools},
                        daemon.alive and self._daemon_code() not in (None, app_code()),
                        questions.item_done(open_qs, view.finishes))

    def _daemon_code(self) -> str | None:
        """The code version the newest daemon started on, when it said."""
        ev = self.log.last("daemon_start")
        return ev.detail.get("code") if ev else None

    def scan_picks(self, view: state.View) -> dict[str, list[picks.PickInfo]]:
        """`picks.pending` (the inbox's Picks section reads the same), by item, a picker per
        `pick:` line; only asked when some report named a manifest, so most refreshes read nothing."""
        if not any(f.detail.get("pick") for f in view.finishes.values()):
            return {}
        try:
            out: dict[str, list[picks.PickInfo]] = {}
            for p in picks.pending(self.cfg, view, self.log):
                out.setdefault(p.item, []).append(p)
            return out
        except (OSError, ValueError):            # a choices file caught mid-write
            return {}

    def scan_questions(self) -> list[questions.Question]:
        """`questions.scan`, re-parsing only files whose question or result changed:
        resolved questions are never archived, so a full parse per tick grows forever."""
        d = questions.qdir(self.cfg.control)
        out, seen = [], set()
        for p in d.glob("*.md") if d.is_dir() else []:
            if p.name.endswith(".answer.md"):
                continue
            try:
                st = p.stat()
                r = p.with_name(p.name.removesuffix(".md") + ".result.json")
                rst = r.stat() if r.exists() else None
            except OSError:
                continue
            key = (st.st_mtime_ns, st.st_size, rst and (rst.st_mtime_ns, rst.st_size))
            seen.add(p)
            hit = self._qcache.get(p)
            if hit is None or hit[0] != key or hit[1] is None:
                hit = (key, questions.load(p))
                self._qcache[p] = hit
            if hit[1] is not None:
                out.append(hit[1])
        for gone in set(self._qcache) - seen:
            del self._qcache[gone]
        return sorted(out, key=lambda q: q.asked)

    @staticmethod
    def _state(daemon: DaemonInfo, repos: list[RepoState], open_qs: list) -> tuple[str, str]:
        reds = [r.name for r in repos if r.red]
        busy = any(r.running or r.staged for r in repos)
        counts = [(sum(len(r.running) for r in repos), "running"), (sum(len(r.staged) for r in repos), "staged"),
                  (sum(1 for r in repos for q in r.queued if q.id not in {s.item for s in r.staged}), "queued")]
        if reds:
            return "red", f"held after red: {', '.join(reds)}"
        if not daemon.alive and (busy or counts[2][0]):
            # The header's daemon label says it is down: this says what waits on it.
            return "red", " · ".join(f"{n} {k}" for n, k in counts if n) + " with the daemon down"
        if open_qs := [q for q in open_qs if not q.design]:     # a design question holds nothing up
            return "asking", f"{len(open_qs)} open question(s)"
        if busy:
            return "running", " · ".join(f"{n} {k}" for n, k in counts[:2] if n)
        return "idle", "idle" + ("" if daemon.alive else " (daemon not running)")

    # ------------------------------------------------------------ trail
    def trail(self, item_id: str) -> "Trail":       # noqa: F821 (item_model.Trail)
        from .item_model import build_trail     # item_model imports this module
        return build_trail(self.cfg, self.events, item_id)

