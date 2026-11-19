"""`conductor mcp`, as the desktop app starts it: a thin launcher (docs/mcp.md, *The launcher*).

The app starts one server process and keeps it for its whole life, so a server that held
its code would need a full app restart after every conductor prompt that changes a tool. The
launcher holds no tool code: it relays each JSON-RPC message to a worker (`conductor mcp
--worker`, `planner_mcp`) and, before a call, starts a fresh worker when conductor's source
has changed since the running one started (or it has died), replaying the client's
`initialize`. A changed tool's behaviour is live on the next call; a new or renamed tool, or
a changed description, still needs the app restarted, since the app lists tools once.

Calls are relayed as they arrive, never one at a time (141): the worker runs them together,
each reply is routed back by its id, and a call never ends unanswered. A worker that exits,
or a call over `CALL_SECONDS`, gets an error result for each call it had. A JSON-RPC batch
(an array) is answered as an array. Every message in and out is logged, a line each, to
`out/mcp-traffic.log` beside this checkout (`TRAFFIC`).

    conductor mcp [--control <folder> ...]             the launcher (what the app runs)
    conductor mcp --worker [--control <folder> ...]    the server itself, one process
    conductor mcp --selftest [--control <folder>]      every tool against the sandbox, a line each
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from . import procs

PACKAGE = Path(__file__).resolve().parent
REPLAY_ID = "launcher-initialize"
CALL_SECONDS = 150.0        # a write waits 30s for its receipt, a session start up to 60s more
READY_SECONDS = 30.0        # a fresh worker's replayed `initialize`
TRAFFIC = PACKAGE.parents[1] / "out" / "mcp-traffic.log"
TRAFFIC_BYTES = 1_000_000   # then it moves to `.1`, the one kept before it


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


def messages(line: str) -> list:
    """The JSON values on one line: one as a rule, several when a client runs them together."""
    out, dec, i = [], json.JSONDecoder(), 0
    while i < len(line):
        while i < len(line) and line[i] in " \t\r\n":
            i += 1
        if i >= len(line):
            break
        value, i = dec.raw_decode(line, i)
        out.append(value)
    return out


def key(mid) -> str:
    """An id as a dict key that keeps `1` and `"1"` apart."""
    return json.dumps(mid)


class Call:
    def __init__(self, mid, method: str, label: str, batch: "Batch | None"):
        self.id, self.method, self.label, self.batch = mid, method, label, batch
        self.started = time.monotonic()


class Batch:
    """A JSON-RPC batch: its replies go back together, as one array."""

    def __init__(self, n: int):
        self.n, self.replies = n, []


class Worker:
    """One worker process and the calls in flight on it; a thread routes what it says."""

    def __init__(self, launcher: "Launcher", cmd: list[str], watch: Path):
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(
            filter(None, [str(watch.parent), os.environ.get("PYTHONPATH")]))}   # this very package
        self.seen = fingerprint(watch)
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                     encoding="utf-8", env=env,
                                     **({"creationflags": subprocess.CREATE_NO_WINDOW} if procs.WINDOWS else {}))
        try:
            procs.die_with_me(self.proc)
        except Exception:
            pass                                # the worker ends at stdin's close anyway
        self.pending: dict[str, Call] = {}      # under the launcher's lock
        self.ready = threading.Event()
        self.gone = False
        self.write_lock = threading.Lock()
        self.thread = threading.Thread(target=launcher._read_worker, args=(self,), daemon=True)
        self.thread.start()

    def send(self, msg: dict) -> bool:
        try:
            with self.write_lock:
                self.proc.stdin.write(json.dumps(msg) + "\n")
                self.proc.stdin.flush()
            return True
        except (OSError, ValueError):           # it died, or its stdin is closed
            return False

    def close(self) -> None:
        """No more calls: it answers those it has, then exits at its stdin's end."""
        try:
            with self.write_lock:
                self.proc.stdin.close()
        except (OSError, ValueError):
            pass


