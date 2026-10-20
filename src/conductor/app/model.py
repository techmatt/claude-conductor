"""The app's data layer. No Qt here, so it is testable headless.

Everything is read: `events.db` strictly read-only (a `mode=ro` URI; a missing file is
an empty log, never created) and the control folder's files. The app's only writes go
through `actions.py`.
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.parse
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .. import config as config_mod
from .. import git, items, memory, picks, questions, state, talk, targets
from ..events import RED_KINDS, EventLog, Event
from ..executor import RunPaths
from ..inbox import checkpoint_name, describe, outline_line, outline_rows, progress_line, reply_to, slow_text
from ..state import DaemonInfo, daemon_info
from ..util import FrontMatterError, clock, now, parse_stamp, read_text, split_front_matter

# Overall state, most urgent first: the tray shows the first that applies anywhere.
PRIORITY = ("red", "asking", "running", "idle")
PHASES = {"exec": "running", "merge": "merging", "concluding": "concluding", "done": "done"}
TEXT_LIMIT = 400_000          # characters of a log, report or diff shown at once
FINISHED_SHOWN = 10           # finished items per repo in Repos (every unreviewed one besides)


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

    def last(self, kind: str, item: str | None = None, repo: str | None = None) -> Event | None:
        return self._call(EventLog.last, None, kind, item=item, repo=repo)

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
    phase: str                  # running | merging | concluding | staging | staged
    started: str | None
    detail: str = ""
    progress: str = ""          # the latest `progress` line, with its time
    session_id: str | None = None
    readonly: bool = False      # runs beside the repo's other items, never merges
    port: int | None = None     # its CONDUCTOR_PORT

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
    pick: str | None = None     # the manifest ref its report's `pick:` line named
    slow: str | None = None     # its report's `slow:`, unless `none` or absent


@dataclass
class RepoState:
    name: str
    path: Path
    running: list[Activity] = field(default_factory=list)
    staged: list[Activity] = field(default_factory=list)
    queued: list[items.Item] = field(default_factory=list)
    action: str = "idle"        # state.decide's action for the queue head
    reason: str = ""
    red: Event | None = None    # the red event holding the queue until `clear`
    finished: list[Finished] = field(default_factory=list)     # newest first
    finished_total: int = 0
    earlier: list[Finished] = field(default_factory=list)      # settled by a checkpoint, newest first
    earlier_total: int = 0
    audit_notes: int = 0        # audit notes no cleanup has used yet

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
    picks: dict[str, picks.PickInfo] = field(default_factory=dict)  # finished item → its picker (`picks.pending`)
    memory: tuple[str, str] = ("", "ok")                       # `memory.line`: text, level

    @property
    def to_pick(self) -> int:
        """Pickers that wait for Matt: named by a current report, not saved since."""
        return sum(1 for p in self.picks.values() if p.state != "saved")


TARGET_PROBE_SECONDS = 0.3      # per target per refresh, on the UI thread: a local server answers at once


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
        out.append(ProposedItem(p.stem, p, int(m.group(1)) if m else 0,
                                None if repo is None else str(repo), summary[:200], errors))
    return sorted(out, key=lambda x: (x.num, x.name))


def _finished(view: state.View, e: Event) -> Finished:
    return Finished(e.item, e.item.split("/", 1)[-1], str(e.detail.get("status")), e.ts,
                    e.item in view.reviews, e.detail.get("carry"), e.detail.get("session_id"),
                    e.detail.get("pick") or None, slow_text(e.detail))


def finished_items(view: state.View, repo: str, recent: int = FINISHED_SHOWN,
                   keep: Collection[str] = ()) -> tuple[list[Finished], int]:
    """A repo's current finished items (not settled by a checkpoint, or in `keep`: items
    whose picker still waits): the `recent` latest plus every older unreviewed or kept
    one, newest first; and how many current ones it has."""
    evs = sorted((e for e in view.finishes.values()
                  if e.repo == repo and (e.item in keep or not view.settled(e.item))),
                 key=lambda e: e.id, reverse=True)
    shown = [e for n, e in enumerate(evs) if n < recent or e.item not in view.reviews or e.item in keep]
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
                  "approve", "cancel", "reorder", "clear", "edited", "request", "request_failed",
                  "daemon_start", "daemon_stop", "progress", "tell", "amended", "delivered", "resolve",
                  "run_start", "run_ready", "run_open", "run_stop", "run_failed",
                  "pick_open", "pick_saved", "pick_failed"}
ALWAYS_KINDS = RED_KINDS | {"warning"}


def is_activity(ev: Event) -> bool:
    return ev.kind in ACTIVITY_KINDS or ev.kind in ALWAYS_KINDS


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
            if d.get("carry") not in (None, "none"):
                text += f" (carry: {d['carry']})"
            if slow := slow_text(d):
                text += f" (slow: {slow})"
            if d.get("pick"):
                text += f" (pick: {d['pick']})"
            return text
        case "reviewed":
            return f"report {name} reviewed{by}"
        case "audit_used":
            return f"{name} used {len(d.get('notes', []))} audit note(s)"
        case "checkpoint":
            return f"new {checkpoint_name(ev)}{by}"
        case "progress":
            return f"{name}{reply_to(ev)}: {progress_text(ev).replace(chr(10), ' · ')}"
        case "tell" if d.get("outline_request"):
            return f"outline request #{ev.id} to {name}{by}"
        case "tell":
            return f"message #{ev.id} to {name}{by}: {d.get('text')}"
        case "delivered":
            if d.get("amend"):
                return f"amendment #{d['amend']} reached {name} (via {d.get('via')})"
            return f"message #{d.get('tell')} reached {name}"
        case "amended":
            return f"amendment #{ev.id} to {name}{by}, while {d.get('state')} ({d.get('delivery')}): {d.get('text')}"
        case "amend_refused":
            return f"amendment to {name} refused{by}: {d.get('reason')}"
        case "ask":
            how = "blocking" if d.get("block") else f"default {d.get('default')!r} at {d.get('deadline_clock')}"
            return f"question asked by {name} ({how}): {d.get('text')}"
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
        waiting = {item for item, p in pending.items() if p.state != "saved"}
        for repo, path in cfg.repos.items():
            rs = RepoState(repo, path, queued=view.queued.get(repo, []), red=view.red_hold.get(repo))
            for rec in view.running.values():
                if rec.get("repo") == repo:
                    a = Activity(rec.get("item", "?"), rec.get("name", "?"), PHASES.get(rec.get("phase", "exec"), rec.get("phase")),
                                 rec.get("started"), rec.get("branch") or "", session_id=rec.get("session_id"),
                                 readonly=bool(rec.get("readonly")), port=rec.get("port"))
                    if a.phase == "running" and (p := self.log.last("progress", item=a.item)):
                        a.progress = f"{progress_line(p)} · {clock(p.ts)}"
                    rs.running.append(a)
            for rec in view.staged.values():
                if rec.get("repo") == repo:
                    rs.staged.append(Activity(rec.get("item", "?"), rec.get("name", "?"), rec.get("phase", "staging"),
                                              rec.get("started"), rec.get("branch") or ""))
            if d := decisions.get(repo):
                rs.action, rs.reason = d.action, d.reason
            rs.finished, rs.finished_total = finished_items(view, repo, keep=waiting)
            rs.earlier, rs.earlier_total = earlier_items(view, repo, skip=waiting)
            rs.audit_notes = len(view.unused_notes(cfg, repo))
            repos.append(rs)
        st, why = self._state(daemon, repos, open_qs)
        run = {name: targets.status(cfg, t, TARGET_PROBE_SECONDS) for name, t in cfg.targets.items()}
        try:
            mem = memory.line(cfg)
        except OSError as exc:
            mem = (f"memory: could not read ({exc})", "ok")
        return Snapshot(cfg.project, cfg.control, daemon, repos, open_qs, resolved, proposed, st, why, error, run,
                        view.checkpoint, pending, mem)

    def scan_picks(self, view: state.View) -> dict[str, picks.PickInfo]:
        """`picks.pending` (the inbox's Picks section reads the same), keyed by item; only
        asked when some report named a manifest, so most refreshes read nothing."""
        if not any(f.detail.get("pick") for f in view.finishes.values()):
            return {}
        try:
            return {p.item: p for p in picks.pending(self.cfg, view, self.log)}
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
        if reds:
            return "red", f"held after red: {', '.join(reds)}"
        if not daemon.alive and (busy or any(r.queued for r in repos)):
            return "red", "daemon down with work queued or running"
        if open_qs:
            return "asking", f"{len(open_qs)} open question(s)"
        if busy:
            n = sum(len(r.running) + len(r.staged) for r in repos)
            return "running", f"{n} item(s) running or staged"
        return "idle", "idle" + ("" if daemon.alive else " (daemon not running)")

    # ------------------------------------------------------------ trail
    def trail(self, item_id: str) -> "Trail":
        return build_trail(self.cfg, self.events, item_id)


# ---------------------------------------------------------------- an item's conversation
@dataclass
class Turn:
    """One entry of an item's Conversation: who said it, and what came back under it
    (a tell's reply, a question's answer, an amendment's delivery)."""
    id: int                     # the event it stands for
    ts: str
    who: str                    # matt | executor | conductor
    title: str
    text: str = ""
    under: list[tuple[str, str, str]] = field(default_factory=list)    # (label, ts, text)
    note: str = ""              # what it still waits for, or why it never arrived
    tone: str = ""              # "" | wait | warn | red | done | partial
    report: bool = False        # a finish: links to the Report tab


# Shown in Conversation; every other kind (delivered, merge, reaped, enqueue, edited,
# commit, worktree, ...) is plumbing and stays in the item's Events tab. Reds other than
# `red` itself (whose reasons the finish carries) show too.
CONVERSATION_KINDS = {"start", "staged", "tell", "amended", "amend_refused", "ask", "progress",
                      "pick_open", "pick_saved", "pick_failed", "finish"}
QUESTION_ENDS = {"answer": "Matt answered", "default": "default taken (nobody answered)",
                 "late": "late answer, ignored", "question_closed": "closed unanswered"}


def progress_text(ev: Event) -> str:
    """A progress line in the thread: its note, and an outline it carries in the compact
    one-line form (the latest is pinned in full above the thread)."""
    text = progress_line(ev)
    if ev.detail.get("note") and ev.detail.get("outline"):
        text += "\n" + outline_line(ev, posted=False)
    return text


def current_outline(events: list[Event]) -> Event | None:
    """The item's latest outline: its newest `progress` event carrying one."""
    return next((e for e in reversed(events) if e.kind == "progress" and outline_rows(e)), None)


def conversation(events: list[Event]) -> list[Turn]:
    """An item's events (oldest first) as a chat thread. Replies sit under their tell:
    a `progress` event tagged `reply_to` is not a turn of its own."""
    replies: dict[int, Event] = {}
    delivered: dict[int, Event] = {}
    undelivered: dict[int, Event] = {}
    ends: dict[str, Event] = {}
    for e in events:
        d = e.detail
        if e.kind == "progress":
            for mid in talk.reply_ids(d):
                replies.setdefault(mid, e)
        elif e.kind == "delivered":
            delivered[d.get("tell") or d.get("amend")] = e
        elif e.kind == "warning" and (d.get("tell") or d.get("amend")):
            undelivered[d.get("tell") or d.get("amend")] = e
        elif e.kind in QUESTION_ENDS and d.get("question"):
            ends.setdefault(d["question"], e)
    talked = {e.id for e in events if e.kind in ("tell", "amended")}
    out: list[Turn] = []
    for e in events:
        d = e.detail
        if e.kind not in CONVERSATION_KINDS and not (e.kind in RED_KINDS and e.kind != "red"):
            continue
        match e.kind:
            case "start":
                if d.get("error"):
                    out.append(Turn(e.id, e.ts, "conductor", "Failed to launch", str(d["error"]), tone="red"))
                else:
                    out.append(Turn(e.id, e.ts, "conductor",
                                    "Resumed its staged session" if d.get("resumed") else "Started",
                                    " · ".join(x for x in (f"on {d['branch']}" if d.get("branch") else "",
                                                           f"port {d['port']}" if d.get("port") else "") if x)))
            case "staged":
                plan = (d.get("plan") or "").strip().splitlines()
                out.append(Turn(e.id, e.ts, "executor", "Staged; waits for the lock", plan[0] if plan else ""))
            case "tell" | "amended":
                amend = e.kind == "amended"
                by = d.get("by")
                who = "conductor" if by == "conductor" else "matt"
                asks = d.get("outline_request")         # the fixed text: the title says it
                what = "Amendment" if amend else "Outline request" if asks else "Message"
                title = f"{what} #{e.id}" + (f" (by {by})" if by and who == "matt" else "")
                t = Turn(e.id, e.ts, who, title, "" if asks else str(d.get("text") or ""))
                if amend:
                    t.title += f", while {d.get('state')}"
                    t.under.append(("delivery", "", str(d.get("delivery") or "")))
                if dv := delivered.get(e.id):
                    t.under.append(("delivered", dv.ts, f"via {dv.detail.get('via')}"
                                    + (f", after {dv.detail['waited_seconds']}s" if dv.detail.get("waited_seconds") is not None else "")))
                if r := replies.get(e.id):
                    t.under.append(("reply", r.ts, progress_text(r)))
                elif w := undelivered.get(e.id):
                    t.note, t.tone = str(w.detail.get("note") or "never delivered"), "warn"
                elif dv and dv.detail.get("via") in ("hook", "progress"):
                    t.note, t.tone = "waiting for a reply", "wait"
                elif not dv:
                    t.note, t.tone = ("waiting for its launch" if amend and d.get("state") != "running"
                                      else "waiting for delivery, after its next tool call"), "wait"
                out.append(t)
            case "ask":
                how = "blocking" if d.get("block") else f"default {d.get('default')!r} at {d.get('deadline_clock')}"
                text = str(d.get("text") or "")
                if d.get("options"):
                    text += "\noptions: " + " / ".join(map(str, d["options"]))
                t = Turn(e.id, e.ts, "executor", f"Question ({how})", text)
                if end := ends.get(d.get("question")):
                    what = end.detail.get("text") if end.kind != "question_closed" else end.detail.get("reason")
                    t.under.append((QUESTION_ENDS[end.kind], end.ts, str(what or "")))
                else:
                    t.note, t.tone = "waiting for an answer", "wait"
                out.append(t)
            case "progress":
                if (ids := talk.reply_ids(d)) and all(i in talked for i in ids):
                    continue                    # shown under its tell
                title = ("Outline" if d.get("outline") and not d.get("note") else "Progress") + (
                    f" (reply to {', '.join(f'#{i}' for i in ids)})" if ids else "")
                out.append(Turn(e.id, e.ts, "executor", title, progress_text(e)))
            case "pick_open" | "pick_saved" | "pick_failed":
                out.append(Turn(e.id, e.ts, "conductor", "Picker", activity_text(e),
                                tone="red" if e.kind == "pick_failed" else ""))
            case "finish":
                status = str(d.get("status"))
                lines = [f"status: {status}" + (f" ({'; '.join(d['reasons'])})" if d.get("reasons") else "")]
                for key in ("blocks", "carry"):
                    lines.append(f"{key}: {d.get(key) or 'none'}")
                lines.append(f"slow: {slow_text(d) or 'none'}")
                if d.get("pick"):
                    lines.append(f"pick: {d['pick']}")
                out.append(Turn(e.id, e.ts, "conductor", f"Finished {status}", "\n".join(lines),
                                tone=status if status in ("done", "partial", "red") else "", report=True))
            case _:
                out.append(Turn(e.id, e.ts, "conductor", e.kind, activity_text(e), tone="red"))
    return out


# ---------------------------------------------------------------- an item's trail
@dataclass
class Trail:
    item: str
    repo: str
    name: str
    where: str                  # queue | running | done | proposed | gone
    item_path: Path | None
    prompt: str
    events: list[Event]
    report: str
    log: str
    stage_log: str


def find_item_file(cfg: config_mod.Config, repo: str, name: str,
                   events: list[Event] = ()) -> tuple[str, Path | None]:
    """Where the item file is now; a cancelled item's copy is found through its
    `cancel` event's `kept`."""
    for where, p in (("running", cfg.path("running", repo, f"{name}.md")),
                     ("queue", cfg.path("queue", repo, f"{name}.md")),
                     ("done", cfg.path("done", repo, f"{name}.md")),
                     ("proposed", cfg.path("proposed", f"{name}.md"))):
        if p.is_file():
            return where, p
    for e in reversed(list(events)):
        if e.kind == "cancel" and e.item == f"{repo}/{name}" and e.detail.get("kept"):
            p = cfg.control / e.detail["kept"]
            if p.is_file():
                return "cancelled", p
    return "gone", None


def _read(path: Path, limit: int = TEXT_LIMIT) -> str:
    try:
        text = read_text(path)
    except (OSError, UnicodeDecodeError):
        return ""
    return text if len(text) <= limit else text[:limit] + f"\n… ({len(text) - limit} more characters)\n"


def build_trail(cfg: config_mod.Config, events: list[Event], item_id: str) -> Trail:
    repo, _, name = item_id.partition("/")
    paths = RunPaths.for_name(cfg, repo, name)
    own = [e for e in events if e.item == item_id]
    where, item_path = find_item_file(cfg, repo, name, own)
    prompt = _read(paths.prompt) if paths.prompt.is_file() else (_read(item_path) if item_path else "")
    return Trail(
        item=item_id, repo=repo, name=name, where=where, item_path=item_path, prompt=prompt,
        events=own,
        report=_read(paths.report),
        log=render_stream_json(paths.log) if paths.log.is_file() else "",
        stage_log=render_stream_json(paths.stage_log) if paths.stage_log.is_file() else "",
    )


def git_trail(cfg: config_mod.Config, item_id: str, events: list[Event]) -> str:
    """The item's commits with their diffs: from its branch while it exists, else from
    the `merge` event (the rebased commits now on main)."""
    repo, _, name = item_id.partition("/")
    path = cfg.repos.get(repo)
    if path is None or not git.is_repo(path):
        return f"repo {repo!r} is not a git repo here"
    branch = f"conductor/{name}"
    main = cfg.opts(repo).main
    try:
        if git.rev(path, branch):
            base = git.run(path, "merge-base", main, branch, check=False).strip()
            spec = f"{base}..{branch}" if base else branch
            text = f"branch {branch} (still exists), commits since {base[:10] or 'its root'}:\n\n"
            text += git.run(path, "log", "--stat", "-p", "--format=commit %H%nDate: %ad%n%n    %s%n",
                            "--date=local", spec, check=False)
            return _cap(text) if text.strip() else f"branch {branch} has no commits of its own"
        merges = [e for e in events if e.item == item_id and e.kind == "merge"]
        if merges:
            ev = merges[-1]
            sha, n = ev.detail.get("sha"), int(ev.detail.get("commits") or 0)
            if not sha or not git.rev(path, sha):
                return f"merged as {sha}, which is no longer in {path}"
            if n <= 0:
                return f"merged at {sha[:10]} with no commits of its own"
            text = f"merged into {main} at {sha[:10]} ({n} commit(s), rebased; branch deleted):\n\n"
            text += git.run(path, "log", "--stat", "-p", "--format=commit %H%nDate: %ad%n%n    %s%n",
                            "--date=local", "-n", str(n), sha, check=False)
            return _cap(text)
        # No worktree (or its branch is gone and nothing merged): the `commit` events
        # name what the item committed straight onto the branch it ran on.
        shas = [e.detail["sha"] for e in events
                if e.item == item_id and e.kind == "commit" and e.detail.get("sha")]
        if shas:
            there = [s for s in shas if git.rev(path, s)]
            text = f"{len(shas)} commit(s) logged for this item (no branch {branch}, no merge)"
            if missing := [s[:10] for s in shas if s not in there]:
                text += f"; no longer in {path}: {', '.join(missing)}"
            text += ":\n\n"
            if there:
                text += git.run(path, "show", "--stat", "-p", "--format=commit %H%nDate: %ad%n%n    %s%n",
                                "--date=local", *there, check=False)
            return _cap(text)
    except git.GitError as exc:
        return f"git: {exc}"
    return f"no branch {branch}, no merge and no commit events: nothing committed survives"


def _cap(text: str) -> str:
    return text if len(text) <= TEXT_LIMIT else text[:TEXT_LIMIT] + "\n… (diff truncated)\n"


# ---------------------------------------------------------------- stream-json
_RENDERED: dict[tuple, str] = {}


def render_stream_json(path: Path, limit: int = TEXT_LIMIT) -> str:
    """An executor's stream-json log as readable text: what it said, each tool call
    and a short form of each result, then the final result line. Cached per file
    version: moving through a trail never re-parses a log that has not changed."""
    try:
        st = path.stat()
    except OSError:
        return ""
    key = (str(path), st.st_mtime_ns, st.st_size, limit)
    if key in _RENDERED:
        return _RENDERED[key]
    text = _render_stream_json(path, limit)
    for k in [k for k in _RENDERED if k[0] == key[0]]:
        del _RENDERED[k]
    if len(_RENDERED) >= 32:
        _RENDERED.pop(next(iter(_RENDERED)))
    _RENDERED[key] = text
    return text


def _render_stream_json(path: Path, limit: int) -> str:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    out: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            out.append(line)
            continue
        out += _render_obj(obj)
    text = "\n".join(out) + "\n"
    return text if len(text) <= limit else text[:limit] + "\n… (log truncated)\n"


def _short(value, n: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = text.strip()
    return text if len(text) <= n else text[:n] + f" … (+{len(text) - n})"


def _tool_input(name: str, data: dict) -> str:
    for key in ("command", "file_path", "pattern", "path", "url", "query", "description"):
        if isinstance(data, dict) and data.get(key):
            return _short(data[key], 200)
    return _short(data, 200)


def _render_obj(obj: dict) -> list[str]:
    t = obj.get("type")
    if t == "system":
        if obj.get("subtype") == "init":
            bits = [f"{k} {obj[k]}" for k in ("session_id", "model", "cwd") if obj.get(k)]
            return ["── " + " · ".join(bits).replace("session_id", "session", 1)]
        return []
    if t == "assistant":
        out = []
        for c in (obj.get("message") or {}).get("content") or []:
            if c.get("type") == "text" and c.get("text", "").strip():
                out.append("")
                out += [f"  {ln}" for ln in c["text"].strip().splitlines()]
            elif c.get("type") == "tool_use":
                out.append(f"→ {c.get('name')}: {_tool_input(c.get('name', ''), c.get('input') or {})}")
        return out
    if t == "user":
        out = []
        content = (obj.get("message") or {}).get("content")
        for c in content if isinstance(content, list) else []:
            if c.get("type") == "tool_result":
                body = c.get("content")
                if isinstance(body, list):
                    body = " ".join(x.get("text", "") for x in body if isinstance(x, dict))
                mark = "✗" if c.get("is_error") else "←"
                out.append(f"    {mark} {_short(body or '', 240)}".replace("\n", " ⏎ "))
        return out
    if t == "result":
        bits = [f"status {obj.get('subtype')}", f"is_error {obj.get('is_error')}"]
        if obj.get("num_turns") is not None:
            bits.append(f"{obj['num_turns']} turns")
        if obj.get("duration_ms") is not None:
            bits.append(f"{obj['duration_ms'] / 1000:.0f}s")
        if obj.get("total_cost_usd") is not None:
            bits.append(f"${obj['total_cost_usd']:.2f}")
        out = ["", f"══ result: {', '.join(bits)}"]
        if obj.get("result"):
            out += [f"  {ln}" for ln in str(obj["result"]).strip().splitlines()]
        return out
    return []
