"""`inbox.md`: everything since the planner's read mark, reds and holds first."""

from __future__ import annotations

from . import audits, memory, nightshift, picks, questions, talk
from .config import Config
from .events import QUESTION_KINDS, RED_KINDS, Event, EventLog, live_invalids, signal_invalid
from .request import is_closeout
from .resume import label as resume_label
from .state import View, decide
from .util import clock, now, parse_stamp

NIGHT_KINDS = ("backlog_draw", "backlog_sleep", "watch_flag", "watch", "autopilot")      # the *Night shift* section's (`nightshift.lines`)

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


def changed_text(ev: Event) -> str:
    """`report changed since review 3:10pm (after it was opened by app, #812)`: a
    `report_changed` event, as the inbox, the digest and `review.md` flag it."""
    d = ev.detail
    what = " and ".join("audit notes" if f == "audit" else "report" for f in d.get("files") or ["report"])
    opened = f" (after it was opened by {d.get('opened_by')}, #{d['opened']})" if d.get("opened") else ""
    return f"{what} changed since review {clock(ev.ts)}{opened}"


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
            if d.get("status") == "red" and d.get("report_status") in ("done", "partial"):
                text += f" · its report says {d['report_status']}: NOT on main"
            if d.get("conductor"):
                text += f" · landed despite conductor's own red ({'; '.join(d['conductor'])})"
            if resumed := resume_label(d):
                text += f" · {resumed}"
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
        case "tell" if d.get("pause") or d.get("resume"):
            text = f"{'pause' if d.get('pause') else 'resume'} message #{ev.id} for {who}" + (
                f", as the answer to `{d['answers']}`" if d.get("answers") else "")
        case "tell":
            text = f"message #{ev.id} for {who}" + (f" (as the answer to `{d['answers']}`)" if d.get("answers") else "") \
                + f": {d.get('text')}"
        case "pause":
            text = f"`{ev.repo}` **paused** by {d.get('by', 'unknown')}: {', '.join(d.get('items') or []) or 'nothing'} told to wait"
        case "resume":
            text = f"`{ev.repo}` resumed by {d.get('by', 'unknown')}" + (
                f": {', '.join(d.get('items') or [])} told to carry on" if d.get("items") else "")
        case "watch_answer":
            text = f"the watch answered `{d.get('question')}` for {who} (source: {d.get('source')}): {d.get('text')}"
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
        case "opened":
            text = f"opened ({d.get('verb')}) {d.get('url') or d.get('path')}"
        case "open_refused":
            text = f"open: {d.get('verb')} {d.get('target')} refused: {d.get('reason')}"
        case "notify":
            text = f"toast: {d.get('text')}"
        case "reviewed":
            text = f"{who} report reviewed (finished {d.get('status')}, #{d.get('finish')})"
        case "report_changed":
            text = f"{who} {changed_text(ev)}: unreviewed again until the next `reviewed:`"
        case "session_opened":
            text = f"{who} session opened by hand (by {d.get('by')}, session {d.get('session_id')})"
        case "checkpoint":
            text = checkpoint_name(ev) + (f", session start digest `{d['session']}`" if d.get("session") else "")
        case "review":
            what = "every unreviewed report" if d.get("which") == "all" else d.get("which")
            text = (f"review bundle `review.md` written for {what}: {_count(len(d.get('items', [])), 'report')}"
                    + (f", {_count(len(d.get('conductor', [])), 'conductor report')}" if d.get("which") == "all" else ""))
        case "red":
            text = f"{who} red: {'; '.join(d.get('reasons', []))}"
            if d.get("after_red", "stop") == "stop":
                text += f" — queue `{ev.repo}` held"
        case "tamper":
            text = f"{who} changed the control folder; reverted: {'; '.join(d.get('changes', []))}"
            if d.get("attributed"):
                text = (f"{', '.join(d['attributed'])} changed the control folder (its own tool calls named it); "
                        f"reverted: {'; '.join(d.get('changes', []))} (running then: {', '.join(d['concurrent'])})")
            elif d.get("concurrent"):
                text += f" (also running: {', '.join(d['concurrent'])})"
        case "hold":
            text = f"{who} held: {d.get('reason')}"
        case "start":
            how = "resumed its staged session" if d.get("resumed") else "started"
            text = f"{who} {how} (session {d.get('session_id')}, pid {d.get('pid')})"
            if d.get("branch"):
                text += f" on `{d['branch']}`"
            if d.get("alongside"):
                text += f", alongside: {d['alongside']}"
            if resumed := resume_label(d):
                text += f", {resumed} (forking session {d.get('resume_session')})"
            if d.get("beside"):
                text += f"; beside {', '.join(d['beside'])}"
            if d.get("error"):
                text += f"; launch failed: {d['error']}"
        case "setup":
            text = (f"{who} {'stage ' if d.get('stage') else ''}setup running (pid {d.get('pid')}): "
                    + "; ".join(d.get("commands") or []))
        case "stage":
            text = f"{who} staging (session {d.get('session_id')}, pid {d.get('pid')}) in {d.get('worktree')}"
        case "staged":
            plan = (d.get("plan") or "").strip().splitlines()
            text = f"{who} staged; waits for the lock" + (f": {plan[0][:160]}" if plan else "")
        case "merge":
            text = f"{who} merged `{d.get('branch')}` into main at {str(d.get('sha') or '')[:10]} ({d.get('commits')} commit(s))"
            if d.get("squashed"):
                text += f", squashed from {d['squashed']}"
            if d.get("resolved"):
                text += f"; prose conflict resolved in {', '.join(d['resolved'][:5])}"
        case "land" if d.get("state") == "started":
            text = f"{who} landing `{d.get('branch')}` on main (`land:`)"
        case "land":
            text = f"{who} landed `{d.get('branch')}` on main at {str(d.get('sha') or '')[:10]} (`land:`)"
            if d.get("squashed"):
                text += f", squashed from {d['squashed']}"
        case "resolve":
            text = (f"{who} prose conflict in {', '.join(d.get('files', [])[:5])}; merge executor resolving "
                    f"(pid {d.get('pid')})")
        case "conflict":
            files = f" in {', '.join(d['files'][:5])}" if d.get("files") else ""
            text = (f"{who} {'`land:` ' if d.get('land') else ''}rebase conflicted{files}; `{d.get('branch')}` kept, "
                    "main untouched")
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
        case "ack":
            ids = d.get("events") or []
            text = f"acknowledged invalid {', '.join(f'#{i}' for i in ids)}" if ids else "acknowledged: no invalid signal was live"
        case "read":
            text = f"read mark → #{d.get('through')}" + (f" (closeout {d['closeout']})" if d.get("closeout") else "")
        case "clear":
            text = f"queue `{ev.repo}` cleared" + (f": {d['note']}" if d.get("note") else "")
        case "enqueue":
            extras = [f"{k}: {d[k]}" for k in ("depends_on", "locks") if d.get(k)]
            extras += [k for k in ("needs_planner", "readonly", "ultracode", "audit") if d.get(k)]
            extras += [f"template: {d['template']}"] if d.get("template") else []
            extras += [f"alongside: {d['alongside']}"] if d.get("alongside") else []
            extras += [resume_label(d)] if d.get("resume_of") else []
            text = f"{who} queued" + (f" ({', '.join(extras)})" if extras else "")
            text += f"; warning: {d['warning']}" if d.get("warning") else ""
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
        case "daemon_restart":
            text = f"daemon restarting ({d.get('reason')}): new daemon pid {d.get('new_pid')}"
            text += f"; adopts {', '.join(d['running'] + d['staging'])}" if d.get("running") or d.get("staging") else ""
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
        case "backlog_draw":
            text = f"{who} drawn from the backlog ({d.get('from')})" + (" · full-length" if d.get("full_length") else "")
        case "backlog_sleep":
            text = f"backlog sleeps until {d.get('until')}: {d.get('reason')}"
        case "watch_flag":
            text = f"watch flag ({d.get('flag')}): {d.get('text')}" + (f" — watch {d['watch']}" if d.get("watch") else "")
        case "watch":
            text = f"watch {who} launched: {d.get('reason')}"
        case "push":
            text = (f"pushed {d.get('target')} {d.get('branch')} to {d.get('remote')} ({str(d.get('sha'))[:8]}"
                    + (", up to date" if d.get("up_to_date") else "") + f"; {', '.join(d.get('why', []))})")
        case "push_failed":
            text = f"push of {d.get('target')} {d.get('branch')} failed: {d.get('error')}"
            if d.get("output") and not d.get("rejected"):
                text += " — git: " + " ⏎ ".join(d["output"].splitlines()[-3:])
        case "closeout_push":
            text = (f"closeout push: {', '.join(d.get('pushed', [])) or 'nothing'} pushed"
                    + (f"; FAILED: {', '.join(d['failed'])}" if d.get("failed") else ""))
        case "autopilot":
            text = f"autopilot **{d.get('mode')}** (was {d.get('was')})"
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


