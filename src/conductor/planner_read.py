"""The planner MCP server's reads (docs/mcp.md): `status` and `read`, plain text, capped.

A read returns at most `CAP` bytes of a file, cut at a line; the rest comes as further parts
(`part`). Every path is inside a known project's control folder or is one of conductor's own
planner files: nothing in a code repo.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import config as config_mod
from . import items, questions, session, state
from .events import EventLog
from .inbox import latest_outline, outline_line, progress_line
from .util import clock, duration, now, parse_stamp, read_text

CAP = 8000                  # bytes a read returns per part
STATUS_CHARS = 1500         # `status` lands in the planner's context every call
NOTE_CHARS = 160
QUEUE_SHOWN = 6
ROOT = Path(__file__).resolve().parents[2]
SETTINGS_KEY = "controls"   # app.window.SETTINGS_KEY
# Conductor's own planner files a read may name (`read("doc", name)` also takes any `docs/<name>.md`).
PLANNER_FILES = {"planner": "planner.md", "vocab": "planner-vocab.md", "bridge": "planner-bridge.md",
                 "new-project": "claude-ai-new-project.md", "planner-notes": "planner-notes.md",
                 "planner-ideas": "planner-ideas.md"}
CONTROL_FILES = {"state": "state.md", "paged": "paged.md", "habits": "habits.md", "inbox": "inbox.md",
                 "review": "review.md", "session": "session.md", "remote-diag": "remote-diag.md"}
READ_KINDS = (*CONTROL_FILES, "report", "audit", "question", "picks", "receipt", "stats", *PLANNER_FILES,
              "ledger", "doc")
LEDGER_LINES = 12


# ----------------------------------------------------------------- paging
def parts(text: str, cap: int = CAP) -> list[str]:
    """`text` cut at line ends into pieces of at most `cap` bytes (a longer line is cut)."""
    out, cur, size = [], [], 0
    for ln in text.splitlines(keepends=True):
        b = len(ln.encode("utf-8"))
        while b > cap:                      # one huge line: cut it
            if cur:
                out.append("".join(cur))
                cur, size = [], 0
            head = ln.encode("utf-8")[:cap].decode("utf-8", "ignore")
            out.append(head)
            ln = ln[len(head):]
            b = len(ln.encode("utf-8"))
        if size + b > cap and cur:
            out.append("".join(cur))
            cur, size = [], 0
        cur.append(ln)
        size += b
    if cur or not out:
        out.append("".join(cur))
    return out


def paged(text: str, part: int, again: str, cap: int = CAP) -> str:
    """Part `part` (1-based) of `text`, with a footer naming the call for the next part."""
    pieces = parts(text, cap)
    part = max(1, min(part, len(pieces)))
    body = pieces[part - 1].rstrip("\n")
    if len(pieces) == 1:
        return body
    more = f"; next: {again.format(part=part + 1)}" if part < len(pieces) else "; that was the last"
    return f"{body}\n\n[part {part} of {len(pieces)}{more}]"


# ----------------------------------------------------------------- projects
def remembered() -> list[Path]:
    """The app's remembered projects (QSettings); [] if Qt can't say."""
    try:
        from PySide6.QtCore import QSettings           # QtCore only: app.window would pull in the GUI
        value = QSettings("conductor", "conductor-app").value(SETTINGS_KEY, [])
    except Exception:
        return []
    value = [value] if isinstance(value, str) else value or []
    return [Path(v) for v in value]


def siblings() -> list[Path]:
    return sorted(p for p in ROOT.parent.glob("*-control") if (p / config_mod.CONFIG_NAME).is_file())


def configs(controls: list[Path]) -> tuple[list[config_mod.Config], list[str]]:
    found, bad = [], []
    for c in controls or remembered() or siblings():
        try:
            found.append(config_mod.load(Path(c)))
        except Exception as exc:            # a folder that won't load is named, not fatal
            bad.append(f"{c}: {exc}")
    return found, bad


def find_project(cfgs: list[config_mod.Config], name: str) -> config_mod.Config | None:
    want = (name or "").strip().lower()
    for c in cfgs:
        if want in (c.project.lower(), c.control.name.lower(), c.control.name.lower().removesuffix("-control")):
            return c
    return None


# ----------------------------------------------------------------- status
def ago(ts: str | None) -> str:
    if not ts:
        return "never"
    try:
        secs = (now() - parse_stamp(ts)).total_seconds()
        return f"{secs / 86400:.1f} days ago" if secs >= 2 * 86400 else f"{duration(secs)} ago"
    except ValueError:
        return ts


def down(cfg: config_mod.Config) -> str | None:
    """Why `cfg`'s daemon takes no writes now, with how to start it; None when it is alive."""
    d = state.daemon_info(cfg)
    if d.alive and not d.stale:
        return None
    start = (f"Start it: the app's Start daemon, or `uv run conductor run --detach {cfg.control}` "
             "in C:\\Code\\claude-conductor.")
    if d.alive:
        return f"{cfg.project}'s daemon is NOT ANSWERING (pid {d.pid}, last heartbeat {ago(d.beat)}). {start}"
    how = f"it died (pid {d.pid}, last heartbeat {ago(d.beat)})" if d.died else "it is stopped"
    return f"{cfg.project}'s daemon is DOWN: {how}. {start}"


