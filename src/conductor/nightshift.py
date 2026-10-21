"""The night shift's facts, shared by the daemon (`daemon_watch`), the inbox and the digest:
the backlog, the night and its budget, the back-to-back rule, posted ETAs, and whether a
planner session is active (docs/night-shift.md)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from . import audits, items
from .config import Config
from .events import Event, EventLog
from .items import Item
from .state import View
from .util import clock, now, parse_stamp

FULL_HOURS = 2.0        # an item declaring `max_hours` at least this is full-length
WATCH = "watch"         # the watch item's template


# ---------------------------------------------------------------- backlog
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
NUMBER_ERROR = "file name must be NNN_name.md"


def parse(path, repo: str) -> Item:
    """A backlog file as an item. Its name needs no number (`bug_sweep.md`, `2_bug_sweep.md`):
    a draw gives it the queue's next one."""
    item = items.parse(path, repo)
    if NAME_RE.fullmatch(path.stem):
        item.errors = [e for e in item.errors if e != NUMBER_ERROR]
    return item


def backlog(cfg: Config) -> dict[str, list[Item]]:
    """Each repo's backlog items, `backlog/<repo>/*.md`, in name order (numbers as numbers)."""
    out = {}
    for repo in cfg.repos:
        files = items.item_files(cfg.path("backlog", repo))
        out[repo] = [parse(p, repo) for p in sorted(files, key=lambda p: items.amend_key(p.stem))]
    return out


def queue_name(cfg: Config, view: View, repo: str, stem: str) -> str:
    """`stem` (`bug_sweep` or `07_bug_sweep`) as the repo's next item: `NNN_bug_sweep`, NNN one
    past every number queued, running, done or proposed there, padded like the widest of them
    (3 at least). A number the planner picked but had not written yet can still be shared: then
    neither item answers to the bare number (`items.resolve_ref`), and the planner's receipt says so."""
    rest = re.sub(r"^\d+_", "", stem)
    names = [i.split("/", 1)[1] for i in view.known() if i.startswith(repo + "/")]
    names += [p.stem for p in items.item_files(cfg.path("proposed")) if items.front_repo(p) == repo]
    nums = [m.group(1) for n in names if (m := items.NAME_RE.match(n))]
    width = max([3, *(len(n) for n in nums)])
    return f"{max([0, *(int(n) for n in nums)]) + 1:0{width}d}_{rest}"


def full_length(meta: dict) -> bool:
    """A template run (bug hunt, cleanups, audits; never a watch) or a declared `max_hours` of
    2 or more. Only a declared one: the projects' default `max_hours` is 2, which would make
    every item full-length."""
    tmpl = audits.canonical(meta.get("template"))
    return (tmpl is not None and tmpl != WATCH) or float(meta.get("max_hours") or 0) >= FULL_HOURS


def last_full(log: EventLog, repo: str) -> Event | None:
    """The `start` of the repo's latest full-length item (its `enqueue` carries the front-matter)."""
    meta = {e.item: e.detail for e in log.query(kinds=["enqueue"], repo=repo)}
    return next((e for e in reversed(log.query(kinds=["start"], repo=repo))
                 if not e.detail.get("error") and full_length(meta.get(e.item, e.detail))), None)


MATT_BY = ("app", "cli")    # a signal file `by:` these is Matt's own hand


def matt_since(cfg: Config, log: EventLog, start: Event) -> str | None:
    """What Matt did after `start`, or None: a signal file `by: app` or `cli`, or a planner
    write that carries his approval (an approval, a queue write but a draw or a watch, an
    answer, a backlog file written since). A request or any other planner signal
    (`review:`, `reviewed:`, `tell:` …) is bookkeeping and doesn't count."""
    rows = log.conn.execute(
        "SELECT * FROM events WHERE id > ? AND (kind IN ('approve', 'answer', 'enqueue', 'edited') OR "
        "(paths LIKE '[\"signals/%' AND json_extract(detail, '$.by') IN (?, ?))) ORDER BY id",
        (start.id, *MATT_BY)).fetchall()
    conductors = {e.item for e in drawn(log)}
    for ev in map(Event.from_row, rows):
        if ev.kind in ("enqueue", "edited") and (ev.item in conductors or is_watch(ev.detail)):
            continue                    # conductor's own queue writes
        return f"{ev.kind} #{ev.id}"
    since = parse_stamp(start.ts).timestamp()
    for item in (i for queue in backlog(cfg).values() for i in queue):
        try:
            if item.path.stat().st_mtime > since:
                return f"backlog {cfg.rel(item.path)}"
        except OSError:
            continue
    return None


