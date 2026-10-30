"""What is queued, running and done, and what may run next. Shared by daemon and CLI."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import audits, git, globs, items, procs, resume
from .config import Config
from .events import Event, EventLog
from .items import Item
from .util import clock, now, parse_stamp


@dataclass
class DaemonInfo:
    alive: bool
    pid: int | None = None
    started: str | None = None
    beat: str | None = None     # its last heartbeat (`beat` in daemon.json)
    stale: bool = False         # alive, but no heartbeat for three memory samples (at least 90s)
    died: bool = False          # not alive, and its lock is still there: it never stopped cleanly


def daemon_info(cfg: Config) -> DaemonInfo:
    """The daemon as `.conductor/daemon.json` tells it: a clean stop removes the file,
    so a file whose process is gone is a daemon that died (killed, or its tree was)."""
    lock = cfg.state_dir / "daemon.json"
    try:
        info = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DaemonInfo(False)
    if not isinstance(info, dict):
        return DaemonInfo(False)
    pid, started, beat = info.get("pid"), info.get("started"), info.get("beat")
    if not procs.alive(pid, info.get("create_time")):
        return DaemonInfo(False, pid, started, beat, died=True)
    stale = False
    if beat and cfg.memory_sample_seconds:
        try:
            stale = (now() - parse_stamp(beat)).total_seconds() > max(3 * cfg.memory_sample_seconds, 90)
        except ValueError:
            pass
    return DaemonInfo(True, pid, started, beat, stale=stale)


@dataclass
class Decision:
    repo: str
    action: str                 # run | resume | stage | wait | hold | busy | idle
    item: Item | None = None
    reason: str = ""


@dataclass
class View:
    queued: dict[str, list[Item]] = field(default_factory=dict)
    running: dict[str, dict] = field(default_factory=dict)     # item id → run record
    staged: dict[str, dict] = field(default_factory=dict)      # item id → stage record (item still queued)
    done: set[str] = field(default_factory=set)
    finishes: dict[str, Event] = field(default_factory=dict)   # item id → last finish event
    reviews: dict[str, Event] = field(default_factory=dict)    # item id → its latest `reviewed` event
    changed: dict[str, Event] = field(default_factory=dict)    # item id → its latest `report_changed` event
    read_mark: int = 0
    red_hold: dict[str, Event] = field(default_factory=dict)   # repo → red event holding it
    planner_hold: dict[str, Event] = field(default_factory=dict)  # repo → unread finish
    paused: dict[str, Event] = field(default_factory=dict)     # repo → the `pause` event no `resume` followed
    stray: list[Path] = field(default_factory=list)            # queue folders for unknown repos
    checkpoint: Event | None = None                            # the latest `checkpoint` event
    audit_used: dict[str, Event] = field(default_factory=dict) # note's item id → `audit_used` event

    def known(self) -> list[str]:
        ids = [i.id for q in self.queued.values() for i in q]
        return ids + list(self.running) + sorted(self.done)

    def status_of(self, item_id: str) -> str:
        if item_id in self.running:
            return "running"
        if item_id in self.done:
            ev = self.finishes.get(item_id)
            return ev.detail.get("status", "unknown") if ev else "unknown"
        return "queued"

    def reviewed(self, item_id: str) -> bool:
        """Its report has a `reviewed` event, and no `report_changed` since (a reviewed report
        or its audit notes edited afterwards is unreviewed again until the next `reviewed`).
        A watch's report never waits for review: the inbox's *Night shift* lists it."""
        if self.is_watch(item_id):
            return True
        rev, chg = self.reviews.get(item_id), self.changed.get(item_id)
        return rev is not None and (chg is None or chg.id < rev.id)

    def is_watch(self, item_id: str) -> bool:
        """A finished watch item (`template: watch`, docs/night-shift.md)."""
        return (fin := self.finishes.get(item_id)) is not None and fin.detail.get("template") == "watch"

    def changed_since_review(self, item_id: str) -> Event | None:
        """The `report_changed` that made a reviewed report unreviewed again, or None."""
        chg = self.changed.get(item_id)
        return chg if chg is not None and not self.reviewed(item_id) else None

    def unreviewed(self) -> list[Event]:
        """The finish events of items whose report is not reviewed (`reviewed`), oldest first."""
        return sorted((ev for i, ev in self.finishes.items() if not self.reviewed(i)), key=lambda e: e.id)

    def settled(self, item_id: str) -> bool:
        """Hidden from the current view: finished before the latest checkpoint and reviewed
        before it too, and not the red holding its queue. Everything else carries forward."""
        cp, fin, rev = self.checkpoint, self.finishes.get(item_id), self.reviews.get(item_id)
        if self.is_watch(item_id):              # reviewed without a `reviewed` event
            return cp is not None and fin.id < cp.id
        if cp is None or fin is None or rev is None or fin.id > cp.id or rev.id > cp.id or not self.reviewed(item_id):
            return False
        return not any(ev.item == item_id for ev in self.red_hold.values())

    def claimed_notes(self) -> set[str]:
        """Notes handed to a cleanup that is running: not free for another."""
        return {n for rec in self.running.values() for n in rec.get("notes") or []}

    def unused_notes(self, cfg: Config, repo: str) -> list[audits.Note]:
        return audits.notes(cfg, repo, self.finishes, self.audit_used, self.claimed_notes())


def read_run(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def scan(cfg: Config, log: EventLog) -> View:
    view = View(read_mark=log.read_mark(), checkpoint=log.last("checkpoint"))
    qroot = cfg.path("queue")
    if qroot.is_dir():
        for d in sorted(p for p in qroot.iterdir() if p.is_dir()):
            if d.name not in cfg.repos:
                view.stray.append(d)
    for repo in cfg.repos:
        q = [items.parse(p, repo) for p in items.item_files(qroot / repo)]
        view.queued[repo] = sorted(q, key=lambda i: (i.num, i.name))
        for rec in sorted(cfg.path("running", repo).glob("*.run.json")) if cfg.path("running", repo).is_dir() else []:
            if rec.with_name(rec.name.removesuffix(".run.json") + ".md").is_file():
                data = read_run(rec)
                view.running[data["item"]] = data
        for rec in sorted(cfg.path("running", repo).glob("*.stage.json")) if cfg.path("running", repo).is_dir() else []:
            data = read_run(rec)
            view.staged[data["item"]] = data
        ddir = cfg.path("done", repo)
        if ddir.is_dir():
            view.done |= {f"{repo}/{p.stem}" for p in ddir.glob("*.md")}
    for ev in log.query(kinds=["finish"]):
        view.finishes[ev.item] = ev
    for ev in log.query(kinds=["reviewed"]):
        view.reviews[ev.item] = ev
    for ev in log.query(kinds=["report_changed"]):
        view.changed[ev.item] = ev
    for ev in log.query(kinds=["audit_used"]):
        for note in ev.detail.get("notes", []):
            view.audit_used.setdefault(note, ev)
    clears = {ev.repo: ev.id for ev in log.query(kinds=["clear"])}
    for ev in log.query(kinds=["red"]):
        if ev.detail.get("after_red", "stop") == "stop" and ev.id > clears.get(ev.repo, 0):
            view.red_hold[ev.repo] = ev
    for ev in log.query(kinds=["pause", "resume"]):
        if ev.kind == "pause":
            view.paused[ev.repo] = ev
        else:
            view.paused.pop(ev.repo, None)
    for ev in view.finishes.values():
        if ev.detail.get("needs_planner") and ev.id > view.read_mark:
            view.planner_hold.setdefault(ev.repo, ev)
    for item in (i for q in view.queued.values() for i in q if i.resume_of and not i.errors):
        if why := resume.problem(view, item):
            item.errors.append(why)
    return view


def can_share(cfg: Config, head: Item, rec: dict) -> bool:
    """May `head` run beside the running item `rec` in the same repo, as far as the repo
    lock goes? Only in worktrees, and never when either is an exclusive template
    (`audits.EXCLUSIVE`). A readonly item (either one) never merges, so it neither holds
    the repo lock nor waits for it; an `alongside` item (either one) is the planner's word
    that they are unrelated. Their named locks are `decide`'s. Otherwise (legacy `paths`)
    both declared `paths`, the globs are disjoint (prose aside) and they share no named lock."""
    opts = cfg.opts(head.repo)
    if head.template == "watch" or rec.get("template") == "watch":
        return True                 # a watch only reads, for minutes, and must see a stall as it is
    if not (opts.worktrees and rec.get("worktree")):
        return False
    if head.template in audits.EXCLUSIVE or rec.get("template") in audits.EXCLUSIVE:
        return False
    if head.readonly or rec.get("readonly") or head.alongside or rec.get("alongside"):
        return True
    return (globs.disjoint(head.paths, rec.get("paths") or [], opts.prose)
            and not set(head.locks) & set(rec.get("locks") or []))


def may_stage(cfg: Config, view: View, head: Item) -> bool:
    opts = cfg.opts(head.repo)
    wanted = head.stage if head.stage is not None else opts.stage
    others = [i for i, rec in view.staged.items() if rec["repo"] == head.repo and i != head.id]
    # A cleanup or disk cleanup never stages: what it is handed (notes, sizes) is fixed at
    # launch, and an item running meanwhile may still change it. A resume item never
    # does: a stage is a new session, and its whole point is the original's.
    return (opts.worktrees and wanted and not others and head.template not in audits.NO_STAGE
            and not head.resume_of)


def running_locks(view: View) -> dict[str, str]:
    """{named lock: item} held by running items: one executing or still in setup holds its
    named locks; one merging or concluding has let them go. `decide` and `early_starts` both
    use this, so the rule lives in one place."""
    return {lock: item_id for item_id, rec in view.running.items() if rec.get("phase", "exec") in ("exec", "setup")
            for lock in rec.get("locks", [])}


def decide(cfg: Config, view: View) -> dict[str, Decision]:
    """One decision per configured repo. Items start strictly in NNN order per repo.

    A finished item still waiting to merge keeps its repo lock but not its named locks;
    one still in setup holds both.
    A readonly item takes no repo lock (`can_share`): at the head it starts beside
    whatever runs, and the items after it never wait for it. An `alongside` item, or any
    item once an `alongside` one is all that runs, starts beside the others too. Either
    kind behind the head may start before it reaches the head (`early_starts`).
    """
    held_locks = running_locks(view)
    known = view.known()
    out: dict[str, Decision] = {}
    for repo in cfg.repos:
        running_here = {i: r for i, r in view.running.items() if r["repo"] == repo}
        queue = view.queued.get(repo, [])
        head = queue[0] if queue else None
        if head is None:
            out[repo] = Decision(repo, "busy" if running_here else "idle", None,
                                 f"running {next(iter(running_here))}" if running_here else "")
            continue
        out[repo] = d = _decide_head(cfg, view, repo, head, known, held_locks, running_here)
        if d.action in ("run", "resume"):
            for lock in head.locks:
                held_locks[lock] = head.id
    return out


def early_starts(cfg: Config, view: View, decisions: dict[str, Decision]) -> list[Item]:
    """Readonly and `alongside` items behind their queue's head that may start now, in
    queue order. Neither takes the repo lock (`can_share`), so it need not wait to reach
    the head; it still waits for its named locks (held by running items, or by heads that
    `decisions` starts), for every `depends_on` to finish done, and for any running item it
    may not share with (an exclusive template). An exclusive template in the queue is a
    barrier: nothing behind it starts early, so a stream of small items never starves it.
    A held repo starts none of them; one that `_hold` would hold waits until it is the
    head, which logs why."""
    held = set(running_locks(view))
    held |= {lock for d in decisions.values() if d.action in ("run", "resume") for lock in d.item.locks}
    known = view.known()
    out: list[Item] = []
    for repo in cfg.repos:
        trees = cfg.opts(repo).worktrees
        queue = view.queued.get(repo, [])
        running_here = [r for r in view.running.values() if r["repo"] == repo]
        for i, item in enumerate(queue[1:], 1):
            # A watch starts beside anything, past any barrier, in any repo: it always has a worktree.
            watch = item.template == "watch"
            if not watch and (not trees or repo in view.paused or any(q.template in audits.EXCLUSIVE for q in queue[:i])):
                continue
            if (not (item.readonly or item.alongside) or item.id in view.staged
                    or (item.template in audits.NO_STAGE + audits.EXCLUSIVE and not watch)
                    or set(item.locks) & held or _hold(cfg, view, repo, item, known)
                    or not all(can_share(cfg, item, rec) for rec in running_here)):
                continue
            if any(view.status_of(items.resolve_ref(ref, repo, known)) != "done" for ref in item.depends_on):
                continue
            held |= set(item.locks)
            out.append(item)
    return out


def _short(item_id: str) -> str:
    return item_id.split("/", 1)[-1]        # the name: a bare number may be shared


def _why_not_beside(cfg: Config, item: Item, other: str | None) -> str:
    """Why `item` can't start beside a running or queued item whose template is `other`."""
    if not cfg.opts(item.repo).worktrees:
        return "repo busy; no worktrees"
    if item.template in audits.EXCLUSIVE:
        return f"{item.template} runs alone"
    if other in audits.EXCLUSIVE:
        return "template runs alone"
    return "repo busy, no `alongside`"


