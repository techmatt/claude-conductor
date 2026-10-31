"""The night shift in the daemon (docs/night-shift.md): backlog receipts and draws every tick,
the stall check every `watch_check_minutes`, and the watch item it launches.

A **draw** moves a repo's next eligible `backlog/<repo>/` item into its empty queue under the
next item number. A full-length item is drawn only if Matt acted (`nightshift.matt_since`)
since the repo's last full-length item started, and at night only while the backlog budget lasts.

The **check** costs no tokens: it flags (one `watch_flag` event per flag, de-duplicated by
its key) a running item silent or past its ETA, a question left open (and a blocking one
left unanswered `watch_blocked_minutes`), a repo whose empty queue leaves backlog waiting,
memory pressure and a failed run target. A stall flag, or half an hour of running work since
the last watch, launches a **watch item**: a short readonly run of the `watch` template, unless
a planner session is active. When it finishes, its report's `## Actions` lines (tell, answer,
notify, draw: nothing else) are carried out, attributed `by: watch`. A paused repo's items
(`pause:`) are neither checked nor watched.

**Autopilot** (`off`, `night`, `always`; the `autopilot:` signal) gates only what starts work
on its own: draws, a watch's included. Repairs (a watch's tell and answer), the checks and the
watch itself run at any setting.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import items, nightshift, questions, state
from . import daemon_signals
from .util import clock, duration, now, parse_stamp, read_text, retry_io, write_text

WATCH_BODY = "{reason}\n"           # the watch item's body: why conductor launched it
STALL_FLAGS = ("quiet", "eta", "blocked")      # the flags that launch a watch; the rest are logged only
ACTIONS = ("tell", "answer", "notify", "draw")
ANSWER_RE = re.compile(r"^(\S+)\s+(.+?)\s*\(source:\s*([^)]+)\)\s*$")    # `<question> <answer> (source: <where>)`
FOOTER_RE = re.compile(r"^`?report written to\b", re.IGNORECASE)     # every report's last line, often under `## Actions`
MAX_ACTIONS = 6
QUIET_GRACE = timedelta(minutes=5)      # past a promised time, how long a silent item still has
WATCH_FRONT = ("---\nrepo: {repo}\nreadonly: true\ntemplate: watch\nmax_hours: {hours:g}\n"
               "after_red: continue\nstage: false\n---\n")


class WatchMixin:
    def _init_watch(self) -> None:
        self._watch_at: float | None = None         # monotonic time of the last check
        self._watch_seen = self.log.max_id()        # a `run_failed` newer than this is a flag
        self.watch_keys = {e.detail.get("key") for e in self.log.query(kinds=["watch_flag", "backlog_sleep"])}
        self.backlog_seen: dict[str, int] = {}      # backlog file → the mtime its receipt covers
        self._night_error: str | None = None        # the last failure logged, so a stuck one logs once
        self._draw_key: tuple | None = None         # what the last draw attempt saw (`_night_shift`)
        self._unwatched: set[str] = set()           # stall flags no watch was launched for: retried

    def _prepare_backlog(self) -> None:
        root = self.cfg.path("backlog")
        for repo in self.cfg.repos:
            root.joinpath(repo).mkdir(parents=True, exist_ok=True)
        if not (root / ".gitignore").is_file():     # ignored without a request, as receipts/ is
            write_text(root / ".gitignore", "*\n")

    def night_shift(self) -> None:
        try:
            self._night_shift()
        except OSError:
            raise                           # the tick's own recovery (`run_forever`)
        except Exception as exc:            # noqa: BLE001 - the night shift never stops the daemon
            if repr(exc) != self._night_error:
                self._night_error = repr(exc)
                self.log.append("warning", note=f"the night shift failed: {exc!r}; the rest of the tick goes on")

    def _night_shift(self) -> None:
        self.process_backlog()
        # A draw depends on the log, the settled backlog and the night; unchanged, it is not retried.
        settled = sum(map(self._settled, self.backlog_seen))
        key = (self.log.max_id(), tuple(sorted(self.backlog_seen.items())), settled, bool(nightshift.night(self.cfg)))
        if self.backlog_seen and key != self._draw_key:
            self._draw_key = key
            view = state.scan(self.cfg, self.log)
            for repo in self.cfg.repos:
                self._draw(view, repo)
        every = self.cfg.watch_check_minutes
        if every and (self._watch_at is None or time.monotonic() - self._watch_at >= every * 60):
            self._watch_at = time.monotonic()
            self.watch_check(state.scan(self.cfg, self.log))

    # ---------------------------------------------------------------- backlog
    def process_backlog(self) -> None:
        """A receipt for each new or rewritten backlog file: waiting to be drawn, or refused
        for what would make it invalid in the queue. No event: a backlog write changes nothing."""
        files = [p for repo in self.cfg.repos for p in items.item_files(self.cfg.path("backlog", repo))]
        fresh, self.backlog_seen = self._new_versions(files, self.backlog_seen)
        for p, rel in fresh:
            repo = p.parent.name
            item = nightshift.parse(p, repo)
            if item.errors:
                self._write_receipt(rel, [], reason="; ".join(item.errors))
            else:
                full = " (full-length: drawn only after Matt acts)" if nightshift.full_length(item.meta()) else ""
                self._write_receipt(rel, [], result=f"in the backlog for {repo}: drawn when its queue is empty{full}")

    def _settled(self, rel: str) -> bool:
        """A receipted backlog file old enough to draw: its receipt comes as soon as it reads,
        a draw only once it can't still be being written."""
        mtime = self.backlog_seen.get(rel)
        return mtime is not None and mtime / 1e9 < time.time() - daemon_signals.SIGNAL_SETTLE_SECONDS

    def _eligible(self, view: state.View, repo: str) -> tuple[items.Item | None, str]:
        """The backlog item a draw would take now, or None and why none."""
        if view.queued.get(repo):
            return None, "its queue is not empty"
        if busy := [i for i, r in view.running.items() if r["repo"] == repo and not nightshift.is_watch(r)]:
            return None, f"{busy[0]} is running"
        if ev := view.red_hold.get(repo):
            return None, f"its queue is held after red {ev.item}"
        if ev := view.planner_hold.get(repo):
            return None, f"its queue is held for the planner ({ev.item})"
        if self._reorder_pending() == repo:
            return None, "a reorder is being finished"
        # Only files settled and receipted (`process_backlog`): never one still being written.
        queue = [i for i in nightshift.backlog(self.cfg)[repo] if self._settled(self.cfg.rel(i.path))]
        if not queue:
            return None, "the backlog is empty"
        if hold := nightshift.autopilot_holds(self.cfg, self.log):
            return None, hold
        item, why, done = nightshift.pick(self.cfg, self.log, view, repo, queue)
        if done:
            self._sleep(nightshift.night(self.cfg), "budget", done)
        return item, "" if item else "; ".join(w for _, w in why)

    def _draw(self, view: state.View, repo: str, by: str | None = None) -> str | None:
        """Draw `repo`'s next eligible backlog item into its queue; None, or why not."""
        item, why = self._eligible(view, repo)
        if item is None:
            return why
        name = nightshift.queue_name(self.cfg, view, repo, item.name)
        dest = self.cfg.path("queue", repo, f"{name}.md")
        src = self.cfg.rel(item.path)
        if not items.NAME_RE.match(name) or dest.exists():
            return f"{item.name} can't be queued as {name}"
        retry_io(os.replace, item.path, dest)
        ev = self.log.append("backlog_draw", item=f"{repo}/{name}", paths=[self.cfg.rel(dest), src],
                             full_length=nightshift.full_length(item.meta()), night=bool(nightshift.night(self.cfg)),
                             by=by or "conductor", **{"from": src})
        self._write_receipt(src, [ev], result=f"drawn into the queue as {repo}/{name} (#{ev.id})")
        self.backlog_seen.pop(src, None)
        view.queued[repo] = [items.parse(dest, repo)]       # this tick sees it queued
        return None

    def _sleep(self, window, kind: str, reason: str) -> None:
        """Logged once a night per kind. `budget`: no more draws tonight (`_eligible` refuses
        them); `idle`: nothing queued anywhere and nothing eligible, a note only (a backlog
        item written later is still drawn)."""
        key = f"sleep:{window[0].date()}:{kind}"
        if key not in self.watch_keys:
            self.watch_keys.add(key)
            self.log.append("backlog_sleep", key=key, reason=reason, until=clock(window[1]),
                            spent_hours=round(nightshift.spent_hours(self.log, window), 2),
                            budget_hours=self.cfg.backlog_night_hours)

    # ---------------------------------------------------------------- the check
    def watch_check(self, view: state.View) -> None:
        at = now()
        flags: list[tuple[str, str, str | None, str]] = []      # (key, flag, item, text)
        quiet = self.cfg.watch_quiet_minutes * 60
        for item_id, run in sorted(self.runs.items()):
            rec = run.rec
            if (rec.get("phase", "exec") != "exec" or run.exited() or nightshift.is_watch(rec) or item_id in self.blocked
                    or rec.get("paused_at") or rec["repo"] in view.paused):
                continue
            p = self.log.last("progress", item=item_id)
            since = parse_stamp(p.ts if p else rec["started"])
            # Silent but a process of its own is still writing: a healthy long run (aicomplete 009).
            if (quiet and (at - since).total_seconds() >= quiet and not self.still_writing(item_id)
                    and not self._promised(item_id, at)):
                flags.append((f"quiet:{item_id}:{p.id if p else 0}", "quiet", item_id,
                              f"{item_id}: no progress line for {duration((at - since).total_seconds())} "
                              f"(last {clock(since)}{'' if p else ', its start'})"))
            eta, posted = self._eta(item_id)
            if eta is not None and (due := nightshift.parse_eta(eta, parse_stamp(posted.ts))) and at > due:
                flags.append((f"eta:{item_id}:{posted.id}", "eta", item_id,
                              f"{item_id}: past its ETA {eta} (posted {clock(posted.ts)})"))
        for q in questions.scan(self.cfg.control):
            if not q.is_open or q.item.split("/", 1)[0] in view.paused:
                continue
            waited = (at - parse_stamp(q.asked)).total_seconds()
            if waited >= self.cfg.watch_check_minutes * 60:
                flags.append((f"question:{q.id}", "question", q.item,
                              f"question `{q.id}` from {q.item} open since {clock(q.asked)}"
                              + (" (blocking)" if q.block else "")))
            # A blocked item does nothing until answered: a watch looks for an answer already given.
            if q.block and self.cfg.watch_blocked_minutes and waited >= self.cfg.watch_blocked_minutes * 60:
                flags.append((f"blocked:{q.id}", "blocked", q.item,
                              f"{q.item} blocked on question `{q.id}` since {clock(q.asked)}, unanswered"))
        for repo, queue in nightshift.backlog(self.cfg).items():
            idle = not view.queued.get(repo) and not any(r["repo"] == repo for r in view.running.values())
            if queue and idle and (why := self._eligible(view, repo)[1]) != "the backlog is empty":   # unsettled
                flags.append((f"backlog:{repo}:{why}", "backlog", None,
                              f"queue `{repo}` is empty and its backlog waits: {why}"))
        if self.mem_level != "ok" and (mev := self.log.last("memory_critical") or self.log.last("memory")):
            flags.append((f"memory:{mev.id}", "memory", None, f"memory {self.mem_level} since {clock(mev.ts)}"))
        for ev in self.log.query(since_id=self._watch_seen, kinds=["run_failed"]):
            if ev.detail.get("tool"):
                continue                # a click's failure, seen where it was clicked: no watch
            flags.append((f"run_failed:{ev.id}", "run_failed", None,
                          f"run target `{ev.detail.get('target')}` failed: {ev.detail.get('error')}"))
        self._watch_seen = self.log.max_id()
        settling = not all(map(self._settled, self.backlog_seen))
        if (window := nightshift.night(self.cfg)) and not settling and all(
                not view.queued.get(r) and not any(x["repo"] == r for x in view.running.values()) for r in self.cfg.repos):
            self._sleep(window, "idle", "nothing in any queue and nothing eligible in the backlog")
        new = [f for f in flags if f[0] not in self.watch_keys]
        # A stall a refused launch skipped (planner active, memory, a watch live) is tried again.
        stalls = [f for f in flags if f[1] in STALL_FLAGS and (f[0] not in self.watch_keys or f[0] in self._unwatched)]
        why_not = None
        if stalls:
            keys = {f[0] for f in stalls}
            why_not = self._launch_watch("; ".join(f[3] for f in stalls), sorted(keys), item=stalls[0][2])
            self._unwatched = (self._unwatched | keys) if why_not else (self._unwatched - keys)
        elif self._watch_due(at):
            self._launch_watch(f"the routine look while items run ({clock(at)})", [])
        for key, flag, item_id, text in new:
            self.watch_keys.add(key)
            extra = {"watch": f"skipped: {why_not}"} if why_not and flag in STALL_FLAGS else {}
            self.log.append("watch_flag", item=item_id, key=key, flag=flag, text=text, **extra)

    def _promised(self, item_id: str, at: datetime) -> bool:
        """The item said when it would next be heard from, and that is still ahead: its outline's
        next row or its latest `eta`, with `QUIET_GRACE`. The routine watch still looks in."""
        e, o = self.log.last("progress", item=item_id, has="eta"), self.log.last("progress", item=item_id, has="outline")
        rows = [r for r in (o.detail.get("outline") or []) if isinstance(r, dict)] if o else []
        row = next((r for r in rows if not r.get("done")), None)
        times = [(row.get("eta"), o)] if row else []
        times += [(e.detail.get("eta"), e)] if e else []
        return any((due := nightshift.parse_eta(text, parse_stamp(ev.ts))) and at <= due + QUIET_GRACE
                   for text, ev in times)

    def _eta(self, item_id: str):
        """The item's newest ETA and the progress event that posted it: an `eta`, or its
        outline's last row; (None, None) if it never gave one."""
        e, o = self.log.last("progress", item=item_id, has="eta"), self.log.last("progress", item=item_id, has="outline")
        rows = (o.detail.get("outline") or []) if o else []
        last_row = rows[-1].get("eta") if rows and isinstance(rows[-1], dict) else None
        if o and last_row and (e is None or o.id > e.id):
            return last_row, o
        return (e.detail.get("eta"), e) if e else (None, None)

    def _watch_due(self, at) -> bool:
        """Half an hour (`watch_every_minutes`) of running work since the later of the last
        watch and the earliest start among the items executing now, paused ones aside."""
        every = self.cfg.watch_every_minutes
        starts = [parse_stamp(r.rec["started"]) for r in self.runs.values()
                  if not nightshift.is_watch(r.rec) and r.rec.get("phase", "exec") == "exec" and not r.exited()
                  and not r.rec.get("paused_at")]
        if not every or not starts:
            return False
        ref = min(starts)
        if (last := self.log.last("watch", has="file")) and parse_stamp(last.ts) > ref:
            ref = parse_stamp(last.ts)
        return (at - ref).total_seconds() >= every * 60

    def _launch_watch(self, reason: str, flags: list[str], item: str | None = None,
                      by: str | None = None, force: bool = False) -> str | None:
        """Queue a watch item (readonly, `template: watch`), which starts at once beside
        anything; None, or why not. `force` (the `watch: now` signal) skips the planner and
        memory rules; never the one-at-a-time rule."""
        view = state.scan(self.cfg, self.log)
        live = [i for i, r in view.running.items() if nightshift.is_watch(r)]
        live += [it.id for q in view.queued.values() for it in q if it.template == nightshift.WATCH]
        if live:
            return f"a watch is already queued or running ({live[0]})"
        if not force:
            mins = self.cfg.watch_planner_minutes
            if (p := nightshift.planner_at(self.log)) and (now() - p).total_seconds() < mins * 60:
                return f"a planner session is active (its last signal or request {clock(p)})"
            if self.mem_level != "ok":
                return f"memory is at {self.mem_level}"
        running = [r["repo"] for r in view.running.values() if not nightshift.is_watch(r) and r["repo"] not in view.paused]
        repo = item.split("/", 1)[0] if item else (running[0] if running else next(iter(self.cfg.repos)))
        if self._reorder_pending() == repo:
            return f"a reorder of {repo} is being finished"
        name = nightshift.watch_name(view, repo)
        path = self.cfg.path("queue", repo, f"{name}.md")
        write_text(path, WATCH_FRONT.format(repo=repo, hours=self.cfg.watch_max_hours) + WATCH_BODY.format(reason=reason))
        rel = self.cfg.rel(path)
        self.log.append("watch", item=f"{repo}/{name}", paths=[rel], file=rel, reason=reason, flags=flags,
                        by=by or self._by or "conductor")
        return None

    # ---------------------------------------------------------------- its actions
    def _watch_actions(self, rec: dict) -> None:
        """A finished watch's `## Actions` lines, carried out `by: watch`; anything else
        it wrote there is refused with a warning (it recommends those in its report).
        Autopilot holds back only a draw: a tell or an answer repairs, it starts nothing."""
        item_id, report = rec["item"], Path(rec["report"])
        rel = self.cfg.rel(report)
        try:
            text = read_text(report)
        except (OSError, UnicodeDecodeError):
            return
        lines, inside = [], False
        for ln in text.splitlines():
            if re.match(r"^#+\s", ln):
                inside = bool(re.match(r"^#+\s*actions\s*$", ln.strip(), re.IGNORECASE))
            elif (inside and (s := ln.strip().lstrip("-*").strip()) and s.lower().rstrip(".") != "none"
                  and not FOOTER_RE.match(s)):
                lines.append(s.strip("`"))
        for n, s in enumerate(lines):
            verb, _, value = s.partition(":")
            verb, value = verb.strip().lower(), value.strip()
            try:
                if n >= MAX_ACTIONS:
                    raise ValueError(f"more than {MAX_ACTIONS} actions")
                if verb not in ACTIONS or not value:
                    raise ValueError("a watch may only tell, answer, notify or draw; it recommends the rest in its report")
                if verb == "draw" and (hold := nightshift.autopilot_holds(self.cfg, self.log)):
                    raise ValueError(f"{hold}: recommended only")
                with self._attributed("watch"):
                    if verb == "tell":
                        self._tell(value, rel)
                    elif verb == "answer":
                        self._watch_answer(value, item_id, rel)
                    elif verb == "notify":
                        self.log.append("notify", item=item_id, paths=[rel], text=value)
                    elif value not in self.cfg.repos:
                        raise ValueError(f"{value!r} is not a repo")
                    elif why := self._draw(state.scan(self.cfg, self.log), value, by="watch"):
                        raise ValueError(why)
            except Exception as exc:        # noqa: BLE001 - a bad action never stops its item concluding
                self.log.append("warning", item=item_id, paths=[rel], action=s,
                                note=f"watch action `{s}` not carried out: {exc}")

    def _watch_answer(self, value: str, watch_id: str, rel: str) -> None:
        """`answer: <question> <answer> (source: <where>)`: an open question answered only with
        what Matt or the planner already said (a message, the item's prompt or an amendment),
        its source named in the answer. Conductor can't judge the source; the watch template
        forbids anything new, and the `watch_answer` event keeps the source for review."""
        m = ANSWER_RE.match(value)
        if not m:
            raise ValueError("an answer is `answer: <question id> <answer> (source: <where Matt or the planner "
                             "gave it>)`")
        ref, text, source = m.groups()
        q = questions.find(self.cfg.control, ref)
        if q is None or not q.is_open:
            raise ValueError(f"no open question {ref!r}")
        if q.answer_path.exists():
            raise ValueError(f"{q.id} already has an answer waiting")
        self.log.append("watch_answer", item=q.item, paths=[rel], question=q.id, text=text, source=source,
                        watch=watch_id)
        questions.write_answer(q, f"{text}\n\n(Answered by conductor's watch from what was already said: {source}.)",
                               by="watch")
