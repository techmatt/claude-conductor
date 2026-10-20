"""`inbox.md`: everything since the planner's read mark, reds and holds first."""

from __future__ import annotations

from . import audits, memory, picks, questions, talk
from .config import Config
from .events import QUESTION_KINDS, RED_KINDS, Event, EventLog
from .state import View, decide
from .util import clock, now, parse_stamp


def progress_line(ev: Event) -> str:
    """A `progress` event as one line: `note (ETA 5:40pm, 3 done, 4 to go)`."""
    d = ev.detail
    bits = [f"ETA {d['eta']}"] if d.get("eta") else []
    if d.get("done") is not None:
        bits.append(f"{d['done']:g} done")
    if d.get("remaining") is not None:
        bits.append(f"{d['remaining']:g} to go")
    if not d.get("note") and d.get("outline"):
        return outline_line(ev, posted=False) + (f" ({', '.join(bits)})" if bits else "")
    return str(d.get("note", "")) + (f" ({', '.join(bits)})" if bits else "")


def outline_rows(ev: Event | None) -> list[dict]:
    rows = ev.detail.get("outline") if ev else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def outline_line(ev: Event, posted: bool = True) -> str:
    """An outline as one line: `outline 2/5 · next: Re-cut images by 11:05am · finish
    ~11:20am (posted 10:52am)`."""
    rows = outline_rows(ev)
    done = sum(1 for r in rows if r.get("done"))
    text = f"outline {done}/{len(rows)}"
    if nxt := next((r for r in rows if not r.get("done")), None):
        text += f" · next: {nxt.get('title')}" + (f" by {nxt['eta']}" if nxt.get("eta") else "")
        if rows[-1].get("eta"):
            text += f" · finish ~{rows[-1]['eta']}"
    else:
        text += " done"
    return text + (f" (posted {clock(ev.ts)})" if posted else "")


def latest_outline(log: EventLog, item_id: str) -> Event | None:
    """The item's current outline: its newest `progress` event carrying one."""
    return log.last("progress", item=item_id, has="outline")


def now_progress(log: EventLog, item_id: str) -> str:
    """A running item's tail on the *Now* line: its latest progress note (unless that
    line was only an outline) and its current outline, ` — note, at 10:58am; outline …`."""
    bits = []
    if (p := log.last("progress", item=item_id)) and p.detail.get("note"):
        bits.append(f"{progress_line(p)}, at {clock(p.ts)}")
    if o := latest_outline(log, item_id):
        bits.append(outline_line(o))
    return f" — {'; '.join(bits)}" if bits else ""


def reply_to(ev: Event) -> str:
    """` (reply to #12)` for a `progress` event tagged as a reply, else empty."""
    ids = talk.reply_ids(ev.detail)
    return f" (reply to {', '.join(f'#{i}' for i in ids)})" if ids else ""


def checkpoint_name(ev: Event) -> str:
    """`checkpoint #42 "session start"`: its number is its event id."""
    label = ev.detail.get("label")
    return f"checkpoint #{ev.id}" + (f' "{label}"' if label else "")


def slow_text(detail: dict) -> str | None:
    """A finish's `slow:` worth showing: what its report said took long, or None when
    it said `none` or (an older report) nothing."""
    slow = (detail.get("slow") or "").strip()
    return slow if slow and slow.lower().rstrip(".") != "none" else None