def start_estimate(cfg: Config, view: View, item: Item) -> str:
    """When a queued item will start and why, for its queue receipt: `starts now`, `starts
    now, beside 028 (alongside)`, `waits behind 024 (repo busy, no alongside) and 025
    (template runs alone)`, or `held: …`. The scheduler's rules (`decide`, `early_starts`),
    read for one item; a guess at queue time, not a promise."""
    repo, known = item.repo, view.known()
    if (ev := view.paused.get(repo)) and item.template != "watch":
        return f"waits: `{repo}` paused {clock(ev.ts)}"
    if hold := _hold(cfg, view, repo, item, known):
        return f"held: {hold.reason}"
    queue = view.queued.get(repo, [])
    ahead = queue[:next((i for i, q in enumerate(queue) if q.id == item.id), len(queue))]
    running = {i: r for i, r in view.running.items() if r["repo"] == repo}
    shares = (item.readonly or item.alongside or item.template == "watch") and cfg.opts(repo).worktrees
    behind: list[str] = []
    for item_id, rec in running.items():
        if not can_share(cfg, item, rec):
            behind.append(f"{_short(item_id)} ({_why_not_beside(cfg, item, rec.get('template'))})")
    for q in ahead:         # serial: strict NNN order; sharing: only an exclusive template is a barrier
        if q.template in audits.EXCLUSIVE and item.template != "watch":
            behind.append(f"{_short(q.id)} (template runs alone)")
        elif not shares:
            behind.append(f"{_short(q.id)} (queued ahead, no `alongside`)")
    held = running_locks(view)
    for lock in item.locks:
        if lock in held:
            behind.append(f"{_short(held[lock])} (lock {lock})")
    for ref in item.depends_on:
        dep = items.resolve_ref(ref, repo, known)
        if dep and view.status_of(dep) in ("queued", "running"):
            behind.append(f"{_short(dep)} (depends_on)")
    behind = list(dict.fromkeys(behind))
    if behind:
        return "waits behind " + (", ".join(behind[:-1]) + " and " + behind[-1] if len(behind) > 1 else behind[0])
    if running:
        why = "readonly" if item.readonly else "alongside" if item.alongside else "watch" if item.template == "watch" else "disjoint paths"
        return f"starts now, beside {', '.join(_short(i) for i in running)} ({why})"
    return "starts now"