class Launcher:
    def __init__(self, cmd: list[str], out=None, watch: Path = PACKAGE, traffic: Path | None = TRAFFIC,
                 call_seconds: float = CALL_SECONDS):
        self.cmd, self.watch, self.traffic, self.call_seconds = cmd, watch, traffic, call_seconds
        self.out = out or sys.stdout
        self.worker: Worker | None = None
        self.workers: list[Worker] = []         # the current one, and any retired still answering
        self.init: dict | None = None           # the client's `initialize`, replayed to a new worker
        self.starts = 0
        self.lock = threading.RLock()
        self.out_lock = threading.Lock()
        self.log_lock = threading.Lock()
        self.closing = threading.Event()
        threading.Thread(target=self._watchdog, daemon=True).start()

    @property
    def proc(self) -> subprocess.Popen | None:
        return self.worker.proc if self.worker else None

    # ------------------------------------------------------------- the worker
    def start(self) -> Worker:
        """A new worker; the one before it is retired, finishing what it has."""
        old, self.worker = self.worker, None
        if old is not None:
            old.close()
        w = Worker(self, self.cmd, self.watch)
        self.workers.append(w)
        self.starts += 1
        self.log("start", f"worker pid {w.proc.pid} (start {self.starts})")
        if self.init is not None:               # a fresh worker: the handshake the client made once
            w.send({**self.init, "id": REPLAY_ID})
            if not w.ready.wait(READY_SECONDS):
                self.log("start", "the replayed initialize had no answer")
            w.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        else:
            w.ready.set()
        self.worker = w
        return w

    def fresh(self) -> Worker:
        """A worker running the source as it is now."""
        w = self.worker
        if w is None or w.gone or w.proc.poll() is not None or fingerprint(self.watch) != w.seen:
            w = self.start()
        return w

    def stop(self, wait: float = 35.0) -> None:
        """Every worker told to finish, waited for up to `wait`, then killed."""
        self.closing.set()
        for w in list(self.workers):
            w.close()
        end = time.monotonic() + wait
        for w in list(self.workers):
            try:
                w.proc.wait(timeout=max(0.1, end - time.monotonic()))
            except subprocess.TimeoutExpired:
                w.proc.kill()
            w.thread.join(5)
        self.worker = None

    def _read_worker(self, w: Worker) -> None:
        """Route what a worker says: a reply to its call's caller, anything else to the client."""
        for line in w.proc.stdout:
            try:
                found = messages(line)
            except ValueError:
                self.log("drop", f"worker {w.proc.pid}: not JSON: {line.strip()[:120]}")
                continue
            for msg in found:
                if not isinstance(msg, dict):
                    continue
                if msg.get("id") == REPLAY_ID:
                    w.ready.set()
                    continue
                if "id" in msg and ("result" in msg or "error" in msg) and "method" not in msg:
                    with self.lock:
                        call = w.pending.pop(key(msg["id"]), None)
                    if call is None:
                        self.log("late", f"id {key(msg['id'])}: already answered (timed out); dropped")
                        continue
                    self.answer(call, msg)
                else:
                    self._emit(msg)
        self._worker_gone(w)

    def _worker_gone(self, w: Worker) -> None:
        with self.lock:
            w.gone = True
            calls, w.pending = list(w.pending.values()), {}
            if self.worker is w:
                self.worker = None              # the next call starts another
            if w in self.workers:
                self.workers.remove(w)
        w.ready.set()
        for call in calls:
            self.answer(call, error(call.id, "conductor's MCP worker exited during the call; the next call "
                                             "starts a new one. A write may have landed: `status`, or "
                                             "read(what=\"receipt\")"))
        if calls:
            self.log("gone", f"worker {w.proc.pid} exited with {len(calls)} call(s) in flight")

    def _watchdog(self) -> None:
        """A call over `call_seconds` is answered with an error; a late reply is dropped."""
        while not self.closing.wait(1.0):
            now, late = time.monotonic(), []
            with self.lock:
                for w in self.workers:
                    for k, call in list(w.pending.items()):
                        if now - call.started > self.call_seconds:
                            late.append(w.pending.pop(k))
            for call in late:
                self.answer(call, error(call.id, f"no answer from conductor's MCP worker in "
                                                 f"{self.call_seconds:.0f}s ({call.label}). A write may still "
                                                 "land: `status`, or read(what=\"receipt\") later"))

    # ------------------------------------------------------------- the client
    def answer(self, call: Call, reply: dict) -> None:
        self.log("out", f"id {key(call.id)} {call.label} {time.monotonic() - call.started:.2f}s"
                        + (" ERROR" if "error" in reply or (reply.get("result") or {}).get("isError") else ""))
        if call.batch is None:
            self._emit(reply, logged=True)
            return
        with self.lock:
            call.batch.replies.append(reply)
            done = len(call.batch.replies) == call.batch.n
        if done:
            self._emit(call.batch.replies, logged=True)

    def _emit(self, msg, logged: bool = False) -> None:
        if not logged:
            self.log("out", summary(msg))
        with self.out_lock:
            self.out.write(json.dumps(msg) + "\n")
            self.out.flush()

    def handle(self, msg: dict, batch: Batch | None = None) -> None:
        method, mid = msg.get("method"), msg.get("id")
        is_call = method is not None and "id" in msg
        self.log("in", summary(msg))
        with self.lock:
            if method == "initialize":
                self.init = msg
            if method in ("initialize", "tools/call") or self.worker is None or self.worker.gone:
                w = self.fresh()
            else:
                w = self.worker
            if is_call:
                call = Call(mid, method, label(msg), batch)
                w.pending[key(mid)] = call
        if not w.send(msg) and is_call:         # it died before the call reached it
            with self.lock:
                lost = w.pending.pop(key(mid), None)
            if lost is not None:
                self.answer(lost, error(mid, "conductor's MCP worker was not running; the next call starts "
                                             "a new one. Nothing was written"))

    def feed(self, line: str) -> None:
        """One line from the client: a message, a batch, or several run together."""
        line = line.strip()
        if not line:
            return
        try:
            found = messages(line)
        except ValueError:
            self.log("in", f"parse error: {line[:200]}")
            self._emit({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            return
        for value in found:
            if isinstance(value, list):
                calls = [m for m in value if isinstance(m, dict) and m.get("method") and "id" in m]
                if not value:
                    self._emit({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "empty batch"}})
                    continue
                batch = Batch(len(calls)) if calls else None
                for m in value:
                    if isinstance(m, dict):
                        self.handle(m, batch)
            elif isinstance(value, dict):
                self.handle(value)

    def serve(self, stdin=None) -> int:
        stdin = stdin or sys.stdin
        for line in stdin:
            self.feed(line)
        self.log("in", "stdin closed")
        self.stop()
        return 0

    # ------------------------------------------------------------- the log
    def log(self, way: str, text: str) -> None:
        """A line in `out/mcp-traffic.log`: what came in and went out, never what it said."""
        if self.traffic is None:
            return
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S.%f}"[:-3] + f" {os.getpid()} {way:<5} {text}\n"
        with self.log_lock:
            try:
                self.traffic.parent.mkdir(parents=True, exist_ok=True)
                if self.traffic.is_file() and self.traffic.stat().st_size > TRAFFIC_BYTES:
                    os.replace(self.traffic, self.traffic.with_name(self.traffic.name + ".1"))
                with open(self.traffic, "a", encoding="utf-8", newline="\n") as fh:
                    fh.write(line)
            except OSError:
                pass