def describe(ev: Event) -> str:
    """One line for an event, used by the inbox and `conductor events`."""
    d = ev.detail
    who = ev.item or ev.repo or ""
    match ev.kind:
        case "finish":
            text = f"{who} finished **{d.get('status')}**"
            if d.get("reasons"):
                text += f" ({'; '.join(d['reasons'])})"
            if d.get("blocks") not in (None, "none"):
                text += f" · blocks: {d['blocks']}"
            if d.get("carry") not in (None, "none"):
                text += f" · carry: {d['carry']}"
            if slow := slow_text(d):
                text += f" · slow: {slow}"
            if d.get("pick"):
                text += f" · pick: {d['pick']}"
        case "pick_open":
            text = f"opened picker `{d.get('name')}` ({d.get('manifest')}) at {d.get('url')}"
            if d.get("opened") is False:
                text += " — browser did not open"
        case "pick_saved":
            text = (f"picker `{d.get('name')}` saved: {d.get('changed')} of {d.get('rows')} changed, "
                    f"{_count(d.get('notes'), 'note')}")
        case "pick_failed":
            text = f"picker {d.get('manifest')} failed: {d.get('error')}"
        case "progress":
            text = f"{who} progress{reply_to(ev)}: {progress_line(ev)}"
            if d.get("note") and d.get("outline"):
                text += f" · {outline_line(ev, posted=False)}"
        case "tell" if d.get("outline_request"):
            text = f"outline request #{ev.id} for {who}"
        case "tell":
            text = f"message #{ev.id} for {who}: {d.get('text')}"
        case "delivered":
            waited = f" after {d['waited_seconds']}s" if d.get("waited_seconds") is not None else ""
            what = f"amendment #{d['amend']}" if d.get("amend") else f"message #{d.get('tell')}"
            text = f"{who} got {what}{waited} (via {d.get('via')})"
        case "amended":
            text = f"amendment #{ev.id} for {who} (while {d.get('state')}: {d.get('delivery')}): {d.get('text')}"
        case "amend_refused":
            text = f"amendment for {who} refused (while {d.get('state')}): {d.get('reason')}; {d.get('path')} left in place"
        case "run_start":
            how = "already starting" if d.get("reused") else "started"
            text = f"run target `{d.get('target')}` {how} (pid {d.get('pid')})"
        case "run_ready":
            text = f"run target `{d.get('target')}` ready after {d.get('waited_seconds')}s"
        case "run_open":
            text = f"opened {d.get('url')} (run target `{d.get('target')}`)"
        case "run_stop":
            text = f"run target `{d.get('target')}` stopped" + (f": {d['note']}" if d.get("note") else "")
        case "run_failed":
            text = f"run target `{d.get('target')}` failed: {d.get('error')}"
            if d.get("log_tail"):
                text += " — log tail: " + " ⏎ ".join(d["log_tail"].splitlines()[-5:])
        case "reviewed":
            text = f"{who} report reviewed (finished {d.get('status')}, #{d.get('finish')})"
        case "checkpoint":
            text = checkpoint_name(ev) + (f", session start digest `{d['session']}`" if d.get("session") else "")
        case "red":
            text = f"{who} red: {'; '.join(d.get('reasons', []))}"
            if d.get("after_red", "stop") == "stop":
                text += f" — queue `{ev.repo}` held"
        case "tamper":
            text = f"{who} changed the control folder; reverted: {'; '.join(d.get('changes', []))}"
            if d.get("concurrent"):
                text += f" (also running: {', '.join(d['concurrent'])})"
        case "hold":
            text = f"{who} held: {d.get('reason')}"
        case "start":
            how = "resumed its staged session" if d.get("resumed") else "started"
            text = f"{who} {how} (session {d.get('session_id')}, pid {d.get('pid')})"
            if d.get("branch"):
                text += f" on `{d['branch']}`"
            if d.get("error"):
                text += f"; launch failed: {d['error']}"
        case "stage":
            text = f"{who} staging (session {d.get('session_id')}, pid {d.get('pid')}) in {d.get('worktree')}"
        case "staged":
            plan = (d.get("plan") or "").strip().splitlines()
            text = f"{who} staged; waits for the lock" + (f": {plan[0][:160]}" if plan else "")
        case "merge":
            text = f"{who} merged `{d.get('branch')}` into main at {str(d.get('sha') or '')[:10]} ({d.get('commits')} commit(s))"
            if d.get("resolved"):
                text += f"; prose conflict resolved in {', '.join(d['resolved'][:5])}"
        case "resolve":
            text = (f"{who} prose conflict in {', '.join(d.get('files', [])[:5])}; merge executor resolving "
                    f"(pid {d.get('pid')})")
        case "conflict":
            files = f" in {', '.join(d['files'][:5])}" if d.get("files") else ""
            text = f"{who} rebase conflicted{files}; `{d.get('branch')}` kept, main untouched"
            if d.get("resolver"):
                text += f"; merge executor failed: {d.get('error')}"
        case "paths_violation":
            files = d.get("files", [])
            text = (f"{who} changed {len(files)} file(s) outside its paths "
                    f"{d.get('paths_declared')}: {', '.join(files[:5])}")
        case "worktree":
            text = f"{who} worktree {d.get('action')}: {ev.paths[0] if ev.paths else ''}"
            if d.get("note"):
                text += f" ({d['note']})"
        case "kill":
            text = f"{who} killed: {d.get('reason')}"
        case "commit":
            text = f"{d.get('scope')} commit {d.get('sha', '')[:10]} {d.get('subject', '')}".strip()
        case "request":
            text = f"request {d.get('request')} applied: {d.get('message', '').splitlines()[0] if d.get('message') else ''}"
            if d.get("no_change"):
                text += " (no change)"
        case "request_failed":
            text = f"request {d.get('request')} failed: {d.get('error')}"
        case "invalid":
            text = f"{who or d.get('path')} invalid: {d.get('error')}"
        case "read":
            text = f"read mark → #{d.get('through')}"
        case "clear":
            text = f"queue `{ev.repo}` cleared"
        case "enqueue":
            extras = [f"{k}: {d[k]}" for k in ("depends_on", "locks") if d.get(k)]
            extras += [k for k in ("needs_planner", "readonly", "ultracode", "audit") if d.get(k)]
            extras += [f"template: {d['template']}"] if d.get("template") else []
            text = f"{who} queued" + (f" ({', '.join(extras)})" if extras else "")
        case "edited":
            text = f"{who} rewritten in the queue; it runs the new text" + (
                " (its stage is discarded; it launches fresh)" if d.get("staged") else "")
        case "adopt":
            text = f"{who} {'stage ' if d.get('stage') else ''}adopted after restart (pid {d.get('pid')})"
        case "recover":
            text = f"{who} finished while conductor was down; harvested"
        case "requeue":
            text = f"{who} returned to the queue: {d.get('reason')}"
        case "daemon_start":
            text = f"daemon started (pid {d.get('pid')})"
        case "daemon_died":
            text = (f"the previous daemon (pid {d.get('pid')}, started {clock(d['started']) if d.get('started') else '?'}) "
                    f"died without stopping: last heartbeat {clock(d['last_beat']) if d.get('last_beat') else 'never'}, "
                    f"nothing ran for {d.get('gap')}")
            if d.get("memory"):
                m = d["memory"]
                text += f"; memory then: {m.get('commit')} of {m.get('limit')} GiB committed, {m.get('free')} GiB free ({m.get('level')})"
            if d.get("running"):
                text += f"; was running: {', '.join(d['running'])}"
        case "memory" | "memory_critical":
            text = (f"memory {d.get('level')} (was {d.get('was')}): {d.get('commit')} of {d.get('limit')} GiB "
                    f"committed ({d.get('percent')}%), {d.get('free')} GiB free")
            if d.get("top"):
                text += "; biggest: " + ", ".join(f"{t['item']} {t['kind']}×{t['count']} {t['private_mb']} MiB"
                                                  for t in d["top"][:3])
            if d.get("others"):
                text += "; outside items: " + ", ".join(f"{o['name']}×{o['count']} {o['private_mb']} MiB"
                                                        for o in d["others"])
            if d.get("action"):
                text += f" — {d['action']}"
        case "reaped":
            procs_ = d.get("processes", [])
            text = (f"{who} left {len(procs_)} process(es) running in its worktree ({d.get('context')}); killed: "
                    + ", ".join(f"{p['name']} {p['pid']}" for p in procs_[:6])
                    + (" …" if len(procs_) > 6 else "") + f" ({d.get('private_mb')} MiB)")
        case "daemon_stop":
            text = "daemon stopped" + (" by a `stop: daemon` signal" if d.get("reason") == "signal" else "")
            text += f"; still running: {', '.join(d['running'])}" if d.get("running") else ""
        case "approve":
            text = f"{who} approved from {d.get('from')}"
        case "cancel":
            text = f"{who} cancelled from {d.get('from')}; file kept at {d.get('kept')}"
        case "reorder":
            text = f"{ev.repo} reordered: {', '.join(d.get('before', []))} → {', '.join(d.get('after', []))}"
        case "drift":
            text = f"control folder changed outside an executor window (accepted): {'; '.join(d.get('changes', []))}"
        case "ask":
            how = "blocking" if d.get("block") else f"default {d.get('default')!r} at {d.get('deadline_clock')}"
            text = f"{who} asked `{d.get('question')}` ({how}): {d.get('text')}"
        case "answer":
            text = f"{who} got an answer to `{d.get('question')}`: {d.get('text')}"
        case "default":
            text = f"{who} took the default for `{d.get('question')}` (nobody answered): {d.get('text')}"
        case "late":
            text = f"late answer to `{d.get('question')}` ignored ({d.get('outcome')} already): {d.get('text')}"
        case "question_closed":
            text = f"`{d.get('question')}` closed unanswered: {d.get('reason') or d.get('outcome')}"
        case "audit_used":
            text = f"{who} used {len(d.get('notes', []))} audit note(s): {', '.join(d.get('notes', []))}"
        case "queue_empty":
            text = f"queue `{ev.repo}` is empty (last: {d.get('last')})"
        case "warning" | "config_reload":
            text = f"{ev.kind}: {d.get('note', 'conductor.toml reloaded')}"
        case _:
            extras = ", ".join(f"{k}={v}" for k, v in d.items())
            text = f"{ev.kind} {who} {extras}".strip()
    if d.get("by"):
        text += f" (by {d['by']})"
    return text


