"""`conductor mcp`: the planner's MCP server (docs/mcp.md), reached by a claude.ai planner
chat linked to this machine through the Claude desktop app.

    conductor mcp [--control <folder> ...]

Every tool that changes something writes the file the planner wrote over the file bridge
(a signal, request, queue item, amendment, answer, parked line or backlog item, `by:
planner`), marked first as the server's (`daemon_receipts.mark`: its events carry `via:
mcp`), waits for its receipt and returns it. A project whose daemon is down is said before
anything is written. Reads are in `planner_read`. The projects are `--control` folders, else
the app's remembered list, else the `*-control` folders beside this repo; their control
folders, their code repos' tracked files (read only) and conductor's own planner files and
reports are reachable. The desktop app runs `mcp_launcher`, which runs this code fresh.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import yaml

from . import config as config_mod
from . import daemon_receipts as receipts
from . import git, items, questions, request, state
from . import planner_read as rd
from .app import actions
from .daemon_receipts import rel_for
from .events import EventLog
from .nightshift import queue_name
from .util import FrontMatterError, read_text, split_front_matter

SERVER_NAME = "conductor"
BY = "planner"
RECEIPT_SECONDS = 30.0      # a receipt lands within seconds with the daemon up (receipts.md, *How long*)
RECEIPT_POLL = 0.2
# Every call answers within this of its arrival: the bridge gives up at 80 s, and an answer
# after that is lost (142: four calls in two projects, nothing said). A write still queued
# behind its project's others with under `CALL_MIN_LEFT` left is refused, nothing written.
CALL_BUDGET = 65.0
CALL_MIN_LEFT = 8.0
RECEIPT_LINES = 4           # event lines a write returns
NEAR = 4                    # lines either side of a missed edit's best match
EDITABLE = ("state.md", "paged.md", "habits.md")    # creatable even before they are tracked
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
READ_TOOLS = ("status", "read")     # run at once; every other tool is a write, in its project's lane
READERS = 8
_counter = iter(range(1, 10**9))
_counter_lock = threading.Lock()


def _next() -> int:
    with _counter_lock:
        return next(_counter)

P = {"type": "string", "description": "The project (its conductor.toml `project`)."}
TOOLS = [
    {"name": "status", "description": (
        "Conductor's live state, read-only. With `project`: daemon (alive, heartbeat, last event), per repo the "
        "running item (elapsed, latest progress, outline), staged, queue, red holds; proposed; open questions. "
        "Without: one line per project."),
     "inputSchema": {"type": "object", "properties": {"project": P}}},
    {"name": "start_session", "description": (
        "Start a planner session: the `session: start` signal, then planner.md (the rules) and session.md "
        "(the digest) in one return. The first call of every session."),
     "inputSchema": {"type": "object", "properties": {"project": P, "label": {"type": "string"}}, "required": ["project"]}},
    {"name": "read", "description": (
        "Read by name, capped ~8 KB a part (`part` for the next). what: state | paged | habits | inbox | review | "
        "session | remote-diag (control files; `section` = a ## heading) | report (name = NNN or repo/NNN_name: "
        "header and section list first, then `section`, or \"all\") | audit | question (id) | picks (picker name) | "
        "receipt (a write's path; no name: the server's writes of the last 2 h, for a call that never answered) | stats (`weeks`, default 4: items per week and model, spend by kind, "
        "corrections, planner writes per session by route) | repo (name = <repo>/<path>: a tracked text file of "
        "a code repo, read-only; a folder lists its names; images stay on the file bridge) | planner | vocab | "
        "bridge | new-project | planner-notes | planner-ideas | ledger (conductor prompts run and waiting) | doc "
        "(name = a conductor docs/ topic) | conductor-report (name = NNN or a name: header first, as report; "
        "none: what is past the planner's mark)."),
     "inputSchema": {"type": "object", "properties": {
         "project": P, "what": {"type": "string"}, "name": {"type": "string"},
         "section": {"type": "string"}, "part": {"type": "integer"}, "weeks": {"type": "integer"}},
        "required": ["what"]}},
    {"name": "queue_item", "description": (
        "Write a queue item (or, `held`, a proposed one). `name`: a slug (numbered next) or NNN_slug (an existing "
        "one is rewritten). `meta`: front-matter beyond repo (template, max_hours, alongside, …). Returns the "
        "receipt: the id taken and when it starts, or what a rewrite changed."),
     "inputSchema": {"type": "object", "properties": {
         "project": P, "repo": {"type": "string"}, "name": {"type": "string"}, "body": {"type": "string"},
         "meta": {"type": "object"}, "held": {"type": "boolean"}}, "required": ["project", "repo", "name", "body"]}},
    {"name": "amend", "description": "Add to a queued, staged or running item's instructions. Returns the receipt.",
     "inputSchema": {"type": "object", "properties": {
         "project": P, "item": {"type": "string", "description": "repo/NNN_name, NNN_name or NNN"},
         "text": {"type": "string"}}, "required": ["project", "item", "text"]}},
    {"name": "signal", "description": (
        "Write one signal file (`by: planner` is added): `lines` are the verb lines in order, as planner.md's "
        "vocab names them (`reviewed: R/NNN_x`, `cancel: …`, `tell: …`). Returns the receipt; a refusal ends "
        "with the accepted form."),
     "inputSchema": {"type": "object", "properties": {
         "project": P, "lines": {"type": "array", "items": {"type": "string"}}}, "required": ["project", "lines"]}},
    {"name": "edit_state", "description": (
        "Change a tracked control file (state.md, paged.md, habits.md, conductor.toml, …) by request, one of: "
        "`edits`, old/new pairs of whole lines checked against the file now (a miss returns the lines near it, "
        "nothing written); `sections`, in order, {op: delete | move | replace, section: a ## heading, with or without `## `, to: the "
        "file a move appends it to (a new .md is created), body: a replace's text under the heading}: text "
        "moves without retyping it, and a miss names the sections there are; or `content`, the whole file. "
        "Returns the receipt."),
     "inputSchema": {"type": "object", "properties": {
         "project": P, "file": {"type": "string"},
         "edits": {"type": "array", "items": {"type": "object", "properties": {
             "old": {"type": "string"}, "new": {"type": "string"}}, "required": ["old", "new"]}},
         "sections": {"type": "array", "items": {"type": "object", "properties": {
             "op": {"type": "string", "enum": ["delete", "move", "replace"]}, "section": {"type": "string"},
             "to": {"type": "string"}, "body": {"type": "string"}}, "required": ["op", "section"]}},
         "content": {"type": "string"}, "message": {"type": "string"}}, "required": ["project", "file"]}},
    {"name": "answer", "description": "Answer an open question (its id or a suffix). Returns the receipt.",
     "inputSchema": {"type": "object", "properties": {
         "project": P, "question": {"type": "string"}, "text": {"type": "string"}},
         "required": ["project", "question", "text"]}},
    {"name": "park", "description": "Add a parked edit line for a repo (`[rules]` first for a rules edit). Returns the receipt.",
     "inputSchema": {"type": "object", "properties": {
         "project": P, "repo": {"type": "string"}, "line": {"type": "string"}}, "required": ["project", "repo", "line"]}},
    {"name": "note", "description": (
        "The rarer writes. kind: backlog (project, repo, name, body, meta: a pre-approved item) | conductor_prompt "
        "(name = slug, body: into conductor's prompts/inbox) | prompt_support (name = file name, body: "
        "prompts/support) | planner_note (text: a *For HANDOFF Open* line) | reviewed_mark (text: the last "
        "conductor report reviewed) | planner_idea (text: a planner-ideas.md line)."),
     "inputSchema": {"type": "object", "properties": {
         "kind": {"type": "string"}, "project": P, "repo": {"type": "string"}, "name": {"type": "string"},
         "body": {"type": "string"}, "text": {"type": "string"}, "meta": {"type": "object"}}, "required": ["kind"]}},
]
TOOL_NAMES = [t["name"] for t in TOOLS]


class Refused(Exception):
    """A write not made: the text says why (and the accepted form where there is one)."""


# ----------------------------------------------------------------- files
def write_file(path: Path, text: str) -> int:
    """`text` with LF endings, as `.tmp` then renamed (the daemon skips `.tmp`); its mtime in ms."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text.replace("\r\n", "\n"))
    os.replace(tmp, path)
    return path.stat().st_mtime_ns // 1_000_000