def daemon_line(cfg: config_mod.Config, log: EventLog) -> str:
    d = state.daemon_info(cfg)
    row = log.conn.execute("SELECT id, ts, kind FROM events ORDER BY id DESC LIMIT 1").fetchone()
    last = f"last event #{row[0]} {row[2]} {ago(row[1])}" if row else "no events yet"
    if d.alive and not d.stale:
        return f"daemon alive (pid {d.pid}, since {clock(d.started)}); heartbeat {ago(d.beat)}; {last}"
    if d.alive:
        return (f"daemon NOT ANSWERING: pid {d.pid} lives but its last heartbeat was {ago(d.beat)}; {last}. "
                "Nothing new launches while it hangs.")
    how = (f"it died (pid {d.pid}, last heartbeat {clock(d.beat) if d.beat else 'never'}, {ago(d.beat)}; never stopped cleanly)"
           if d.died else "stopped")
    return (f"daemon DOWN: {how}; {last}. Nothing launches, finishes or commits until it is started "
            "(the app's Start daemon, or `conductor run <control> --detach`).")


def _cut(text: str, n: int = NOTE_CHARS) -> str:
    return text if len(text) <= n else text[:n - 1] + "…"


def project_status(cfg: config_mod.Config) -> str:
    log = EventLog(cfg.events_db)
    try:
        out = [f"{cfg.project} — {daemon_line(cfg, log)}"]
        view = state.scan(cfg, log)
        decisions = state.decide(cfg, view)
        for repo in cfg.repos:
            out.append(f"{repo}:")
            busy = False
            for item_id, rec in view.running.items():
                if rec["repo"] != repo:
                    continue
                busy = True
                age = duration((now() - parse_stamp(rec["started"])).total_seconds())
                phase = rec.get("phase")
                what = "merging" if phase == "merge" else "setup" if phase == "setup" else "running"
                line = f"  {what} {rec['name']} {age}"
                if what == "running" and rec.get("deadline"):
                    line += f", kill at {clock(rec['deadline'])}"
                out.append(line)
                # The newest line with a note: an outline-only update has none (aicomplete 288).
                if p := log.last("progress", item=item_id, has="note"):
                    out.append(f"    progress {clock(p.ts)}: {_cut(progress_line(p))}")
                if o := latest_outline(log, item_id):
                    out.append(f"    {_cut(outline_line(o))}")
            for rec in view.staged.values():
                if rec["repo"] == repo:
                    busy = True
                    out.append(f"  staged {rec['name']} ({rec.get('phase')})")
            queue = view.queue(repo)
            if queue:
                busy = True
                names = [i.name for i in queue[:QUEUE_SHOWN]]
                more = f" +{len(queue) - QUEUE_SHOWN} more" if len(queue) > QUEUE_SHOWN else ""
                dec = decisions[repo]
                head = f" (next: {dec.action}{': ' + dec.reason if dec.reason else ''})" if dec.action in (
                    "hold", "wait") else ""
                out.append(f"  queued {', '.join(names)}{more}{head}")
            if ev := view.red_hold.get(repo):
                busy = True
                out.append(f"  held after red {ev.item}")
            if not busy:
                out.append("  idle")
        proposed = sorted(cfg.path("proposed").glob("*.md")) if cfg.path("proposed").is_dir() else []
        out.append(f"proposed: {', '.join(p.stem for p in proposed) or 'none'}")
        if open_qs := [q.id for q in questions.scan(cfg.control) if q.is_open]:
            out.append(f"questions open: {', '.join(open_qs)}")
    finally:
        log.close()
    text = "\n".join(out)
    return text if len(text) <= STATUS_CHARS else text[:STATUS_CHARS - 12] + "\n…(cut)"


