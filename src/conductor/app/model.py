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
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .. import config as config_mod
from .. import git, items, procs, questions, state
from ..events import RED_KINDS, EventLog, Event
from ..executor import RunPaths
from ..util import FrontMatterError, now, parse_stamp, read_text, split_front_matter

# Overall state, most urgent first: the tray shows the first that applies anywhere.
PRIORITY = ("red", "asking", "running", "idle")
PHASES = {"exec": "running", "merge": "merging", "concluding": "concluding", "done": "done"}
TEXT_LIMIT = 400_000          # characters of a log, report or diff shown at once


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
class DaemonInfo:
    alive: bool
    pid: int | None = None
    started: str | None = None


def daemon_info(cfg: config_mod.Config) -> DaemonInfo:
    lock = cfg.state_dir / "daemon.json"
    try:
        info = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DaemonInfo(False)
    if not isinstance(info, dict):
        return DaemonInfo(False)
    if procs.alive(info.get("pid"), info.get("create_time")):
        return DaemonInfo(True, info.get("pid"), info.get("started"))
    return DaemonInfo(False)


@dataclass
class Activity:
    """A running, merging, staging or staged item, as the Repos panel shows it."""
    item: str
    name: str
    phase: str                  # running | merging | concluding | staging | staged
    started: str | None
    detail: str = ""

    def elapsed(self, at: datetime | None = None) -> float:
        if not self.started:
            return 0.0
        return ((at or now()) - parse_stamp(self.started)).total_seconds()


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
            self.events, self.last_id, self._kinds = [], 0, set()
            self.generation += 1
        new = self.log.query(since_id=self.last_id)
        if new:
            self.events += new
            self.last_id = new[-1].id
            self._kinds |= {e.kind for e in new}
        return new

    def filter_events(self, repo: str | None = None, item: str | None = None,
                      kinds: list[str] | None = None, since: datetime | None = None,
                      until: datetime | None = None, reds: bool = False,
                      events: list[Event] | None = None) -> list[Event]:
        from ..events import item_matches
        out = []
        for ev in self.events if events is None else events:
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
        for repo, path in cfg.repos.items():
            rs = RepoState(repo, path, queued=view.queued.get(repo, []), red=view.red_hold.get(repo))
            for rec in view.running.values():
                if rec.get("repo") == repo:
                    rs.running.append(Activity(rec.get("item", "?"), rec.get("name", "?"), PHASES.get(rec.get("phase", "exec"), rec.get("phase")),
                                               rec.get("started"), rec.get("branch") or ""))
            for rec in view.staged.values():
                if rec.get("repo") == repo:
                    rs.staged.append(Activity(rec.get("item", "?"), rec.get("name", "?"), rec.get("phase", "staging"),
                                              rec.get("started"), rec.get("branch") or ""))
            if d := decisions.get(repo):
                rs.action, rs.reason = d.action, d.reason
            repos.append(rs)
        st, why = self._state(daemon, repos, open_qs)
        return Snapshot(cfg.project, cfg.control, daemon, repos, open_qs, resolved, proposed, st, why, error)

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