def item_text(meta: dict, body: str) -> str:
    head = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, default_flow_style=False, width=1000).strip()
    return f"---\n{head}\n---\n{body.strip()}\n"


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


# ----------------------------------------------------------------- receipts
def receipt(cfg: config_mod.Config, write_rel: str, since_ms: int | None = None,
            timeout: float = RECEIPT_SECONDS) -> str:
    """Wait for the receipt of `write_rel` (one at least `since_ms` new, for a rewritable file)
    and say what it says, lean; a `pending` one is waited out to `timeout`."""
    path = cfg.control / rel_for(write_rel)
    end, last = time.monotonic() + timeout, None
    while True:
        try:
            meta, body = split_front_matter(read_text(path))
        except (OSError, UnicodeDecodeError, FrontMatterError):
            meta, body = None, ""
        if meta and (since_ms is None or (meta.get("written_ms") or 0) >= since_ms):
            last = (meta, body)
            if meta.get("outcome") != "pending":
                break
        if time.monotonic() >= end:
            break
        time.sleep(RECEIPT_POLL)
    if last is None:
        return (f"wrote {write_rel}; no receipt after {timeout:.0f}s: the daemon may be busy launching, or "
                f"not taking writes (`status`). Later: read(what=\"receipt\", name=\"{write_rel}\").")
    return say_receipt(*last)