def parked_estimate(cfg: Config, view: View, item: Item) -> str:
    """Which waiting parked edits `item` will carry as the queue stands, for its receipt:
    none unless it is serial, and none an earlier serial item in its queue takes first."""
    from . import parked
    edits = parked.eligible(item, parked.waiting(cfg.control, item.repo))
    queue = view.queued.get(item.repo, [])
    for q in queue[:next((i for i, q in enumerate(queue) if q.id == item.id), len(queue))]:
        edits = [e for e in edits if e not in parked.eligible(q, edits)]
    if not edits:
        return ""
    shown = "; ".join(e.text if len(e.text) <= 60 else e.text[:59] + "…" for e in edits[:3])
    return f"carries {len(edits)} parked edit(s) as things stand: {shown}" + ("; …" if len(edits) > 3 else "")


def _decide_head(cfg: Config, view: View, repo: str, head: Item, known: list[str],
                 held_locks: dict[str, str], running_here: dict[str, dict]) -> Decision:
    if (ev := view.paused.get(repo)) and head.template != "watch":
        # A wait, not a hold: Matt paused it on purpose, so no `hold` event and no toast.
        return Decision(repo, "wait", head, f"paused {clock(ev.ts)}; `resume: {repo}` releases it")
    if d := _hold(cfg, view, repo, head, known):
        return d
    # What the head waits on; staging is allowed only when every wait is a busy lock or
    # a dependency that is running.
    waits: list[tuple[str, bool]] = []
    for item_id, rec in running_here.items():
        if not can_share(cfg, head, rec):
            what = {"merge": "merging", "setup": "setting up"}.get(rec.get("phase"), "running")
            waits.append((f"repo busy: {item_id} {what}", True))
            break
    for ref in head.depends_on:
        dep = items.resolve_ref(ref, repo, known)
        status = view.status_of(dep)
        if status in ("queued", "running"):
            waits.append((f"waiting on {dep} ({status})", status == "running"))
    for lock in head.locks:
        if lock in held_locks:
            waits.append((f"lock {lock} held by {held_locks[lock]}", True))
    stage = view.staged.get(head.id)
    if waits:
        reason = waits[0][0]
        if stage is None and all(ok for _, ok in waits) and may_stage(cfg, view, head):
            return Decision(repo, "stage", head, reason)
        if stage is not None:
            reason += f"; {'staging' if stage.get('phase') == 'staging' else 'staged'}"
        return Decision(repo, "wait", head, reason)
    # Checked only once nothing else blocks it: an audit item running ahead may add notes.
    if head.template == "cleanup" and not view.unused_notes(cfg, repo):
        return Decision(repo, "hold", head, f"cleanup: no unused audit notes for {repo}; it runs once an "
                                            "`audit: true` item finishes, or cancel it")
    if stage is not None:
        if stage.get("phase") == "staging":
            return Decision(repo, "wait", head, "staging; executes when the stage finishes")
        return Decision(repo, "resume", head)
    return Decision(repo, "run", head)