def _line(ev: Event, commit: Event | None = None) -> str:
    shown = ev.paths[:1] if ev.kind == "finish" else ev.paths     # the report; log and done/ are derivable
    paths = f" — {', '.join(shown)}" if shown and ev.kind in ("finish", "tamper", "request") else ""
    folded = f" · committed {commit.detail.get('sha', '')[:10]} (#{commit.id})" if commit else ""
    return f"- {clock(ev.ts)} #{ev.id} {describe(ev)}{paths}{folded}"


def unreviewed_lines(view: View, finishes: list[Event]) -> list[str]:
    """The *Unreviewed reports* summary: one line per finish, with its report, carry,
    `slow:`, pick, a red item's reasons and whether it was carried from before the checkpoint."""
    out = []
    for ev in finishes:
        d = ev.detail
        why = f" ({'; '.join(d['reasons'])})" if d.get("status") == "red" and d.get("reasons") else ""
        report = f" — {ev.paths[0]}" if ev.paths else ""
        carry = f" · carry: {d['carry']}" if d.get("carry") is not None else ""
        slow = f" · slow: {s}" if (s := slow_text(d)) else ""
        pick = f" · pick: {d['pick']}" if d.get("pick") else ""
        resumed = f" · {r}" if (r := resume_label(d)) else ""
        resumed += f" · **{changed_text(chg)}**" if (chg := view.changed_since_review(ev.item)) else ""
        old = (" · **carried from before checkpoint "
               f"#{view.checkpoint.id}**") if view.checkpoint and ev.id < view.checkpoint.id else ""
        out.append(f"- {ev.item} **{d.get('status')}**{why}{resumed}, finished {clock(ev.ts)} (#{ev.id})"
                   f"{report}{carry}{slow}{pick}{old}")
    return out


