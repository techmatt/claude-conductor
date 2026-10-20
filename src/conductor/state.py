"""What is queued, running and done, and what may run next. Shared by daemon and CLI."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import audits, git, globs, items
from .config import Config
from .events import Event, EventLog
from .items import Item


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
    reviews: dict[str, Event] = field(default_factory=dict)    # item id → its `reviewed` event
    read_mark: int = 0
    red_hold: dict[str, Event] = field(default_factory=dict)   # repo → red event holding it
    planner_hold: dict[str, Event] = field(default_factory=dict)  # repo → unread finish
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

    def unreviewed(self) -> list[Event]:
        """The finish events of items whose report has no `reviewed` event, oldest first."""
        return sorted((ev for i, ev in self.finishes.items() if i not in self.reviews), key=lambda e: e.id)

    def settled(self, item_id: str) -> bool:
        """Hidden from the current view: finished before the latest checkpoint and reviewed
        before it too, and not the red holding its queue. Everything else carries forward."""
        cp, fin, rev = self.checkpoint, self.finishes.get(item_id), self.reviews.get(item_id)
        if cp is None or fin is None or rev is None or fin.id > cp.id or rev.id > cp.id:
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
        view.reviews.setdefault(ev.item, ev)
    for ev in log.query(kinds=["audit_used"]):
        for note in ev.detail.get("notes", []):
            view.audit_used.setdefault(note, ev)
    clears = {ev.repo: ev.id for ev in log.query(kinds=["clear"])}
    for ev in log.query(kinds=["red"]):
        if ev.detail.get("after_red", "stop") == "stop" and ev.id > clears.get(ev.repo, 0):
            view.red_hold[ev.repo] = ev
    for ev in view.finishes.values():
        if ev.detail.get("needs_planner") and ev.id > view.read_mark:
            view.planner_hold.setdefault(ev.repo, ev)
    return view


def can_share(cfg: Config, head: Item, rec: dict) -> bool:
    """May `head` run beside the running item `rec` in the same repo? Only in worktrees,
    when both declared `paths`, the globs are disjoint (prose aside) and they share no
    named lock."""
    opts = cfg.opts(head.repo)
    return (opts.worktrees and bool(rec.get("worktree"))
            and globs.disjoint(head.paths, rec.get("paths") or [], opts.prose)
            and not set(head.locks) & set(rec.get("locks") or []))


def may_stage(cfg: Config, view: View, head: Item) -> bool:
    opts = cfg.opts(head.repo)
    wanted = head.stage if head.stage is not None else opts.stage
    others = [i for i, rec in view.staged.items() if rec["repo"] == head.repo and i != head.id]
    # A cleanup or disk audit never stages: what it is handed (notes, sizes) is fixed at
    # launch, and an item running meanwhile may still change it.
    return opts.worktrees and wanted and not others and head.template not in audits.NO_STAGE


def decide(cfg: Config, view: View) -> dict[str, Decision]:
    """One decision per configured repo. Items start strictly in NNN order per repo.

    A finished item still waiting to merge keeps its repo lock but not its named locks.
    """
    held_locks: dict[str, str] = {}
    for item_id, rec in view.running.items():
        if rec.get("phase", "exec") == "exec":
            for lock in rec.get("locks", []):
                held_locks[lock] = item_id
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


def _decide_head(cfg: Config, view: View, repo: str, head: Item, known: list[str],
                 held_locks: dict[str, str], running_here: dict[str, dict]) -> Decision:
    if d := _hold(cfg, view, repo, head, known):
        return d
    # What the head waits on; staging is allowed only when every wait is a busy lock or
    # a dependency that is running.
    waits: list[tuple[str, bool]] = []
    for item_id, rec in running_here.items():
        if not can_share(cfg, head, rec):
            what = "merging" if rec.get("phase") == "merge" else "running"
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
    if ev := view.red_hold.get(repo):
        return hold(f"queue held after red {ev.item} (event #{ev.id}); write `clear: {repo}` to signals/")
    if ev := view.planner_hold.get(repo):
        return hold(f"needs_planner: report for {ev.item} unread (finish #{ev.id}); move the read mark past it")
    unknown = [lock for lock in head.locks if lock not in cfg.locks]
    if unknown:
        return hold(f"unknown lock(s) {unknown}; declare them in conductor.toml")
    for ref in head.depends_on:
        dep = items.resolve_ref(ref, repo, known)
        if dep is None:
            return hold(f"unknown dependency {ref}")
        status = view.status_of(dep)
        if status == "queued" and dep.startswith(repo + "/"):
            return hold(f"depends on {dep}, which is later in its own queue")
        if status not in ("queued", "running", "done"):
            return hold(f"dependency {dep} ended {status}")
    return None