def one_line(cfg: config_mod.Config) -> str:
    d = state.daemon_info(cfg)
    daemon = ("daemon alive" if d.alive and not d.stale else "daemon NOT ANSWERING" if d.alive
              else "daemon DOWN")
    running = queued = 0
    try:
        for repo in cfg.repos:
            rdir = cfg.path("running", repo)
            running += len(list(rdir.glob("*.run.json"))) if rdir.is_dir() else 0
            queued += len(items.item_files(cfg.path("queue", repo)))
    except OSError:
        pass
    return f"{cfg.project}: {daemon}, {running} running, {queued} queued ({cfg.control})"


def status(controls: list[Path], project: str | None) -> str:
    cfgs, bad = configs(controls)
    if not (project or "").strip():
        lines = [one_line(c) for c in cfgs] + [f"not loaded: {b}" for b in bad]
        return "\n".join(lines) or "no projects known"
    if cfg := find_project(cfgs, project):
        return project_status(cfg)
    return unknown(project, cfgs)


def unknown(project: str, cfgs: list[config_mod.Config]) -> str:
    return f"no project {project!r}; known: {', '.join(c.project for c in cfgs) or 'none'}"


# ----------------------------------------------------------------- read
def sections(text: str, level: str = "## ") -> list[tuple[str, int, int]]:
    """(heading, start line, end line) of each `level` section."""
    lines = text.splitlines()
    heads = [(ln[len(level):].strip(), i) for i, ln in enumerate(lines) if ln.startswith(level)]
    return [(h, i, heads[n + 1][1] if n + 1 < len(heads) else len(lines)) for n, (h, i) in enumerate(heads)]


def section_text(text: str, name: str) -> str | None:
    """The `## ` (else `### `) section whose heading starts with `name` (case and `*` ignored)."""
    want = name.strip().strip("*").lower()
    lines = text.splitlines()
    for level in ("## ", "### "):
        for head, a, b in sections(text, level):
            if head.strip("*").lower().startswith(want):
                return "\n".join(lines[a:b]).rstrip()
    return None


def outline(text: str) -> str:
    """A report's header and its section list: what a first read of a report returns."""
    head, body = "", text
    if text.startswith("---\n") and (end := text.find("\n---\n", 4)) != -1:
        head, body = text[:end + 5], text[end + 5:]
    else:                                   # a report opens with `status:` and header lines
        lines = text.splitlines()
        n = next((i for i, ln in enumerate(lines) if not re.match(r"^[a-z_]+:", ln)), len(lines))
        head, body = "\n".join(lines[:n]) + "\n", "\n".join(lines[n:])
    heads = [f"- {h} ({len(chr(10).join(body.splitlines()[a:b]).encode('utf-8'))} bytes)"
             for h, a, b in sections(body)]
    title = next((ln for ln in body.splitlines() if ln.startswith("# ")), "")
    return (head.rstrip() + ("\n" + title if title else "") + "\n\nSections:\n" + ("\n".join(heads) or "- (none)")
            + f"\n\nWhole: {len(text.encode('utf-8'))} bytes. Read one with `section`, or `section: \"all\"`.")


def _report(cfg: config_mod.Config, ref: str, suffix: str) -> Path | str:
    """A report (or its audit notes) by `repo/NNN_name`, `NNN_name` or `NNN`."""
    reports = cfg.path("reports")
    names = [p.name[:-len(suffix)].replace("__", "/", 1) for p in reports.glob(f"*__*{suffix}")
             if p.name.endswith(suffix) and (suffix != ".md" or not re.search(r"\.[a-z]+\.md$", p.name))]
    repos = [ref.split("/", 1)[0]] if "/" in ref else list(cfg.repos)
    hits = sorted({h for r in repos for h in items.ref_matches(ref, r, names)})
    if len(hits) != 1:
        return (f"no report {ref!r}" if not hits else f"{ref!r} names several: {', '.join(hits)}")
    repo, name = hits[0].split("/", 1)
    return reports / f"{repo}__{name}{suffix}"


