"""`conductor mcp`: the planner's MCP server (docs/mcp.md), reached by a claude.ai planner
chat linked to this machine through the Claude desktop app.

    conductor mcp [--control <folder> ...]

Every tool that changes something writes the file the planner wrote over the file bridge
(a signal, request, queue item, amendment, answer, parked line or backlog item, `by:
planner`), waits for its receipt and returns it: the daemon and the event log are unchanged.
A project whose daemon is down is said before anything is written. Reads are in
`planner_read`. The projects are `--control` folders, else the app's remembered list, else
the `*-control` folders beside this repo; only their control folders and conductor's own
planner files are reachable.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import yaml

from . import config as config_mod
from . import git, items, questions, request, state
from . import planner_read as rd
from .app import actions
from .ask_server import serve_stdio
from .daemon_receipts import rel_for
from .events import EventLog
from .nightshift import queue_name
from .util import FrontMatterError, read_text, split_front_matter

SERVER_NAME = "conductor"
BY = "planner"
RECEIPT_SECONDS = 30.0      # a receipt lands within seconds with the daemon up (receipts.md, *How long*)
RECEIPT_POLL = 0.2
RECEIPT_LINES = 4           # event lines a write returns
NEAR = 4                    # lines either side of a missed edit's best match
EDITABLE = ("state.md", "paged.md", "habits.md")    # creatable even before they are tracked
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_counter = iter(range(1, 10**9))

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
        "receipt (a write's path) | planner | vocab | bridge | new-project | planner-notes | planner-ideas | "
        "ledger (conductor prompts run and waiting) | doc (name = a conductor docs/ topic)."),
     "inputSchema": {"type": "object", "properties": {
         "project": P, "what": {"type": "string"}, "name": {"type": "string"},
         "section": {"type": "string"}, "part": {"type": "integer"}}, "required": ["what"]}},
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
        "Change a tracked control file (state.md, paged.md, habits.md, conductor.toml, …) by request: `edits` "
        "are old/new pairs of whole lines, checked against the file now (a miss returns the lines near it, "
        "nothing written), or `content` replaces the file. Returns the receipt."),
     "inputSchema": {"type": "object", "properties": {
         "project": P, "file": {"type": "string"},
         "edits": {"type": "array", "items": {"type": "object", "properties": {
             "old": {"type": "string"}, "new": {"type": "string"}}, "required": ["old", "new"]}},
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
    def __init__(self, controls: list[Path], out=sys.stdout, receipt_seconds: float = RECEIPT_SECONDS):
        self.controls, self.out = controls, out
        self.receipt_seconds = receipt_seconds

    def send(self, obj: dict) -> None:
        self.out.write(json.dumps(obj) + "\n")
        self.out.flush()

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
            try:
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
        return receipt(cfg, rel, since_ms, self.receipt_seconds)

    # ------------------------------------------------------------- dispatch
    def call(self, name: str, a: dict) -> str:
        if name == "status":
            return rd.status(self.controls, a.get("project"))
        if name == "read":
            what = (a.get("what") or "").lower()
            cfg = None if what in (*rd.PLANNER_FILES, "doc", "ledger") else self.project(a, live=False)
            text, err = rd.read(cfg, what, a.get("name") or "", a.get("section") or "", int(a.get("part") or 1))
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
        path = actions.write_signal(cfg.control, f"session: {label}", by=BY)
        rel = cfg.rel(path)
        got = self.wait(cfg, rel)
        if got.startswith("REFUSED"):
            return got
        digest = cfg.control / "session.md"
        end = time.monotonic() + self.receipt_seconds
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
            path = actions.amend(cfg.control, hits[0], a.get("text") or "", by=BY)
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
        path = actions.write_signal(cfg.control, *lines, by=BY)
        return self.wait(cfg, cfg.rel(path))

    def answer(self, cfg: config_mod.Config, a: dict) -> str:
        q = questions.find(cfg.control, a.get("question") or "")
        if q is None:
            open_qs = [x.id for x in questions.scan(cfg.control) if x.is_open]
            raise Refused(f"no question {a.get('question')!r}; open: {', '.join(open_qs) or 'none'}")
        if not (a.get("text") or "").strip():
            raise Refused("the answer is empty")
        questions.write_answer(q, a["text"], by=BY)
        return self.wait(cfg, cfg.rel(q.answer_path))

    def park(self, cfg: config_mod.Config, a: dict) -> str:
        repo = self.repo(cfg, a.get("repo") or "")
        line = " ".join((a.get("line") or "").split()).removeprefix("- ")
        if not line:
            raise Refused("the parked line is empty")
        path = cfg.path("parked", f"{repo}.md")
        old = rd._text(path) or ""
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
        ms = write_file(path, item_text(meta, a.get("body") or ""))
        return self.wait(cfg, cfg.rel(path), ms)

    def edit_state(self, cfg: config_mod.Config, a: dict) -> str:
        rel = (a.get("file") or "").replace("\\", "/").strip("/")
        tracked = set(git.ls_files(cfg.control))
        if not rel or ".." in rel.split("/") or (rel not in tracked and rel not in EDITABLE):
            raise Refused(f"{rel!r} is not a tracked control file; tracked: "
                          + ", ".join(sorted(t for t in tracked if t.endswith((".md", ".toml")))[:20]))
        edits, content = a.get("edits") or [], a.get("content")
        if bool(edits) == (content is not None):
            raise Refused("give `edits` (old/new pairs) or `content` (the whole file), not both or neither")
        current = rd._text(cfg.control / rel)
        if edits:
            if current is None:
                raise Refused(f"{rel} doesn't exist yet: send `content` to create it")
            pairs = [(_lines(e.get("old") or ""), _lines(e.get("new") or "")) for e in edits]
            try:
                new_text = request.apply_edits(current, rel, pairs)
            except request.RequestError as exc:
                n = int(m.group(1)) if (m := re.search(r"edit (\d+)", str(exc))) else 1
                raise Refused(f"{exc}\nNothing was written. The lines of {rel} nearest edit {n}'s old text, "
                              f"now:\n{near(_progress(current, pairs[:n - 1]), pairs[n - 1][0])}") from None
        else:
            new_text = content if content.endswith("\n") else content + "\n"
        if new_text == current:
            return f"no change: {rel} already reads that way; nothing written"
        message = " ".join((a.get("message") or f"{Path(rel).stem}: planner edit").split())
        d = cfg.path("requests", f"{datetime.now():%Y%m%d-%H%M}-mcp{datetime.now():%S}{next(_counter):03d}")
        blocks = "" if content is not None or any(_marker(t) for p in pairs for t in p) else "".join(
            f"<<<<<<< {rel}\n{old}=======\n{new}>>>>>>>\n" for old, new in pairs)
        if not blocks:                          # whole file: the target first, request.md last
            write_file(d / rel, new_text)
        head = yaml.safe_dump({"targets": [rel], "message": message}, sort_keys=False, allow_unicode=True)
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


def main(controls: list[str] | None = None) -> int:
    return serve_stdio(PlannerServer([Path(c) for c in controls or []]))
