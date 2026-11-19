"""`conductor mcp`, as the desktop app starts it: a thin launcher (docs/mcp.md, *The launcher*).

The app starts one server process and keeps it for its whole life, so a server that held
its code would need a full app restart after every conductor prompt that changes a tool. The
launcher holds no tool code: it relays each JSON-RPC line to a worker (`conductor mcp
--worker`, `planner_mcp`) and, before a call, starts a fresh worker when conductor's source
has changed since the running one started (or it has died), replaying the client's
`initialize`. A changed tool's behaviour is live on the next call; a new or renamed tool, or
a changed description, still needs the app restarted, since the app lists tools once.

    conductor mcp [--control <folder> ...]             the launcher (what the app runs)
    conductor mcp --worker [--control <folder> ...]    the server itself, one process
    conductor mcp --selftest [--control <folder>]      every tool against the sandbox, a line each
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from . import procs

PACKAGE = Path(__file__).resolve().parent
REPLAY_ID = "launcher-initialize"


def fingerprint(root: Path = PACKAGE) -> tuple:
    """Every source file under `root` by (path, mtime, size): any edit, add or removal moves it."""
    out = []
    for p in root.rglob("*.py"):
        if "__pycache__" in p.parts or "tests" in p.parts:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        out.append((p.as_posix(), st.st_mtime_ns, st.st_size))
    return tuple(sorted(out))


def worker_cmd(controls: list[str]) -> list[str]:
    return [sys.executable, "-m", "conductor", "mcp", "--worker", *(a for c in controls for a in ("--control", str(c)))]


class Launcher:
    def __init__(self, cmd: list[str], out=None, watch: Path = PACKAGE):
        self.cmd, self.watch = cmd, watch
        self.out = out or sys.stdout
        self.proc: subprocess.Popen | None = None
        self.seen: tuple | None = None          # the source the running worker started from
        self.init: dict | None = None           # the client's `initialize`, replayed to a new worker
        self.starts = 0

    # ------------------------------------------------------------- the worker
    def start(self) -> None:
        self.stop()
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(
            filter(None, [str(self.watch.parent), os.environ.get("PYTHONPATH")]))}   # this very package
        self.seen = fingerprint(self.watch)
        self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                     encoding="utf-8", env=env,
                                     **({"creationflags": subprocess.CREATE_NO_WINDOW} if procs.WINDOWS else {}))
        self.starts += 1
        try:
            procs.die_with_me(self.proc)
        except Exception:
            pass                                # the worker ends at stdin's close anyway
        if self.init is not None:               # a fresh worker: the handshake the client made once
            self._send({**self.init, "id": REPLAY_ID})
            self._reply(REPLAY_ID)
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def stop(self) -> None:
        if self.proc is None:
            return
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
        self.proc = None

    def fresh(self) -> None:
        """A worker running the source as it is now."""
        if self.proc is None or self.proc.poll() is not None or fingerprint(self.watch) != self.seen:
            self.start()

    def _send(self, msg: dict) -> None:
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def _reply(self, mid) -> dict | None:
        """Lines from the worker until the reply to `mid`; any other line is relayed."""
        while True:
            line = self.proc.stdout.readline()
            if not line:
                return None                     # it died mid-call
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("id") == mid and ("result" in msg or "error" in msg):
                return msg
            if mid != REPLAY_ID:
                self._emit(msg)

    def _emit(self, msg: dict) -> None:
        self.out.write(json.dumps(msg) + "\n")
        self.out.flush()

    # ------------------------------------------------------------- the client
    def handle(self, msg: dict) -> None:
        method, mid = msg.get("method"), msg.get("id")
        if method == "initialize":
            self.init = msg
        if method in ("initialize", "tools/call") or self.proc is None:
            self.fresh()
        self._send(msg)
        if mid is None:
            return                              # a notification: no reply
        reply = self._reply(mid)
        if reply is None:
            self.proc = None                    # the next call starts another
            reply = {"jsonrpc": "2.0", "id": mid, "error": {
                "code": -32603, "message": "conductor's MCP worker exited during the call; the next call starts a new one"}}
        self._emit(reply)

    def serve(self, stdin=None) -> int:
        stdin = stdin or sys.stdin
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self._emit({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
                continue
            if isinstance(msg, dict):
                self.handle(msg)
        self.stop()
        return 0


# ----------------------------------------------------------------- the self-check
PROBE = "selftest_probe"
NONE = "selftest_none"


def sandbox() -> Path:
    """The sandbox control folder beside this checkout (CLAUDE.md: tests touch nothing else)."""
    return PACKAGE.parents[2] / "conductor-sandbox-control"


class _Client:
    """Just enough of an MCP client: the launcher started as the desktop app starts it."""

    def __init__(self, control: Path):
        self.proc = subprocess.Popen([sys.executable, "-m", "conductor", "mcp", "--control", str(control)],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8",
                                     **({"creationflags": subprocess.CREATE_NO_WINDOW} if procs.WINDOWS else {}))
        self.n = 0

    def request(self, method: str, params: dict) -> dict:
        self.n += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("the launcher exited")
            msg = json.loads(line)
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(msg["error"].get("message"))
                return msg["result"]

    def call(self, tool: str, args: dict) -> tuple[str, bool]:
        r = self.request("tools/call", {"name": tool, "arguments": args})
        return r["content"][0]["text"], bool(r.get("isError"))

    def close(self) -> None:
        self.proc.stdin.close()
        self.proc.wait(timeout=30)


def selftest(control: Path | None = None, out=None) -> int:
    """Every tool through the launcher against the sandbox, one line each: `ok` or `FAIL`,
    with what came back. The writes are kept harmless: a probe proposal, then its cancel;
    the rest check a refusal that names what is there. 0 when every line is ok."""
    from . import config as config_mod
    from . import planner_mcp
    out = out or sys.stdout
    control = Path(control or sandbox())
    cfg = config_mod.load(control)
    p, repo = cfg.project, next(iter(cfg.repos))
    c = _Client(control)
    results: list[tuple[str, bool, str]] = []

    def check(tool: str, args: dict, ok, label: str = "") -> str:
        try:
            text, err = c.call(tool, {"project": p, **args})
            good = bool(ok(text, err))
        except Exception as exc:                # the launcher or the worker broke
            text, good = f"{type(exc).__name__}: {exc}", False
        results.append((label or tool, good, " ".join(text.split())[:110]))
        return text

    try:
        c.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "conductor-selftest", "version": "1"}})
        listed = [t["name"] for t in c.request("tools/list", {})["tools"]]
        if listed != planner_mcp.TOOL_NAMES:
            results.append(("tools/list", False, f"lists {listed}"))
        check("status", {}, lambda t, e: not e and t.startswith(f"{p} — daemon"))
        check("start_session", {"label": "selftest"}, lambda t, e: not e and "\n=====\n" in t)
        check("read", {"what": "repo", "name": repo}, lambda t, e: not e and "(tracked):" in t)
        got = check("queue_item", {"repo": repo, "name": PROBE, "body": "conductor mcp --selftest: never runs",
                                   "held": True}, lambda t, e: not e and t.startswith("accepted: proposed"))
        name = next((w.strip("`)") for w in got.split() if w.strip("`)").endswith(PROBE)), PROBE)
        check("amend", {"item": f"999_{NONE}", "text": "x"}, lambda t, e: e and "no queued, staged or running" in t)
        check("signal", {"lines": [f"cancel: proposed/{name}"]}, lambda t, e: not e and t.startswith("accepted"))
        check("edit_state", {"file": "state.md", "sections": [{"op": "delete", "section": NONE}]},
              lambda t, e: e and ("Its sections:" in t or "doesn't exist yet" in t))
        check("answer", {"question": NONE, "text": "x"}, lambda t, e: e and t.startswith("no question"))
        check("park", {"repo": NONE, "line": "x"}, lambda t, e: e and t.startswith("no repo"))
        check("note", {"kind": "backlog", "repo": NONE, "name": "x", "body": "x"},
              lambda t, e: e and t.startswith("no repo"))
    finally:
        c.close()
    for label, good, said in results:
        out.write(f"{'ok  ' if good else 'FAIL'} {label:<13} {said}\n")
    bad = sum(not g for _, g, _ in results)
    out.write(f"{len(results) - bad} of {len(results)} ok ({control})\n")
    return 1 if bad else 0


def main(controls: list[str] | None = None) -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    return Launcher(worker_cmd(controls or [])).serve()