def error(mid, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": message}}


def label(msg: dict) -> str:
    """`tools/call read(stats)`: a call named, its arguments not."""
    method, params = msg.get("method"), msg.get("params") or {}
    if method != "tools/call":
        return str(method)
    args = params.get("arguments") or {}
    detail = args.get("what") or args.get("kind") or ""
    return f"{params.get('name')}({detail})" if detail else str(params.get("name"))


def summary(msg) -> str:
    if isinstance(msg, list):
        return f"batch of {len(msg)}: " + "; ".join(summary(m) for m in msg)
    if not isinstance(msg, dict):
        return type(msg).__name__
    if msg.get("method"):
        return (f"id {key(msg['id'])} " if "id" in msg else "notification ") + label(msg)
    return f"reply id {key(msg.get('id'))}" + (" ERROR" if "error" in msg else "")


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
        self.replies: dict = {}

    def send(self, method: str, params: dict) -> int:
        self.n += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()
        return self.n

    def reply(self, mid: int) -> dict:
        while mid not in self.replies:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("the launcher exited")
            msg = json.loads(line)
            if isinstance(msg, dict) and "id" in msg:
                self.replies[msg["id"]] = msg
        msg = self.replies.pop(mid)
        if "error" in msg:
            raise RuntimeError(msg["error"].get("message"))
        return msg["result"]

    def request(self, method: str, params: dict) -> dict:
        return self.reply(self.send(method, params))

    def together(self, calls: list[tuple[str, dict]]) -> list[tuple[str, bool]]:
        """Every call written before any reply is read: a chat's calls sent in one turn."""
        ids = [self.send("tools/call", {"name": t, "arguments": a}) for t, a in calls]
        out = []
        for mid in ids:
            r = self.reply(mid)
            out.append((r["content"][0]["text"], bool(r.get("isError"))))
        return out

    def call(self, tool: str, args: dict) -> tuple[str, bool]:
        r = self.request("tools/call", {"name": tool, "arguments": args})
        return r["content"][0]["text"], bool(r.get("isError"))

    def close(self) -> None:
        self.proc.stdin.close()
        self.proc.wait(timeout=60)


def selftest(control: Path | None = None, out=None) -> int:
    """Every tool through the launcher against the sandbox, one line each: `ok` or `FAIL`,
    with what came back; then calls sent together (three reads; a write with a read), each of
    which must answer. The writes are kept harmless: a probe proposal, then its cancel; the
    rest check a refusal that names what is there. 0 when every line is ok."""
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

    def together(label: str, calls: list[tuple[str, dict, object]]) -> list[str]:
        try:
            got = c.together([(t, {"project": p, **a}) for t, a, _ in calls])
            bad = [f"{t}: {' '.join(text.split())[:60]}" for (t, _, ok), (text, err) in zip(calls, got)
                   if not ok(text, err)]
            said = "; ".join(bad) if bad else f"{len(got)} sent together, {len(got)} answered"
            results.append((label, not bad, said[:110]))
            return [text for text, _ in got]
        except Exception as exc:
            results.append((label, False, f"{type(exc).__name__}: {exc}"[:110]))
            return []

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
        together("reads-at-once", [("read", {"what": "conductor-report"}, lambda t, e: not e and "Past the mark" in t),
                             ("read", {"what": "stats"}, lambda t, e: not e and "stats" in t),
                             ("read", {"what": "repo", "name": repo}, lambda t, e: not e and "(tracked):" in t)])
        got = together("write+read", [
            ("queue_item", {"repo": repo, "name": PROBE, "body": "conductor mcp --selftest: never runs", "held": True},
             lambda t, e: not e and t.startswith("accepted: proposed")),
            ("status", {}, lambda t, e: not e and t.startswith(f"{p} — daemon"))])
        if got:                                 # the probe goes again, alone
            name = next((w.strip("`)") for w in got[0].split() if w.strip("`)").endswith(PROBE)), PROBE)
            c.call("signal", {"project": p, "lines": [f"cancel: proposed/{name}"]})
    finally:
        c.close()
    for label_, good, said in results:
        out.write(f"{'ok  ' if good else 'FAIL'} {label_:<13} {said}\n")
    bad = sum(not g for _, g, _ in results)
    out.write(f"{len(results) - bad} of {len(results)} ok ({control})\n")
    return 1 if bad else 0


def main(controls: list[str] | None = None) -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    return Launcher(worker_cmd(controls or [])).serve()