def _count(n, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def saved_line(c: dict) -> str:
    """A saved picker: `art_alts_009 saved 4:52pm: 6 changed, 2 notes; picks/....json`."""
    return (f"{c.get('name')} saved {clock(c['saved_at'])}: {c.get('changed')} changed, "
            f"{_count(c.get('notes'), 'note')}; `picks/{c.get('name')}.choices.json`")


def pick_lines(cfg: Config, log: EventLog, view: View) -> list[str]:
    """The Picks section: each pending picker (saved, opened or waiting for Matt), then
    pickers saved since the checkpoint (any time without one) that no pending item names."""
    out, shown = [], set()
    for p in picks.pending(cfg, view, log):
        shown.add(p.ref)
        if p.state == "saved":
            out.append(f"- {saved_line({'name': p.name, 'saved_at': p.saved_at, 'changed': p.changed, 'notes': p.notes})}"
                       f" ({p.item})")
        elif p.state == "opened":
            out.append(f"- {p.item} opened {clock(p.opened_at)}, not saved — `{p.ref}`")
        else:
            out.append(f"- {p.item} waiting for Matt — signal `pick: {p.ref}`")
    cp = view.checkpoint
    for ref, c in sorted(picks.scan_choices(cfg.control).items(), key=lambda kv: str(kv[1].get("saved_at"))):
        if ref in shown or not c.get("saved_at"):
            continue
        try:
            if cp and parse_stamp(c["saved_at"]) < parse_stamp(cp.ts):
                continue
        except ValueError:
            continue
        out.append(f"- {saved_line(c)}")
    return out


def _line(ev: Event) -> str:
    shown = ev.paths[:1] if ev.kind == "finish" else ev.paths     # the report; log and done/ are derivable
    paths = f" — {', '.join(shown)}" if shown and ev.kind in ("finish", "tamper", "request") else ""
    return f"- {clock(ev.ts)} #{ev.id} {describe(ev)}{paths}"


def render(cfg: Config, log: EventLog, view: View, heading: str | None = None) -> str:
    """The inbox; with `heading` (the session digest's), that replaces its title and
    the mark-read hint, since the digest moves the read mark itself."""
    mark = log.read_mark()
    events = log.query(since_id=mark)
    top = log.max_id()
    mark_ev = log.last("read")
    red = sorted((e for e in events if e.kind in RED_KINDS), key=lambda e: (e.kind != "tamper", e.id))
    # Only holds still in effect: the item has not started, finished, merged, been
    # cancelled or been renamed by a reorder (which retires its old id) since.
    moved: dict[str, int] = {}
    for e in log.query(kinds=["start", "finish", "merge", "cancel", "reorder"]):
        if e.kind != "reorder":
            moved[e.item] = e.id
        elif e.repo != "proposed":
            for name in set(e.detail.get("before", [])) - set(e.detail.get("after", [])):
                moved[f"{e.repo}/{name}"] = e.id
    holds = [e for e in events if e.kind == "hold" and moved.get(e.item, 0) < e.id]
    finished = [e for e in events if e.kind == "finish"]
    changes = [e for e in events if e.kind in ("request", "commit")]
    open_qs = [q for q in questions.scan(cfg.control) if q.is_open]
    open_ids = {q.id for q in open_qs}
    asked = [e for e in events if e.kind in QUESTION_KINDS
             and not (e.kind == "ask" and e.detail.get("question") in open_ids)]
    changes += [e for e in events if e.kind == "merge"]
    other = [e for e in events if e not in red + holds + finished + changes + asked
             and e.kind not in ("read", "enqueue", "start", "ask", "hold", "stage", "worktree", "progress")]

    out = [heading or f"# Inbox — {cfg.project}", ""]
    since = f"since read mark #{mark}" + (f" ({clock(mark_ev.ts)})" if mark_ev else "")
    out.append(f"Generated {clock(now())} · {len(events)} event(s) {since}, through #{top}.")
    if heading is None:
        out.append(f"Mark read: write a file in `signals/` containing `read: {top}`.")
    if cp := view.checkpoint:
        hidden = sum(1 for i in view.finishes if view.settled(i))
        out.append(f"Current checkpoint: {checkpoint_name(cp)}, {clock(cp.ts)}"
                   + (f" (by {cp.detail['by']})" if cp.detail.get("by") else "")
                   + f" · {hidden} item(s) finished and reviewed before it hidden.")
    else:
        out.append("No checkpoint yet: signal `checkpoint: <label>` to start a session's view.")
    if open_qs:
        # Open questions lead whatever the read mark says: they are live, not news.
        out += ["", "## Questions"]
        for q in open_qs:
            how = ("**blocking**" if q.block else
                   f"default at {clock(q.deadline)}: {q.default}")
            out.append(f"- {clock(q.asked)} `{q.id}` from {q.item} — {how}")
            out += [f"  > {ln}" if ln else "  >" for ln in q.body.rstrip().splitlines()]
            out.append(f"  Answer: write `questions/{q.id}.answer.md`.")
    if unreviewed := view.unreviewed():
        # Standing, like questions: shown until a `reviewed:` signal, read mark or not.
        out += ["", "## Unreviewed reports"]
        for ev in unreviewed:
            d = ev.detail
            report = f" — {ev.paths[0]}" if ev.paths else ""
            carry = f" · carry: {d['carry']}" if d.get("carry") is not None else ""
            slow = f" · slow: {s}" if (s := slow_text(d)) else ""
            pick = f" · pick: {d['pick']}" if d.get("pick") else ""
            old = (" · **carried from before checkpoint "
                   f"#{view.checkpoint.id}**") if view.checkpoint and ev.id < view.checkpoint.id else ""
            out.append(f"- {ev.item} **{d.get('status')}**, finished {clock(ev.ts)} (#{ev.id})"
                       f"{report}{carry}{slow}{pick}{old}")
        out.append("  Mark one reviewed once acted on: signal `reviewed: <repo>/<NNN_name>`.")
    if pick_rows := pick_lines(cfg, log, view):
        # Standing too: a picker waits for Matt whatever the read mark says.
        out += ["", "## Picks", *pick_rows]
    sections = [("Red", red), ("Holds", holds), ("Finished", finished), ("Questions resolved", asked),
                ("Requests and commits", changes), ("Other", other)]
    for title, evs in sections:
        if evs:
            out += ["", f"## {title}", *(_line(e) for e in evs)]
    if not events:
        out += ["", "Nothing new."]

    out += ["", "## Now"]
    mem, level = memory.line(cfg)
    out.append(f"- {'**' + mem + '**' if level != 'ok' else mem}")
    decisions = decide(cfg, view)
    for repo in cfg.repos:
        d = decisions[repo]
        queued = [i.name for i in view.queued.get(repo, [])]
        running = []
        for item_id, rec in view.running.items():
            if rec["repo"] != repo:
                continue
            text = rec["name"] + (" (merging)" if rec.get("phase") == "merge" else "")
            if rec.get("phase", "exec") == "exec":
                text += now_progress(log, item_id)
            running.append(text)
        staged = [f"{rec['name']} ({rec.get('phase')})" for rec in view.staged.values() if rec["repo"] == repo]
        parts = []
        if running:
            parts.append(f"running {', '.join(running)}")
        if staged:
            parts.append(f"staged {', '.join(staged)}")
        if queued:
            parts.append(f"queued {', '.join(queued)}")
        if d.action in ("hold", "wait"):
            parts.append(f"{d.action}: {d.reason}")
        elif ev := view.red_hold.get(repo):
            parts.append(f"held after red {ev.item} (#{ev.id}); signal `clear: {repo}` to release")
        if notes := view.unused_notes(cfg, repo):
            parts.append(audits.pending(len(notes), [n.item for n in notes]))
        out.append(f"- `{repo}`: {'; '.join(parts) or 'idle'}")
    for d in view.stray:
        out.append(f"- `queue/{d.name}` is not a repo in conductor.toml; its items never run")
    proposed = list(cfg.path("proposed").glob("*.md")) if cfg.path("proposed").is_dir() else []
    if proposed:
        out.append(f"- proposed, awaiting approval: {', '.join(p.stem for p in sorted(proposed))}")
    return "\n".join(out) + "\n"