def say_receipt(meta: dict, body: str) -> str:
    outcome = meta.get("outcome")
    if outcome == "refused":
        out = f"REFUSED: {meta.get('reason')}"
        if meta.get("applied"):
            out += f"\napplied before it: {meta['applied']}"
    elif outcome == "pending":
        out = f"pending ({meta.get('write')}): {meta.get('result')}"
    else:
        out = f"accepted: {meta.get('result')}"
    if meta.get("warning"):
        out += f"\nWARNING: {meta['warning']}"
    lines = [ln for ln in body.splitlines() if ln.startswith("- ")]
    return out + "".join(f"\n{ln}" for ln in lines[:RECEIPT_LINES]) + (
        f"\n- … {len(lines) - RECEIPT_LINES} more" if len(lines) > RECEIPT_LINES else "")


# ----------------------------------------------------------------- the server
class PlannerServer:
    """Calls run as they arrive (141): reads at once, on a pool; the writes for one project
    one after another, in the order they came (a lane each); `initialize`, `tools/list` and
    `ping` inline. Every reply goes out whole, a line each, under one lock."""

    def __init__(self, controls: list[Path], out=sys.stdout, receipt_seconds: float = RECEIPT_SECONDS):
        self.controls, self.out = controls, out
        self.receipt_seconds = receipt_seconds
        self.out_lock = threading.Lock()
        self.pool = ThreadPoolExecutor(READERS, thread_name_prefix="mcp-read")
        self.lanes: dict[str, ThreadPoolExecutor] = {}
        self.lanes_lock = threading.Lock()
        self.call_budget = CALL_BUDGET
        self.local = threading.local()      # this thread's call: `deadline` (monotonic)

    def left(self) -> float:
        """Seconds the current call has before its answer is due (`CALL_BUDGET`)."""
        return getattr(self.local, "deadline", float("inf")) - time.monotonic()

    def send(self, obj: dict) -> None:
        with self.out_lock:
            self.out.write(json.dumps(obj) + "\n")
            self.out.flush()

    def lane(self, msg: dict) -> ThreadPoolExecutor | None:
        """Where a call runs: None inline, the read pool, or its project's write lane."""
        if msg.get("method") != "tools/call":
            return None
        params = msg.get("params") or {}
        args = params.get("arguments") or {}
        if params.get("name") in READ_TOOLS:
            return self.pool
        name = ("conductor" if params.get("name") == "note" and args.get("kind") != "backlog"
                else str(args.get("project") or "").strip().lower())
        with self.lanes_lock:
            if name not in self.lanes:
                self.lanes[name] = ThreadPoolExecutor(1, thread_name_prefix=f"mcp-write-{name or 'default'}")
            return self.lanes[name]

    def submit(self, msg: dict) -> None:
        """`handle` now or on its lane; a reply is sent either way."""
        msg.setdefault("_arrived", time.monotonic())
        where = self.lane(msg)
        if where is None:
            self.handle(msg)
            return
        where.submit(self._guarded, msg)

    def _guarded(self, msg: dict) -> None:
        try:
            self.handle(msg)
        except BaseException as exc:            # `handle` answers its own failures; this is the last net
            if msg.get("id") is not None:
                self.error(msg["id"], -32603, f"conductor's MCP worker failed: {type(exc).__name__}: {exc}")

    def drain(self) -> None:
        """Every call taken finished and answered (at stdin's end)."""
        self.pool.shutdown(wait=True)
        with self.lanes_lock:
            lanes = list(self.lanes.values())
        for lane in lanes:
            lane.shutdown(wait=True)

    def error(self, mid, code: int, message: str) -> None:
        self.send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})

    def handle(self, msg: dict) -> None:
        method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                      "capabilities": {"tools": {}}, "serverInfo": {"name": SERVER_NAME, "version": "2"}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name = params.get("name")
            if name not in TOOL_NAMES:
                self.error(mid, -32602, f"unknown tool {name!r}")
                return
            self.local.deadline = msg.get("_arrived", time.monotonic()) + self.call_budget
            try:
                if name not in READ_TOOLS and (left := self.left()) < CALL_MIN_LEFT:
                    raise Refused(f"not written: this call waited {self.call_budget - left:.0f}s behind the "
                                  f"project's other writes, too long to answer before the bridge gives up. "
                                  f"Nothing was written; send it again (recent writes: read(what=\"receipt\")).")
                text, is_error = self.call(name, params.get("arguments") or {}), False
            except Refused as exc:
                text, is_error = str(exc), True
            except Exception as exc:            # never leave a call hanging on a bug
                text, is_error = f"{name} failed: {type(exc).__name__}: {exc}", True
            result = {"content": [{"type": "text", "text": text}], "isError": is_error}
        elif method == "ping":
            result = {}
        elif mid is not None:
            self.error(mid, -32601, f"method not found: {method}")
            return
        else:
            return                              # notifications need no reply
        self.send({"jsonrpc": "2.0", "id": mid, "result": result})

    # ------------------------------------------------------------- helpers
    def project(self, args: dict, live: bool = True) -> config_mod.Config:
        """The named project; with `live`, refused at once when its daemon takes no writes."""
        cfgs, _ = rd.configs(self.controls)
        name = args.get("project") or ""
        if not name and len(cfgs) == 1:
            return cfgs[0]
        cfg = rd.find_project(cfgs, name)
        if cfg is None:
            raise Refused(rd.unknown(name, cfgs))
        if live and (why := rd.down(cfg)):
            raise Refused(f"{why} Nothing was written.")
        return cfg

    def repo(self, cfg: config_mod.Config, repo: str) -> str:
        if repo not in cfg.repos:
            raise Refused(f"no repo {repo!r} in {cfg.project}; repos: {', '.join(cfg.repos)}")
        return repo

    def view(self, cfg: config_mod.Config) -> state.View:
        log = EventLog(cfg.events_db)
        try:
            return state.scan(cfg, log)
        finally:
            log.close()

    def wait(self, cfg: config_mod.Config, rel: str, since_ms: int | None = None) -> str:
        """The receipt, waited for no longer than the call's budget leaves (`CALL_BUDGET`)."""
        got = receipt(cfg, rel, since_ms, max(1.0, min(self.receipt_seconds, self.left() - 3)))
        log = EventLog(cfg.events_db)
        try:                                # 142: another chat's writes show in the count
            head = rd.session_writes(log)
        finally:
            log.close()
        return got + (f"\n({head[0].split(' (')[0]}; `status` names the newest)" if head else "")

    @staticmethod
    def mark(cfg: config_mod.Config, path: Path) -> None:
        """Mark the write about to land at `path` as the server's (`daemon_receipts.mark`)."""
        receipts.mark(cfg.control, cfg.rel(path))

    def marker(self, cfg: config_mod.Config):
        """`before=` for `actions.write_signal` / `actions.amend`: the mark, just before the rename."""
        return lambda path: self.mark(cfg, path)

    # ------------------------------------------------------------- dispatch
    def call(self, name: str, a: dict) -> str:
        if name == "status":
            return rd.status(self.controls, a.get("project"))
        if name == "read":
            what = (a.get("what") or "").lower()
            cfg = None if what in rd.CONDUCTOR_KINDS else self.project(a, live=False)
            text, err = rd.read(cfg, what, a.get("name") or "", a.get("section") or "", int(a.get("part") or 1),
                                int(a["weeks"]) if a.get("weeks") else None)
            if err:
                raise Refused(text)
            return text
        if name == "note" and a.get("kind") in ("conductor_prompt", "prompt_support", "planner_note",
                                                "reviewed_mark", "planner_idea"):
            return self.conductor_note(a)
        return getattr(self, name)(self.project(a), a)

    # ------------------------------------------------------------- tools
    def start_session(self, cfg: config_mod.Config, a: dict) -> str:
        label = " ".join((a.get("label") or "start").split())
        path = actions.write_signal(cfg.control, f"session: {label}", by=BY, before=self.marker(cfg))
        rel = cfg.rel(path)
        got = self.wait(cfg, rel)
        if got.startswith("REFUSED"):
            return got
        digest = cfg.control / "session.md"
        end = time.monotonic() + max(1.0, min(self.receipt_seconds, self.left() - 3))
        while time.monotonic() < end:           # its header names the signal it was written for
            if rel in (rd._text(digest) or "")[:2000]:
                break
            time.sleep(RECEIPT_POLL)
        rules = rd._text(rd.ROOT / rd.PLANNER_FILES["planner"]) or "planner.md doesn't read: read(what=\"planner\")"
        text, _ = rd.read(cfg, "session")       # the rules come whole; the digest pages at the cap
        return f"{rules}\n\n=====\n\n{text}"

    def queue_item(self, cfg: config_mod.Config, a: dict) -> str:
        repo = self.repo(cfg, a.get("repo") or "")
        name = (a.get("name") or "").strip().removesuffix(".md")
        if not SLUG_RE.match(name):
            raise Refused(f"name {name!r}: a slug (`fix_menu`) or NNN_slug, letters, digits, _ . -")
        held = bool(a.get("held"))
        meta = {"repo": repo, **{k: v for k, v in (a.get("meta") or {}).items() if k != "repo"}}
        folder = cfg.path("proposed") if held else cfg.path("queue", repo)
        if not items.NAME_RE.match(name):
            existing = [p.stem for p in [*items.item_files(cfg.path("queue", repo)), *items.item_files(cfg.path("proposed"))]
                        if p.stem.split("_", 1)[-1] == name]
            name = existing[0] if len(existing) == 1 else queue_name(cfg, self.view(cfg), repo, name)
        path = folder / f"{name}.md"
        other = (cfg.path("queue", repo) if held else cfg.path("proposed")) / f"{name}.md"
        if other.is_file():
            raise Refused(f"{name} is {'queued' if held else 'proposed'} already: rewrite it there "
                          f"(held={str(not held).lower()}), or signal `approve:` / `cancel:`")
        self.mark(cfg, path)
        ms = write_file(path, item_text(meta, a.get("body") or ""))
        return self.wait(cfg, cfg.rel(path), ms)

    def amend(self, cfg: config_mod.Config, a: dict) -> str:
        view = self.view(cfg)
        live = sorted({*view.running, *view.staged, *(i.id for q in view.queued.values() for i in q)})
        ref = a.get("item") or ""
        hits = sorted({h for r in cfg.repos for h in items.ref_matches(ref, r, live)})
        if len(hits) != 1:
            raise Refused(f"{ref!r} is ambiguous: {', '.join(hits)}" if hits else
                          f"no queued, staged or running item {ref!r}; a correction to finished work is a new item")
        try:
            path = actions.amend(cfg.control, hits[0], a.get("text") or "", by=BY, before=self.marker(cfg))
        except ValueError as exc:
            raise Refused(str(exc)) from None
        return self.wait(cfg, cfg.rel(path))

    def signal(self, cfg: config_mod.Config, a: dict) -> str:
        lines = a.get("lines") or []
        lines = [lines] if isinstance(lines, str) else lines
        lines = [ln.strip() for chunk in lines for ln in str(chunk).splitlines() if ln.strip()]
        lines = [ln for ln in lines if not ln.lower().startswith("by:")]
        if not lines:
            raise Refused("no signal lines")
        path = actions.write_signal(cfg.control, *lines, by=BY, before=self.marker(cfg))
        return self.wait(cfg, cfg.rel(path))

    def answer(self, cfg: config_mod.Config, a: dict) -> str:
        q = questions.find(cfg.control, a.get("question") or "")
        if q is None:
            open_qs = [x.id for x in questions.scan(cfg.control) if x.is_open]
            raise Refused(f"no question {a.get('question')!r}; open: {', '.join(open_qs) or 'none'}")
        if not (a.get("text") or "").strip():
            raise Refused("the answer is empty")
        self.mark(cfg, q.answer_path)
        questions.write_answer(q, a["text"], by=BY)
        return self.wait(cfg, cfg.rel(q.answer_path))

    def park(self, cfg: config_mod.Config, a: dict) -> str:
        repo = self.repo(cfg, a.get("repo") or "")
        line = " ".join((a.get("line") or "").split()).removeprefix("- ")
        if not line:
            raise Refused("the parked line is empty")
        path = cfg.path("parked", f"{repo}.md")
        old = rd._text(path) or ""
        self.mark(cfg, path)
        ms = write_file(path, old + ("" if not old or old.endswith("\n") else "\n") + f"- {line}\n")
        return self.wait(cfg, cfg.rel(path), ms)

    def note(self, cfg: config_mod.Config, a: dict) -> str:
        if a.get("kind") != "backlog":
            raise Refused(f"unknown kind {a.get('kind')!r}: backlog, conductor_prompt, prompt_support, "
                          "planner_note, reviewed_mark, planner_idea")
        repo = self.repo(cfg, a.get("repo") or "")
        name = (a.get("name") or "").removesuffix(".md")
        if not SLUG_RE.match(name):
            raise Refused(f"name {name!r}: a slug")
        meta = {"repo": repo, **{k: v for k, v in (a.get("meta") or {}).items() if k != "repo"}}
        path = cfg.path("backlog", repo, f"{name}.md")
        self.mark(cfg, path)
        ms = write_file(path, item_text(meta, a.get("body") or ""))
        return self.wait(cfg, cfg.rel(path), ms)

    def tracked_file(self, cfg: config_mod.Config, rel: str, tracked: set[str], new_ok: bool = False) -> str:
        """`rel` checked: a tracked control file (or one of `EDITABLE`); with `new_ok`, also a
        new `.md` file that doesn't exist yet (a move's destination)."""
        rel = (rel or "").replace("\\", "/").strip("/")
        fresh = new_ok and rel.endswith(".md") and not (cfg.control / rel).exists()
        try:
            request._check_target(rel)
        except request.RequestError as exc:
            raise Refused(str(exc)) from None
        if rel not in tracked and rel not in EDITABLE and not fresh:
            raise Refused(f"{rel!r} is not a tracked control file"
                          + (" (nor a new .md file)" if new_ok else "") + "; tracked: "
                          + ", ".join(sorted(t for t in tracked if t.endswith((".md", ".toml")))[:20]))
        return rel

    def edit_state(self, cfg: config_mod.Config, a: dict) -> str:
        tracked = set(git.ls_files(cfg.control))
        rel = self.tracked_file(cfg, a.get("file") or "", tracked)
        edits, content, ops = a.get("edits") or [], a.get("content"), a.get("sections") or []
        if sum((bool(edits), content is not None, bool(ops))) != 1:
            raise Refused("give one of `edits` (old/new pairs), `sections` (section operations) or `content` "
                          "(the whole file)")
        current = rd._text(cfg.control / rel)
        pairs: dict[str, list[tuple[str, str]]] = {}
        whole: dict[str, str] = {}
        if ops:
            if current is None:
                raise Refused(f"{rel} doesn't exist yet: nothing to take sections from")
            pairs, whole, said = self.section_ops(cfg, rel, current, ops, tracked)
            default = f"{Path(rel).stem}: {said}"
        elif edits:
            if current is None:
                raise Refused(f"{rel} doesn't exist yet: send `content` to create it")
            pairs[rel] = [(_lines(e.get("old") or ""), _lines(e.get("new") or "")) for e in edits]
            try:
                request.apply_edits(current, rel, pairs[rel])
            except request.RequestError as exc:
                n = int(m.group(1)) if (m := re.search(r"edit (\d+)", str(exc))) else 1
                raise Refused(f"{exc}\nNothing was written. The lines of {rel} nearest edit {n}'s old text, "
                              f"now:\n{near(_progress(current, pairs[rel][:n - 1]), pairs[rel][n - 1][0])}") from None
            default = f"{Path(rel).stem}: planner edit"
        else:
            whole[rel] = content if content.endswith("\n") else content + "\n"
            default = f"{Path(rel).stem}: planner edit"
        if all(request.apply_edits(rd._text(cfg.control / t) or "", t, p) == rd._text(cfg.control / t)
               for t, p in pairs.items()) and all(rd._text(cfg.control / t) == text for t, text in whole.items()):
            return f"no change: {', '.join([*pairs, *whole])} already read(s) that way; nothing written"
        return self.send_request(cfg, pairs, whole, " ".join((a.get("message") or default).split()))

    def section_ops(self, cfg: config_mod.Config, rel: str, text: str, ops: list, tracked: set[str]):
        """`sections` of `edit_state`, in order: {op: delete | move | replace, section: a `## `
        heading's start, to: the file a move appends it to (created if missing), body: a
        replace's new text under the heading}. Returns (edit pairs per file, whole files,
        what was done). A section not found names those that exist; nothing is written."""
        texts = {rel: text}
        pairs: dict[str, list[tuple[str, str]]] = {rel: []}
        whole: dict[str, str] = {}
        said = []
        for n, op in enumerate(ops, 1):
            kind, name = str(op.get("op") or "").lower(), str(op.get("section") or "")
            if kind not in ("delete", "move", "replace"):
                raise Refused(f"section op {n}: `op` is delete, move or replace, not {kind!r}")
            old, head = find_section(texts[rel], name, rel, n)
            if kind == "delete":
                new = ""
                said.append(f"drop *{head}*")
            elif kind == "replace":
                body = (op.get("body") or "").replace("\r\n", "\n").strip("\n")
                new = f"## {head}\n\n{body}\n\n" if body else f"## {head}\n\n"
                said.append(f"rewrite *{head}*")
            else:
                to = self.tracked_file(cfg, op.get("to") or "", tracked, new_ok=True)
                if to == rel:
                    raise Refused(f"section op {n}: a move needs another file than {rel}")
                if to not in texts:
                    texts[to] = rd._text(cfg.control / to) or ""
                base = texts[to].rstrip("\n")
                texts[to] = whole[to] = (base + "\n\n" if base else "") + old.rstrip("\n") + "\n"
                new = ""
                said.append(f"move *{head}* to {to}")
            pairs[rel].append((old, new))
            texts[rel] = texts[rel].replace(old, new, 1)
        try:                                    # as edit blocks only if they apply the same way
            ok = (all(o.endswith("\n") for p in pairs[rel] for o in p if o)
                  and request.apply_edits(text, rel, pairs[rel]) == texts[rel])
        except request.RequestError:
            ok = False
        if not ok:
            whole[rel], pairs = texts[rel], {}
        return pairs, whole, "; ".join(said)

    def send_request(self, cfg: config_mod.Config, pairs: dict[str, list[tuple[str, str]]],
                     whole: dict[str, str], message: str) -> str:
        """One request (`request.py`): edit blocks for `pairs` (applied by the daemon against the
        live copy), whole files for `whole` (written before `request.md`); marked, waited for."""
        if any(_marker(t) for ps in pairs.values() for p in ps for t in p):
            for t, ps in pairs.items():         # a conflict marker in the text: those go whole
                whole[t] = request.apply_edits(rd._text(cfg.control / t) or "", t, ps)
            pairs = {}
        d = cfg.path("requests", f"{datetime.now():%Y%m%d-%H%M}-mcp{datetime.now():%S}{_next():03d}")
        self.mark(cfg, d)
        for t, text in whole.items():           # whole files first, request.md last
            write_file(d / t, text)
        blocks = "".join(f"<<<<<<< {t}\n{old}=======\n{new}>>>>>>>\n" for t, ps in pairs.items() for old, new in ps)
        head = yaml.safe_dump({"targets": [*pairs, *whole], "message": message}, sort_keys=False, allow_unicode=True)
        write_file(d / "request.md", f"---\n{head}---\n{blocks}")
        return self.wait(cfg, cfg.rel(d))

    def conductor_note(self, a: dict) -> str:
        kind = a["kind"]
        if kind in ("conductor_prompt", "prompt_support"):
            name = (a.get("name") or "").removesuffix(".md")
            body = a.get("body") or ""
            if not SLUG_RE.match(name) or not body.strip():
                raise Refused("give `name` (a slug) and `body`")
            if kind == "prompt_support":
                path = rd.ROOT / "prompts" / "support" / (a.get("name") or "")
                if "/" in a.get("name", "") or "\\" in a.get("name", ""):
                    raise Refused("a support file's name is a bare file name")
            else:
                project = (a.get("project") or "conductor").strip()
                if not re.match(r"^[A-Za-z0-9_]+$", project):
                    raise Refused(f"project {project!r}: letters, digits and _ only (it is part of the file name)")
                path = rd.ROOT / "prompts" / "inbox" / f"{stamp()}-{project}-{name}.md"
            write_file(path, body.rstrip() + "\n")
            return f"wrote {path.relative_to(rd.ROOT).as_posix()} (no receipt: Matt runs it with \"run next prompt\")"
        text = " ".join((a.get("text") or "").split()).removeprefix("- ")
        if not text:
            raise Refused("give `text`")
        if kind == "reviewed_mark":
            path = rd.ROOT / "planner-notes.md"
            old = rd._text(path) or ""
            line = f"Last report reviewed by the planner: {text}"
            new = re.sub(r"^Last report reviewed by the planner: .*$", line, old, count=1, flags=re.M)
            write_file(path, new if new != old or line in old else old.rstrip("\n") + f"\n\n{line}\n")
            return f"planner-notes.md: {line}"
        path, heading = ((rd.ROOT / "planner-notes.md", "## For HANDOFF Open") if kind == "planner_note"
                         else (rd.ROOT / "planner-ideas.md", "## Waiting on evidence"))
        write_file(path, insert_under(rd._text(path) or "", heading, f"- {text}"))
        return f"{path.name}: added under {heading.removeprefix('## ')}"


