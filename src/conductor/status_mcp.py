"""`conductor mcp`: a trial stdio MCP server with one read-only tool, `status(project?)`,
for a claude.ai planner chat linked to this machine through the Claude desktop app
(docs/mcp.md). It reads what `conductor status` reads and writes nothing a project uses.

    conductor mcp [--control <folder> ...]

The projects are the `--control` folders, else the app's remembered list, else the
`*-control` folders beside this repo holding a `conductor.toml`. The JSON-RPC loop is
`ask_server.serve_stdio`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import config as config_mod
from . import questions, state
from .ask_server import serve_stdio
from .events import EventLog
from .inbox import progress_line
from .util import clock, duration, now, parse_stamp

SERVER_NAME = "conductor-status"
TOOL_NAME = "status"
MAX_CHARS = 1500            # it lands in the planner's context
NOTE_CHARS = 160
QUEUE_SHOWN = 6
ROOT = Path(__file__).resolve().parents[2]
SETTINGS_KEY = "controls"   # app.window.SETTINGS_KEY

TOOL = {
    "name": TOOL_NAME,
    "description": (
        "Conductor's live state on Matt's machine, read-only. With `project`: per repo the running "
        "item (elapsed, latest progress line), the queue, proposed items, and whether the project's "
        "daemon is alive and how old its last heartbeat and event are. Without it: every project, "
        "one line each. A stopped daemon is reported, not an error."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "project": {"type": "string", "description": "The project name (conductor.toml `project`); leave out to list them."},
        },
    },
}


def ago(ts: str | None) -> str:
    if not ts:
        return "never"
    try:
        secs = (now() - parse_stamp(ts)).total_seconds()
        return f"{secs / 86400:.1f} days ago" if secs >= 2 * 86400 else f"{duration(secs)} ago"
    except ValueError:
        return ts


def remembered() -> list[Path]:
    """The app's remembered projects (`app.window`'s QSettings list); [] if Qt can't say."""
    try:
        from PySide6.QtCore import QSettings           # QtCore only: app.window would pull in the GUI
        value = QSettings("conductor", "conductor-app").value(SETTINGS_KEY, [])
    except Exception:
        return []
    value = [value] if isinstance(value, str) else value or []
    return [Path(v) for v in value]


def siblings() -> list[Path]:
    return sorted(p for p in ROOT.parent.glob("*-control") if (p / config_mod.CONFIG_NAME).is_file())


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
                if (p := log.last("progress", item=item_id)) and p.detail.get("note"):
                    note = progress_line(p)
                    note = note if len(note) <= NOTE_CHARS else note[:NOTE_CHARS - 1] + "…"
                    out.append(f"    progress {clock(p.ts)}: {note}")
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
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS - 12] + "\n…(cut)"


def one_line(cfg: config_mod.Config) -> str:
    d = state.daemon_info(cfg)
    daemon = ("daemon alive" if d.alive and not d.stale else "daemon NOT ANSWERING" if d.alive
              else "daemon DOWN")
    running = queued = 0
    try:
        for repo in cfg.repos:
            rdir = cfg.path("running", repo)
            running += len(list(rdir.glob("*.run.json"))) if rdir.is_dir() else 0
            qdir = cfg.path("queue", repo)
            queued += len(list(qdir.glob("*.md"))) if qdir.is_dir() else 0
    except OSError:
        pass
    return f"{cfg.project}: {daemon}, {running} running, {queued} queued ({cfg.control})"


class StatusServer:
    def __init__(self, controls: list[Path], out=sys.stdout):
        self.controls, self.out = controls, out

    def send(self, obj: dict) -> None:
        self.out.write(json.dumps(obj) + "\n")
        self.out.flush()

    def error(self, mid, code: int, message: str) -> None:
        self.send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})

    def configs(self) -> tuple[list[config_mod.Config], list[str]]:
        found, bad = [], []
        for c in self.controls or remembered() or siblings():
            try:
                found.append(config_mod.load(Path(c)))
            except Exception as exc:            # a folder that won't load is named, not fatal
                bad.append(f"{c}: {exc}")
        return found, bad

    def status(self, args: dict) -> str:
        cfgs, bad = self.configs()
        want = (args.get("project") or "").strip().lower()
        if not want:
            lines = [one_line(c) for c in cfgs] + [f"not loaded: {b}" for b in bad]
            return "\n".join(lines) or "no projects known"
        for c in cfgs:
            if want in (c.project.lower(), c.control.name.lower(), c.control.name.lower().removesuffix("-control")):
                return project_status(c)
        return f"no project {args['project']!r}; known: {', '.join(c.project for c in cfgs) or 'none'}"

    def handle(self, msg: dict) -> None:
        method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                      "capabilities": {"tools": {}}, "serverInfo": {"name": SERVER_NAME, "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [TOOL]}
        elif method == "tools/call":
            if params.get("name") != TOOL_NAME:
                self.error(mid, -32602, f"unknown tool {params.get('name')!r}")
                return
            try:
                text, is_error = self.status(params.get("arguments") or {}), False
            except Exception as exc:            # never leave a call hanging on a bug
                text, is_error = f"status failed: {exc}", True
            result = {"content": [{"type": "text", "text": text}], "isError": is_error}
        elif method == "ping":
            result = {}
        elif mid is not None:
            self.error(mid, -32601, f"method not found: {method}")
            return
        else:
            return                              # notifications need no reply
        self.send({"jsonrpc": "2.0", "id": mid, "result": result})


def main(controls: list[str] | None = None) -> int:
    return serve_stdio(StatusServer([Path(c) for c in controls or []]))
