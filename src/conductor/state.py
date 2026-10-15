"""What is queued, running and done, and what may run next. Shared by daemon and CLI."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import git, items
from .config import Config
from .events import Event, EventLog
from .items import Item


@dataclass
class Decision:
    repo: str
    action: str                 # run | wait | hold | busy | idle
    item: Item | None = None
    reason: str = ""


@dataclass
class View:
    queued: dict[str, list[Item]] = field(default_factory=dict)
    running: dict[str, dict] = field(default_factory=dict)     # item id → run record
    done: set[str] = field(default_factory=set)
    finishes: dict[str, Event] = field(default_factory=dict)   # item id → last finish event
    read_mark: int = 0
    red_hold: dict[str, Event] = field(default_factory=dict)   # repo → red event holding it
    planner_hold: dict[str, Event] = field(default_factory=dict)  # repo → unread finish
    stray: list[Path] = field(default_factory=list)            # queue folders for unknown repos

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


def read_run(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def scan(cfg: Config, log: EventLog) -> View:
    view = View(read_mark=log.read_mark())
    qroot = cfg.path("queue")
    if qroot.is_dir():
        for d in sorted(p for p in qroot.iterdir() if p.is_dir()):
            if d.name not in cfg.repos:
                view.stray.append(d)
    for repo in cfg.repos:
        q = [items.parse(p, repo) for p in sorted((qroot / repo).glob("*.md"))] if (qroot / repo).is_dir() else []
        view.queued[repo] = sorted(q, key=lambda i: (i.num, i.name))
        for rec in sorted(cfg.path("running", repo).glob("*.run.json")) if cfg.path("running", repo).is_dir() else []:
            if rec.with_name(rec.name.removesuffix(".run.json") + ".md").is_file():
                data = read_run(rec)
                view.running[data["item"]] = data
        ddir = cfg.path("done", repo)
        if ddir.is_dir():
            view.done |= {f"{repo}/{p.stem}" for p in ddir.glob("*.md")}
    for ev in log.query(kinds=["finish"]):
        view.finishes[ev.item] = ev
    clears = {ev.repo: ev.id for ev in log.query(kinds=["clear"])}
    for ev in log.query(kinds=["red"]):
        if ev.detail.get("after_red", "stop") == "stop" and ev.id > clears.get(ev.repo, 0):
            view.red_hold[ev.repo] = ev
    for ev in view.finishes.values():
        if ev.detail.get("needs_planner") and ev.id > view.read_mark:
            view.planner_hold.setdefault(ev.repo, ev)
    return view


def decide(cfg: Config, view: View) -> dict[str, Decision]:
    """One decision per configured repo. Items run strictly in NNN order per repo."""
    held_locks: dict[str, str] = {}
    for item_id, rec in view.running.items():
        for lock in rec.get("locks", []):
            held_locks[lock] = item_id
    known = view.known()
    out: dict[str, Decision] = {}
    for repo in cfg.repos:
        running_here = [i for i in view.running if i.startswith(repo + "/")]
        queue = view.queued.get(repo, [])
        head = queue[0] if queue else None
        if running_here:
            out[repo] = Decision(repo, "busy", head, f"running {running_here[0]}")
            continue
        if head is None:
            out[repo] = Decision(repo, "idle")
            continue
        out[repo] = d = _decide_head(cfg, view, repo, head, known, held_locks)
        if d.action == "run":
            for lock in head.locks:
                held_locks[lock] = head.id
    return out


def _decide_head(cfg: Config, view: View, repo: str, head: Item, known: list[str],
                 held_locks: dict[str, str]) -> Decision:
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
        if status in ("queued", "running"):
            return Decision(repo, "wait", head, f"waiting on {dep} ({status})")
        if status != "done":
            return hold(f"dependency {dep} ended {status}")
    for lock in head.locks:
        if lock in held_locks:
            return Decision(repo, "wait", head, f"lock {lock} held by {held_locks[lock]}")
    return Decision(repo, "run", head)