# ----------------------------------------------------------------- edit helpers
def find_section(text: str, name: str, rel: str, n: int) -> tuple[str, str]:
    """The `## ` section of `text` whose heading is `name` (else starts with it; case and `*`
    ignored), as (its text from the heading to the next `## `, its heading); refused naming
    the sections there are when none or several match."""
    want = name.strip().lstrip("#").strip().strip("*").strip().lower()    # `## Paged` or `Paged` (142)
    lines = text.splitlines(keepends=True)
    found = rd.sections(text)
    heads = [h for h, _, _ in found]
    exact = [s for s in found if s[0].strip("*").strip().lower() == want]
    hits = exact or [s for s in found if want and s[0].strip("*").strip().lower().startswith(want)]
    if len(hits) != 1:
        why = f"{len(hits)} sections start {name!r}" if hits else f"no `## ` section {name!r}"
        raise Refused(f"section op {n}: {why} in {rel}; nothing was written. Its sections: "
                      + ("; ".join(heads) or "none"))
    head, a, b = hits[0]
    return "".join(lines[a:b]), head


def _lines(text: str) -> str:
    """Whole lines, as the request's edit blocks take them: an empty side stays empty."""
    text = text.replace("\r\n", "\n")
    return text if not text or text.endswith("\n") else text + "\n"