def _hold(cfg: Config, view: View, repo: str, head: Item, known: list[str]) -> Decision | None:
    def hold(reason: str) -> Decision:
        return Decision(repo, "hold", head, reason)

    if not git.is_repo(cfg.repos[repo]):
        return hold(f"repo path {cfg.repos[repo]} is not a git repo")
    if head.errors:
        return hold("invalid item: " + "; ".join(head.errors))
    if head.id in view.done or head.id in view.running:
        return hold(f"{head.id} already ran; a re-run needs a new item number")
    looks = head.resume_of or head.template == "watch"   # asking why it went red is the point; a watch only reads
    if (ev := view.red_hold.get(repo)) and not looks:
        return hold(f"queue held after red {ev.item} (event #{ev.id}); write `clear: {repo}` to signals/")
    if (ev := view.planner_hold.get(repo)) and head.template != "watch":
        return hold(f"needs_planner: report for {ev.item} unread (finish #{ev.id}); move the read mark past it")
    unknown = [lock for lock in head.locks if lock not in cfg.locks]
    if unknown:
        return hold(f"unknown lock(s) {unknown}; declare them in conductor.toml")
    for ref in head.depends_on:
        dep = items.resolve_ref(ref, repo, known)
        if dep is None and len(both := items.ref_matches(ref, repo, known)) > 1:
            return hold(f"dependency {ref} is ambiguous ({', '.join(both)} share its number): name it in full")
        if dep is None:
            return hold(f"unknown dependency {ref}")
        status = view.status_of(dep)
        if status == "queued" and dep.startswith(repo + "/"):
            return hold(f"depends on {dep}, which is later in its own queue")
        if status not in ("queued", "running", "done"):
            return hold(f"dependency {dep} ended {status}")
    return None


def dependency_warning(view: View, item: Item, cancelled: set[str]) -> str | None:
    """Why `item`'s `depends_on` can never be met in order, said at write time so the planner
    fixes it at once instead of finding a hold later: a later item in its own queue, or one
    already red, partial or cancelled (`cancelled`: ids of cancelled items). None if it can
    be met, or names something not written yet."""
    known = view.known()
    queue = [i.id for i in view.queued.get(item.repo, [])]
    out = []
    for ref in item.depends_on:
        dep = items.resolve_ref(ref, item.repo, known)
        if dep is None:
            if gone := items.resolve_ref(ref, item.repo, sorted(cancelled)):
                out.append(f"`{ref}` names {gone}, which was cancelled: it holds as an unknown dependency")
            continue
        status = view.status_of(dep)
        if status == "queued" and dep in queue and item.id in queue and queue.index(dep) > queue.index(item.id):
            out.append(f"`{ref}` names {dep}, later in its own queue: it holds once it is the head")
        elif status not in ("queued", "running", "done"):
            out.append(f"`{ref}` names {dep}, which ended {status}: it holds once it is the head")
    if not out:
        return None
    return "depends_on can't be met: " + "; ".join(out) + (
        ". Same-repo order already serializes; drop it or name an item that can finish done")