def passed_over_id(events: list[Event]) -> int:
    """The newest request among `events` that rewrote `state.md` (a `closeout:` whatever its
    targets), else 0: the planner wrote down what it made of everything before it."""
    return max((e.id for e in events if e.kind == "request" and (
        "state.md" in (e.detail.get("targets") or []) or is_closeout(e.detail.get("message") or ""))), default=0)


def settled_why(ev: Event, view: View, passed: int, live_bad: set[int]) -> str | None:
    """Why `ev` is hidden behind a count as already dealt with, or None to show it. A
    rejected signal shows until acknowledged or a checkpoint passes; every push is noise;
    an item's events go once its report is reviewed (reds aside); and whatever a later
    `state.md` request passed over goes too, bar what is still live: an unreviewed finish
    and a hold (shown only while in effect)."""
    if signal_invalid(ev):
        return None if ev.id in live_bad else "rejected signals acknowledged or before the checkpoint"
    if ev.kind == "push":
        return "pushes"
    if ev.kind == "warning" and ev.detail.get("checkpoint") and ev.detail.get("finish"):
        return "still-unreviewed warnings (see *Unreviewed reports*)"
    if ev.item and ev.kind not in RED_KINDS and view.reviewed(ev.item) and not view.is_watch(ev.item):
        return "of reviewed items"      # a watch's warnings stay
    if ev.id < passed and ev.kind not in ("finish", "hold"):
        return f"passed over by `state.md` request #{passed}"
    return None


def fold_commits(evs: list[Event]) -> tuple[list[Event], dict[int, Event]]:
    """Each control commit folded into the request it committed (the one just before it
    with its subject): the events left, and request id → its commit."""
    out, folded, last = [], {}, None
    for e in evs:
        if (e.kind == "commit" and e.detail.get("scope") == "control" and last is not None
                and (last.detail.get("message") or "").splitlines()[:1] == [e.detail.get("subject")]):
            folded[last.id], last = e, None
            continue
        last = e if e.kind == "request" else last
        out.append(e)
    return out, folded