def _marker(text: str) -> bool:
    return any(ln.startswith(("<<<<<<< ", request.EDIT_CLOSE)) or ln == request.EDIT_MID for ln in text.splitlines())


def _progress(text: str, pairs: list[tuple[str, str]]) -> str:
    """`text` after the edits before the one that missed (they apply in order)."""
    for old, new in pairs:
        text = text.replace(old, new, 1)
    return text


def near(text: str, old: str) -> str:
    """The file's lines around the line most like `old`'s first line, numbered."""
    lines = text.splitlines()
    first = next((ln for ln in old.splitlines() if ln.strip()), "")
    if not lines:
        return "(the file is empty)"
    best = max(range(len(lines)), key=lambda i: SequenceMatcher(None, lines[i], first).ratio())
    lo, hi = max(0, best - NEAR), min(len(lines), best + NEAR + 1 + max(0, len(old.splitlines()) - 1))
    return "\n".join(f"{i + 1:>4}| {lines[i]}" for i in range(lo, hi))


def insert_under(text: str, heading: str, line: str) -> str:
    """`line` at the end of `heading`'s section (the heading added at the end when missing)."""
    lines = text.splitlines()
    if heading not in lines:
        return text.rstrip("\n") + f"\n\n{heading}\n\n{line}\n"
    i = lines.index(heading) + 1
    end = next((j for j in range(i, len(lines)) if lines[j].startswith("## ")), len(lines))
    while end > i and not lines[end - 1].strip():
        end -= 1
    lines.insert(end, line)
    if end == i:                            # an empty section: a blank line after the heading
        lines.insert(i, "")
    return "\n".join(lines) + "\n"