# ---------------------------------------------------------------- the night
def night(cfg: Config, at: datetime | None = None) -> tuple[datetime, datetime] | None:
    """The night `at` falls in, as (start, end), or None in the day. Hours are local; an end
    hour at or below the start hour ends the next morning (18 → 8)."""
    at = at or now()
    s, e = cfg.night_start_hour, cfg.night_end_hour
    # Naive local wall-clock times, made aware one by one: right across a DST change.
    midnight = at.astimezone().replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    for day in (-1, 0):
        start = (midnight + timedelta(days=day, hours=s)).astimezone()
        end = (midnight + timedelta(days=day + (1 if e <= s else 0), hours=e)).astimezone()
        if start <= at < end:
            return start, end
    return None


def drawn(log: EventLog) -> list[Event]:
    return log.query(kinds=["backlog_draw"])


def spent_hours(log: EventLog, window: tuple[datetime, datetime], at: datetime | None = None) -> float:
    """Executor-hours of backlog items run inside `window` (up to `at`): each drawn item's
    `start` to its `finish`, or to now while it runs."""
    at = at or now()
    lo, hi = window[0], min(window[1], at)
    total = 0.0
    for d in drawn(log):
        start = log.last("start", item=d.item)
        if start is None or start.id < d.id:
            continue
        fin = log.last("finish", item=d.item)
        a, b = max(parse_stamp(start.ts), lo), min(parse_stamp(fin.ts) if fin else at, hi)
        total += max((b - a).total_seconds(), 0) / 3600
    return total


# ---------------------------------------------------------------- the watch
def parse_eta(text: str | None, posted: datetime) -> datetime | None:
    """A progress line's `eta` as a time: a clock time (`5:40pm`, `~11:20am`, `17:40`) on the
    day it was posted (the next day if that is well before the posting), or a duration from
    the posting (`25m`, `1h`, `1h30m`). None for anything else."""
    t = str(text or "").strip().lower().lstrip("~").replace(" ", "")
    if m := re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)", t):
        hour, minute = int(m.group(1)) % 12 + (12 if m.group(3) == "pm" else 0), int(m.group(2) or 0)
    elif (m := re.fullmatch(r"(\d{1,2}):(\d{2})", t)) and int(m.group(1)) < 24:
        hour, minute = int(m.group(1)), int(m.group(2))
    elif m := re.fullmatch(r"(?:(\d+(?:\.\d+)?)h(?:rs?|ours?)?)?(?:(\d+)m(?:in(?:utes?)?)?)?", t):
        if not (m.group(1) or m.group(2)):
            return None
        hours = float(m.group(1) or 0) + int(m.group(2) or 0) / 60
        return posted + timedelta(hours=hours) if hours <= 24 * 30 else None     # never an overflow
    else:
        return None
    if minute > 59:
        return None
    at = posted.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return at + timedelta(days=1) if at < posted - timedelta(hours=1) else at


def planner_at(log: EventLog) -> datetime | None:
    """When the planner last wrote a signal (`by: planner`) or a request."""
    row = log.conn.execute("SELECT ts FROM events WHERE kind = 'request' OR json_extract(detail, '$.by') = "
                           "'planner' ORDER BY id DESC LIMIT 1").fetchone()
    return parse_stamp(row[0]) if row else None


def is_watch(meta: dict | None) -> bool:
    return (meta or {}).get("template") == WATCH


# ---------------------------------------------------------------- the inbox
def lines(cfg: Config, log: EventLog, view: View, since: int) -> list[str]:
    """The inbox's *Night shift* section: each repo's backlog, tonight's budget, and since the
    read mark the draws, the watch's flags and its reports. Empty when there is nothing."""
    out = []
    for repo, queue in backlog(cfg).items():
        if queue:
            names = [i.name + (f" (**invalid**: {'; '.join(i.errors)})" if i.errors else
                               " (full-length)" if full_length(i.meta()) else "") for i in queue]
            out.append(f"- backlog `{repo}`: {', '.join(names)}")
    if window := night(cfg):
        used = spent_hours(log, window)
        sleep = log.last("backlog_sleep")
        asleep = sleep is not None and parse_stamp(sleep.ts) >= window[0]
        how = "sleeping" if asleep and str(sleep.detail.get("key", "")).endswith(":budget") else "idle"
        out.append(f"- tonight ({clock(window[0])}–{clock(window[1])}): {used:.1f}h of "
                   f"{cfg.backlog_night_hours:g}h backlog budget used"
                   + (f"; {how} since {clock(sleep.ts)}: {sleep.detail.get('reason')}" if asleep else ""))
    for ev in log.query(since_id=since, kinds=["backlog_draw", "backlog_sleep", "watch_flag", "watch", "finish"]):
        if ev.kind != "finish":
            out.append(f"- {clock(ev.ts)} #{ev.id} {_describe(ev)}")
        elif is_watch(ev.detail):
            out.append(f"- {clock(ev.ts)} #{ev.id} watch report {ev.item} **{ev.detail.get('status')}**"
                       + (f" — {ev.paths[0]}" if ev.paths else ""))
    return out


def _describe(ev: Event) -> str:
    from .inbox import describe
    return describe(ev)