def render(cfg: Config, log: EventLog, view: View, heading: str | None = None) -> str:
    """The inbox; with `heading` (the session digest's), that replaces its title and
    the mark-read hint, since the digest moves the read mark itself."""
    mark = log.read_mark()
    since_mark = log.query(since_id=mark)
    passed, live_bad = passed_over_id(since_mark), {e.id for e in live_invalids(log)}
    why = {e.id: w for e in since_mark if (w := settled_why(e, view, passed, live_bad))}
    events = [e for e in since_mark if e.id not in why]
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
    finished = [e for e in events if e.kind == "finish" and not view.is_watch(e.item)]     # Night shift lists those
    changes, commits = fold_commits([e for e in events if e.kind in ("request", "commit")])
    open_qs = [q for q in questions.scan(cfg.control) if q.is_open]
    open_ids = {q.id for q in open_qs}
    asked = [e for e in events if e.kind in QUESTION_KINDS
             and not (e.kind == "ask" and e.detail.get("question") in open_ids)]
    changes += [e for e in events if e.kind == "merge"]
    other = [e for e in events if e not in red + holds + finished + changes + asked
             and e.kind not in ("read", "enqueue", "start", "ask", "hold", "stage", "worktree", "progress", "setup",
                                "finish", "request", "commit", *NIGHT_KINDS)]

    out = [heading or f"# Inbox — {cfg.project}", ""]
    since = f"since read mark #{mark}" + (f" ({clock(mark_ev.ts)})" if mark_ev else "")
    counts: dict[str, int] = {}
    for w in why.values():
        counts[w] = counts.get(w, 0) + 1
    hidden = [f"{n} {w}" for w, n in counts.items()]
    out.append(f"Generated {clock(now())} · {len(since_mark)} event(s) {since}, through #{top}"
               + (f" · hidden as settled: {', '.join(hidden)}" if hidden else "") + ".")
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
        out += ["", "## Unreviewed reports", *unreviewed_lines(view, unreviewed)]
        out.append("  Each stays unreviewed until Matt says process X (or process next / process all).")
    if pick_rows := pick_lines(cfg, log, view):
        # Standing too: a picker waits for Matt whatever the read mark says.
        out += ["", "## Picks", *pick_rows]
    if night_rows := nightshift.lines(cfg, log, view, mark):
        out += ["", "## Night shift", *night_rows]
    sections = [("Red", red), ("Holds", holds), ("Finished", finished), ("Questions resolved", asked),
                ("Requests and commits", changes), ("Other", other)]
    for title, evs in sections:
        if evs:
            out += ["", f"## {title}", *(_line(e, commits.get(e.id)) for e in evs)]
    if not events:
        out += ["", "Nothing new."]

    out += ["", "## Now"]
    mem, level = memory.line(cfg)
    out.append(f"- {'**' + mem + '**' if level != 'ok' else mem}")
    decisions = decide(cfg, view)
    for repo in cfg.repos:
        d = decisions[repo]
        queued = [i.name + (f" ({resume_label(i.meta())})" if i.resume_of else "") for i in view.queued.get(repo, [])]
        running = []
        for item_id, rec in view.running.items():
            if rec["repo"] != repo:
                continue
            text = rec["name"] + {"merge": " (merging)", "setup": " (setting up)"}.get(rec.get("phase"), "")
            if tags := ([resume_label(rec)] if rec.get("resume_of") else []) + (
                    ["alongside"] if rec.get("alongside") else []) + (
                    [f"beside {', '.join(rec['beside'])}"] if rec.get("beside") else []):
                text += f" ({', '.join(tags)})"
            if rec.get("phase", "exec") == "exec":
                text += now_progress(log, item_id)
            running.append(text)
        staged = [f"{rec['name']} ({rec.get('phase')})" for rec in view.staged.values() if rec["repo"] == repo]
        parts = []
        if ev := view.paused.get(repo):
            parts.append(f"**PAUSED** since {clock(ev.ts)} by {ev.detail.get('by', 'unknown')} (`resume: {repo}`)")
        if running:
            parts.append(f"running {', '.join(running)}")
        if staged:
            parts.append(f"staged {', '.join(staged)}")
        if queued:
            parts.append(f"queued {', '.join(queued)}")
        if d.action in ("hold", "wait") and repo not in view.paused:
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