def serve(server: PlannerServer, stdin=None) -> int:
    """Newline-delimited JSON-RPC from stdin, each call handed to `server.submit`; at stdin's
    end, every call taken is answered before this returns."""
    stdin = stdin or sys.stdin
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            server.error(None, -32700, "parse error")
            continue
        for m in (msg if isinstance(msg, list) else [msg]):
            if isinstance(m, dict):
                server.submit(m)
    server.drain()
    return 0


def private_stdio():
    """The protocol's pipes as private copies, fds 0 and 1 pointed at NUL. On Windows a
    child started on a call's thread (git) inherits the std handles, and doing so blocks
    while another thread reads the stdin pipe: the first call that ran git while the main
    thread waited for the next line never finished (141). A child's stray output can't
    reach the protocol either."""
    sys.stdout.flush()
    fin, fout = os.dup(0), os.dup(1)            # non-inheritable copies
    nul_in, nul_out = os.open(os.devnull, os.O_RDONLY), os.open(os.devnull, os.O_WRONLY)
    os.dup2(nul_in, 0)
    os.dup2(nul_out, 1)
    os.close(nul_in)
    os.close(nul_out)
    if os.name == "nt":                         # the CRT's dup2 doesn't always move the Win32 std handles
        import ctypes
        import msvcrt
        k32 = ctypes.windll.kernel32
        k32.SetStdHandle(-10, msvcrt.get_osfhandle(0))
        k32.SetStdHandle(-11, msvcrt.get_osfhandle(1))
    stdin = open(fin, "r", encoding="utf-8", newline=None)
    stdout = open(fout, "w", encoding="utf-8", newline="\n")
    sys.stdin = open(os.devnull, encoding="utf-8")
    sys.stdout = sys.stderr                     # a stray print can't corrupt the protocol
    return stdin, stdout


def main(controls: list[str] | None = None) -> int:
    stdin, stdout = private_stdio()
    return serve(PlannerServer([Path(c) for c in controls or []], out=stdout), stdin)