def read(cfg: config_mod.Config | None, what: str, name: str = "", section: str = "", part: int = 1,
         weeks: int | None = None) -> tuple[str, bool]:
    """(text, is_error) for `read`. `cfg` is None only for conductor's own files."""
    what = (what or "").strip().lower()
    again = f'read(what="{what}"' + (f', name="{name}"' if name else "") + (
        f', section="{section}"' if section else "") + ", part={part})"
    if what == "ledger":                    # read before writing a conductor prompt: what ran, what waits
        tail = (_text(ROOT / "prompts" / "ledger.md") or "").splitlines()[-LEDGER_LINES:]
        waiting = sorted(p.name for p in (ROOT / "prompts" / "inbox").glob("*.md"))
        return "\n".join(tail) + f"\n\nwaiting in prompts/inbox: {', '.join(waiting) or 'none'}", False
    if what in PLANNER_FILES or what == "doc":
        rel = PLANNER_FILES.get(what) or f"docs/{Path(name).stem}.md"
        path = ROOT / rel
        if what == "doc" and (not name or "/" in name or "\\" in name or not path.is_file()):
            docs = ", ".join(sorted(p.stem for p in (ROOT / "docs").glob("*.md")))
            return f"no conductor doc {name!r}; docs: {docs}", True
        return _file(path, section, part, again)
    if cfg is None:
        return "name a `project` for that read", True
    if what in CONTROL_FILES:
        path = cfg.control / CONTROL_FILES[what]
        if what in ("paged", "habits") and not path.is_file():
            body = session.state_sections(_text(cfg.control / "state.md") or "").get(
                "planner habits" if what == "habits" else "paged")
            if body is None:
                return f"no {path.name}, and `state.md` has no *{'Planner habits' if what == 'habits' else 'Paged'}*", True
            return paged("\n".join(body).strip("\n") + f"\n\n(from state.md: {path.name} doesn't exist yet)", part, again), False
        return _file(path, section, part, again)
    if what == "stats":                     # `conductor stats` (docs/stats.md); `name` may carry the weeks
        from . import stats
        if weeks is None and name.strip().isdigit():
            weeks = int(name)
        log = EventLog(cfg.events_db)
        try:
            return paged(stats.report(log, cfg.project, weeks or stats.WEEKS), part, again), False
        finally:
            log.close()
    if what in ("report", "audit"):
        if not name:
            return "name the report: `repo/NNN_name`, `NNN_name` or `NNN`", True
        found = _report(cfg, name, ".audit.md" if what == "audit" else ".md")
        if isinstance(found, str):
            return found, True
        text = _text(found)
        if text is None:
            return f"{found.name} doesn't read", True
        if what == "report" and not section:
            return outline(text), False
        return _file(found, "" if section == "all" else section, part, again)
    if what == "question":
        q = questions.find(cfg.control, name) if name else None
        if q is None:
            open_qs = [x.id for x in questions.scan(cfg.control) if x.is_open]
            return f"no question {name!r}; open: {', '.join(open_qs) or 'none'}", True
        return _file(q.path, "", part, again)
    if what == "picks":
        path = cfg.path("picks", f"{Path(name).name}.choices.json")
        if not name or not path.is_file():
            saved = ", ".join(p.name.removesuffix(".choices.json") for p in cfg.path("picks").glob("*.choices.json"))
            return f"no saved choices {name!r}; saved: {saved or 'none'}", True
        try:
            text = json.dumps(json.loads(_text(path) or "null"), indent=1, ensure_ascii=False)
        except ValueError:
            text = _text(path) or ""
        return paged(text, part, again), False
    if what == "receipt":
        rel = name.replace("\\", "/").lstrip("/")
        path = cfg.control / "receipts" / (rel if rel.endswith(".md") else rel + ".md")
        if ".." in rel or not path.is_file():
            return f"no receipt {name!r} (name the write's path: `signals/planner-….signal`, `queue/<repo>/NNN_x.md`)", True
        return _file(path, "", part, again)
    return f"unknown `what` {what!r}; one of: {', '.join(READ_KINDS)}", True


def _text(path: Path) -> str | None:
    try:
        return read_text(path)
    except (OSError, UnicodeDecodeError):
        return None


def _file(path: Path, section: str, part: int, again: str) -> tuple[str, bool]:
    text = _text(path)
    if text is None:
        return f"{path.name} doesn't exist or doesn't read", True
    if section and section != "all":
        found = section_text(text, section)
        if found is None:
            heads = [h for h, _, _ in sections(text)] or [h for h, _, _ in sections(text, "### ")]
            return f"no section {section!r} in {path.name}; sections: {'; '.join(heads) or 'none'}", True
        text = found
    return paged(text, part, again), False
