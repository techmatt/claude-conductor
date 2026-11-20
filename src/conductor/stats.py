"""`conductor stats`: a scoreboard read from a project's event log (docs/stats.md).

Read-only: one pass over `events.db`, nothing written, no new events. Plain text, kept near
2 KB so a planner's `read(what="stats")` costs little.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .events import Event, EventLog
from .util import now, parse_stamp

WEEKS = 4
SESSIONS_SHOWN = 6
KINDS_SHOWN = 5          # per week; the rest are summed as `other`
FEATURE = "feature"
# Signal files the MCP server writes (`actions.write_signal`: a six-digit counter); the
# bridge's are `planner-<YYYYMMDD-HHMMSS>.signal`. MCP requests are named `…-mcp<SS><n>`.
MCP_SIGNAL = re.compile(r"^signals/planner-\d{8}-\d{6}-\d{6}\.signal$")
NAME_BACK = re.compile(r"(?:^|_)(\d{2,4})(?=_|$)")        # `post_fix_278`: a number in the slug
FOLLOWUP = re.compile(r"(?:^|_)follow_?ups?(?=_|$)")


@dataclass
class Run:
    """One finished item: its last `finish`, with what the log says about its run."""
    item: str
    finish: Event
    kind: str                   # `feature` (no template) or the template's name
    model: str
    hours: float | None
    cost: float
    asks: int = 0
    defaults: int = 0


@dataclass
class Session:
    number: int | str
    ts: str
    counts: Counter = field(default_factory=Counter)


def week_of(ts: str | datetime) -> datetime:
    dt = parse_stamp(ts) if isinstance(ts, str) else ts
    day = dt.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return day - timedelta(days=day.weekday())


def short_model(model: str | None) -> str:
    return (model or "unrecorded").removeprefix("claude-")


def _real(value) -> bool:
    """A report line that says something: not missing, empty or `none`."""
    return bool(value) and str(value).strip().lower() not in ("none", "null", "n/a", "-")


def runs(events: list[Event], since: datetime) -> list[Run]:
    first_start: dict[str, Event] = {}
    model: dict[str, str] = {}
    stage_cost: Counter = Counter()
    asks: Counter = Counter()
    defaults: Counter = Counter()
    finishes: dict[str, Event] = {}
    for ev in events:
        if not ev.item:
            continue
        if ev.kind in ("start", "stage"):
            first_start.setdefault(ev.item, ev)
            if ev.detail.get("model"):
                model.setdefault(ev.item, ev.detail["model"])
        elif ev.kind == "staged":
            stage_cost[ev.item] += float(ev.detail.get("cost_usd") or 0)
        elif ev.kind == "ask":
            asks[ev.item] += 1
        elif ev.kind == "default":
            defaults[ev.item] += 1
        elif ev.kind == "finish":
            finishes[ev.item] = ev
    out = []
    for item, fin in finishes.items():
        if parse_stamp(fin.ts) < since:
            continue
        start = first_start.get(item)
        hours = (parse_stamp(fin.ts) - parse_stamp(start.ts)).total_seconds() / 3600 if start else None
        out.append(Run(item, fin, fin.detail.get("template") or FEATURE,
                       short_model(fin.detail.get("model") or model.get(item)), hours,
                       float(fin.detail.get("cost_usd") or 0) + stage_cost[item], asks[item], defaults[item]))
    return out


def corrections(events: list[Event], since: datetime) -> dict[datetime, list[tuple[str, str]]]:
    """Per week, `(item, why)` for each item queued to fix a finished one. The rule, kept
    cheap and stable: a non-template item queued with `resume_of`, with a `depends_on`
    already finished when it was queued, or whose slug names a finished item's number in its
    repo or says `followup`; and every `land:` begun (a kept red branch put on main)."""
    finished: dict[str, set[str]] = defaultdict(set)      # repo → numbers finished so far
    finished_ids: set[str] = set()
    out: dict[datetime, list[tuple[str, str]]] = defaultdict(list)
    for ev in events:
        if ev.kind == "finish" and ev.item:
            finished_ids.add(ev.item)
            repo, _, name = ev.item.partition("/")
            finished[repo].add(name.split("_", 1)[0])
            continue
        if parse_stamp(ev.ts) < since:
            continue
        if ev.kind == "land" and ev.detail.get("state") == "started":
            out[week_of(ev.ts)].append((ev.item or ev.detail.get("branch", "?"), "land"))
        if ev.kind != "enqueue" or not ev.item or ev.detail.get("template"):
            continue
        repo, _, name = ev.item.partition("/")
        num, _, slug = name.partition("_")
        why = None
        if ev.detail.get("resume_of"):
            why = "resume"
        elif any(d in finished_ids or f"{repo}/{d}" in finished_ids for d in ev.detail.get("depends_on") or []):
            why = "depends"
        elif any(n != num and n.lstrip("0") in {f.lstrip("0") for f in finished[repo]}
                 for n in NAME_BACK.findall(slug)) or FOLLOWUP.search(slug):
            why = "name"
        if why:
            out[week_of(ev.ts)].append((ev.item, why))
    return out


def _via_mcp(ev: Event, path: str = "") -> bool:
    """A write the planner's server made: its event's `via: mcp` (140); before that, only an
    MCP signal file's name or request's `-mcp` told."""
    return (ev.detail.get("via") == "mcp" or bool(path and MCP_SIGNAL.match(path))
            or "-mcp" in str(ev.detail.get("request", "")))


def sessions(events: list[Event], since: datetime, conductor_items: set[str]) -> list[Session]:
    """Planner sessions (a `session:` checkpoint to the next) begun since `since`, with what
    the planner wrote in each, and by which route: `mcp` (the server's tools) or `bridge`
    (the rest). An item `approve:` moved into the queue is counted queued, on neither route."""
    out: list[Session] = []
    signals_seen: set[str] = set()
    approved: set[str] = set()
    for ev in events:
        if ev.kind == "approve" and ev.item:
            approved.add(ev.item)
        if ev.kind == "checkpoint" and ev.detail.get("session"):
            out.append(Session(ev.detail.get("number") or "?", ev.ts))
            signals_seen = set()
            continue
        if not out:
            continue
        c = out[-1].counts
        for p in ev.paths:
            if p.startswith("signals/planner-") and p not in signals_seen:
                signals_seen.add(p)
                c["signals"] += 1
                c["mcp" if _via_mcp(ev, p) else "bridge"] += 1
        route = "mcp" if _via_mcp(ev) else "bridge"
        if ev.kind in ("request", "request_failed"):
            c["requests"] += 1
            c["refused"] += ev.kind == "request_failed"
            c[route] += 1
        elif ev.kind in ("enqueue", "edited") and ev.item not in conductor_items:
            c["queued" if ev.kind == "enqueue" else "rewrites"] += 1
            if not (ev.kind == "enqueue" and ev.item in approved):
                c[route] += 1
        elif ev.kind in ("amended", "answer") and ev.detail.get("by") == "planner":
            c["amends" if ev.kind == "amended" else "answers"] += 1
            c[route] += 1
        elif ev.kind in ("planner_write", "memory_note"):   # proposed, parked, backlog (the server's only); memory notes
            c["other"] += 1
            c[route] += 1
    return [s for s in out if parse_stamp(s.ts) >= since]


def _hours(h: float) -> str:
    return f"{h:.1f}h" if h < 10 else f"{h:.0f}h"


SESSION_KEYS = (("requests", 4), ("refused", 8), ("signals", 4), ("queued", 7), ("rewrites", 8),
                ("amends", 6), ("answers", 7), ("other", 6), ("mcp", 4), ("bridge", 7))


def _cols(value, fmt: str) -> str:
    return " ".join(f"{value(k):>{w}{fmt}}" for k, w in SESSION_KEYS)


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def report(log: EventLog, project: str, weeks: int = WEEKS, today: datetime | None = None) -> str:
    weeks = max(1, int(weeks))
    this_week = week_of(today or now())
    since = this_week - timedelta(weeks=weeks - 1)
    events = log.query()
    done = runs(events, since)
    conductor_items = {e.item for e in events if e.kind in ("watch", "retro", "backlog_draw")}
    lines = [f"{project}: stats, {weeks} week{'s' * (weeks > 1)} from Monday {since:%b} {since.day}"]
    week_keys = [since + timedelta(weeks=i) for i in range(weeks)]

    def wk(d: datetime) -> str:
        return f"{d:%b} {d.day:<2}"

    lines.append("")
    lines.append("Items without a template, per week and model:")
    lines.append("  week    model        n  done/part/red  friction slow  median h  median $  asks (timed out)")
    rows = 0
    for w in week_keys:
        by_model: dict[str, list[Run]] = defaultdict(list)
        for r in done:
            if r.kind == FEATURE and week_of(r.finish.ts) == w:
                by_model[r.model].append(r)
        for m, rs in sorted(by_model.items()):
            st = Counter(r.finish.detail.get("status") or "done" for r in rs)
            fr = sum(_real(r.finish.detail.get("friction")) for r in rs)
            sl = sum(_real(r.finish.detail.get("slow")) for r in rs)
            mh = _median([r.hours for r in rs if r.hours is not None])
            mc = _median([r.cost for r in rs])
            lines.append(f"  {wk(w)}  {m:<11} {len(rs):>2}  {st['done']:>4}/{st['partial']}/{st['red']:<6}"
                         f"{round(100 * fr / len(rs)):>6}% {round(100 * sl / len(rs)):>4}%"
                         f"  {_hours(mh) if mh is not None else '-':>8}  {mc:>8.2f}"
                         f"  {sum(r.asks for r in rs):>3} ({sum(r.defaults for r in rs)})")
            rows += 1
    if not rows:
        lines.append("  (none finished)")

    lines.append("")
    lines.append("Spend per week, by kind (share of $; $ and executor hours):")
    for w in week_keys:
        by_kind: dict[str, list[Run]] = defaultdict(list)
        for r in done:
            if week_of(r.finish.ts) == w:
                by_kind[r.kind].append(r)
        total = sum(r.cost for rs in by_kind.values() for r in rs)
        if not by_kind:
            lines.append(f"  {wk(w)}  nothing finished")
            continue
        ranked = sorted(by_kind.items(), key=lambda kv: -sum(r.cost for r in kv[1]))
        if len(ranked) > KINDS_SHOWN:
            ranked = ranked[:KINDS_SHOWN - 1] + [("other", [r for _, rs in ranked[KINDS_SHOWN - 1:] for r in rs])]
        parts = []
        for k, rs in ranked:
            cost = sum(r.cost for r in rs)
            hrs = sum(r.hours or 0 for r in rs)
            share = f"{round(100 * cost / total)}% " if total else ""
            parts.append(f"{k} {share}(${cost:.0f}, {_hours(hrs)})")
        lines.append(f"  {wk(w)}  ${total:.0f}: " + ", ".join(parts))

    lines.append("")
    fixes = corrections(events, since)
    queued = Counter(week_of(e.ts) for e in events if e.kind == "enqueue" and e.item
                     and not e.detail.get("template") and parse_stamp(e.ts) >= since)
    lines.append("Corrections (items queued to fix a finished one; the rule: docs/stats.md):")
    for w in week_keys:
        got = fixes.get(w, [])
        whys = Counter(why for _, why in got)
        names = " ".join(i.rsplit("/", 1)[-1].split("_", 1)[0] for i, _ in got)
        detail = f" ({', '.join(f'{k} {v}' for k, v in sorted(whys.items()))}: {names})" if got else ""
        lines.append(f"  {wk(w)}  {len(got)} of {queued[w]} queued{detail}")

    lines.append("")
    sess = sessions(events, since, conductor_items)
    lines.append(f"Planner writes per session (weeks: the mean; then the latest {SESSIONS_SHOWN}; "
                 "by route: mcp, the server's tools; bridge, the rest):")
    if sess:
        lines.append("  week/#   n   req  refused  sig  queued  rewrite  amend  answer  other  mcp  bridge")
        for w in week_keys:
            ss = [s for s in sess if week_of(s.ts) == w]
            if ss:
                lines.append(f"  {wk(w)}  {len(ss):>2} " + _cols(lambda k: sum(s.counts[k] for s in ss) / len(ss), ".1f"))
        for s in sess[-SESSIONS_SHOWN:]:
            lines.append(f"  #{str(s.number):<6}    " + _cols(lambda k: s.counts[k], "d"))
    else:
        lines.append("  (none)")
    return "\n".join(lines)
