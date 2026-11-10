"""The broker: its own process, started by the daemon for a project with `[remote]`
(`hook.start_broker`). It serves the job API and the browser worker page on the LAN,
behind a shared token in every path (`/w/<token>/…`); the main box's own calls (jobs,
claims, bundles) are taken from loopback only. It exits once the daemon has been gone a
minute, and a crash costs nothing but speed: `farm-seeds` runs whatever is left locally.

    python -m conductor.remote.broker <control folder> [--stay] [--after <pid>]

`--stay` (tests, a manual run): serve until killed, whether a daemon runs or not.
`--after <pid>`: a handover; wait for that broker to exit, then take its port and board.

The join URL stays one URL for good: this PC's name (not its LAN IP), the port, and a token
pinned in `[remote]`. The board is saved every 2 s and taken up at start, so a restart —
a handover when conductor's own code changes (`CODE_CHECK_SECONDS`, as the daemon does),
or the daemon starting a broker that died — loses no job: a worker's shard in flight lands
on the new broker, and `farm-seeds` waits out the gap (`client.PATIENCE`).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import shutil
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import diag, settings
from .board import Board

PAGE = Path(__file__).with_name("page")
MAX_BODY = 16 * 1024 * 1024    # a shard's output, base64'd, fits well under this
FILE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,120}$")
RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM\d|LPT\d)(\..*)?$", re.I)     # Windows device names
JOB_KEEP_DAYS = 7
JS = "text/javascript; charset=utf-8"      # a module script of any other type is refused by the browser
TYPES = {".html": "text/html; charset=utf-8", ".js": JS, ".mjs": JS, ".cjs": JS, ".wasm": "application/wasm",
         ".json": "application/json", ".css": "text/css; charset=utf-8"}
DAEMON_GONE_SECONDS = 60.0
LOCAL_ONLY = {"job", "bundle", "claim", "state", "close", "status"}
CODE_CHECK_SECONDS = 10.0
CODE_RETRY_SECONDS = 300.0       # a version that won't import is tried again after this
AFTER_WAIT_SECONDS = 30.0        # `--after`: the old broker has this long to let go of the port
BOARD = "board.json"             # the saved board, in .conductor/remote/


def lan_ip() -> str:
    """This box's address on the LAN (no packet is sent), else loopback."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def join_host(r: settings.Remote) -> str:
    """`host` if set; else this PC's name when it resolves to a LAN address (it survives an
    IP change: other machines ask the router or LLMNR for it); else the LAN IP."""
    if r.host:
        return r.host
    name = socket.gethostname()
    try:
        if any(not a.startswith("127.") for a in socket.gethostbyname_ex(name)[2]):
            return name
    except OSError:
        pass
    return lan_ip()


class Door:
    """POSTs in flight. A handover closes it (new calls get 503 and retry) and waits for the
    ones inside, so the board it saves holds every call answered."""

    def __init__(self):
        self.cv, self.inside, self.closed = threading.Condition(), 0, False

    def enter(self) -> bool:
        with self.cv:
            if self.closed:
                return False
            self.inside += 1
            return True

    def leave(self) -> None:
        with self.cv:
            self.inside -= 1
            self.cv.notify_all()

    def close(self, timeout: float = 15.0) -> None:
        with self.cv:
            self.closed = True
            self.cv.wait_for(lambda: self.inside == 0, timeout)


_versions: dict[tuple, str] = {}


def page_version() -> str:
    """The worker page's files as served now: an open tab that sees this change reloads."""
    files = [PAGE / "index.html", PAGE / "worker.js"]
    try:
        stat = tuple((p.stat().st_mtime_ns, p.stat().st_size) for p in files)
    except OSError:
        return ""
    if stat not in _versions:
        _versions.clear()
        _versions[stat] = hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()[:12]
    return _versions[stat]


class Handler(BaseHTTPRequestHandler):
    server_version = "conductor-remote"
    timeout = 30                 # a slow or stalled client loses its connection, not a thread forever
    board: Board
    token: str
    bundles: Path
    page: dict                   # the worker page's fixed facts: project, accent
    door: Door

    def log_message(self, fmt, *args):     # quiet: the broker log keeps only what matters
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json", cache: str = "no-store") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode())

    def _route(self) -> list[str] | None:
        parts = self.path.split("?", 1)[0].split("/")
        # ['', 'w', token, ...rest]
        if len(parts) < 3 or parts[1] != "w" or not hmac.compare_digest(parts[2].encode(), self.token.encode()):
            self._send(404, b"not found", "text/plain")
            return None
        return parts[3:]

    def _local(self) -> bool:
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def do_GET(self):
        rest = self._route()
        if rest is None:
            return
        if rest in ([], [""]):
            rest = ["index.html"]
        if len(rest) == 1 and rest[0] in ("index.html", "worker.js"):
            return self._file(PAGE / rest[0])
        if (len(rest) == 3 and rest[0] == "bundle" and all(FILE_RE.match(x) and not RESERVED.match(x)
                                                            for x in rest[1:])):
            # A bundle's folder is its key (the commit and any diff hash) and is never rewritten
            # once there: a browser keeps it, so a tab's cores and a reload fetch it once.
            return self._file(self.bundles / rest[1] / rest[2], cache="public, max-age=604800, immutable")
        if rest == ["api", "status"] and self._local():
            return self._json(self.board.summary())
        self._send(404, b"not found", "text/plain")

    def _file(self, path: Path, cache: str = "no-store") -> None:
        try:
            data = path.read_bytes()
        except OSError:
            return self._send(404, b"not found", "text/plain")
        self._send(200, data, TYPES.get(path.suffix, "application/octet-stream"), cache)

    def do_POST(self):
        rest = self._route()
        if rest is None:
            return
        if len(rest) != 2 or rest[0] != "api":
            return self._send(404, b"not found", "text/plain")
        op = rest[1]
        # JSON only: a browser can't send that cross-origin without a preflight this server
        # never answers, so a page open on the main box can't drive the loopback calls (CSRF).
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self._send(415, b"application/json only", "text/plain")
        if op in LOCAL_ONLY and (not self._local() or self.headers.get("Origin")):
            return self._send(403, b"main box only", "text/plain")
        if not self.door.enter():
            return self._send(503, b"restarting: try again", "text/plain")
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if not 0 <= n <= MAX_BODY:
                self.close_connection = True
                return self._send(413, b"bad length", "text/plain")
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("a JSON object, please")
            self._json(self._op(op, body))
        except (ValueError, KeyError, TypeError, binascii.Error) as exc:
            self._json({"error": str(exc)[:200]}, 400)
        finally:
            self.door.leave()

    def _op(self, op: str, b: dict):
        board = self.board
        if op == "hello":
            if b.get("kind") == "local-remote" and not self._local():
                raise ValueError("a local-remote worker runs on the main box")
            reply = board.hello(str(b["worker"]), str(b["kind"]), int(b.get("cores", 1)), str(b.get("label", "")),
                                fresh=b.get("fresh") is True, report=b.get("report"))
            board.renew(str(b["worker"]), b.get("running"))
            if b.get("log"):                     # the page's own log since its last hello (diag.py)
                reply["log_taken"] = diag.page_lines(board.root, str(b["worker"]), str(b.get("label") or b["worker"]),
                                                     b["log"], b.get("clock"))
            if b.get("view"):                    # the worker page: what it shows, with the hello it already sends
                reply |= {"page": {**self.page, "version": page_version()},
                          "view": board.view(str(b["worker"]), b.get("running"))}
            return reply
        if op == "poll":
            return board.poll(str(b["worker"]), str(b.get("label") or "") or None)
        if op == "gate":
            wid, key = str(b["worker"]), str(b["key"])
            return {"pass": board.gate(wid, key, str(b["value"])), "reference": board.reference_kind(wid, key)}
        if op == "result":
            ok = b.get("ok") is True
            if ok and isinstance(b.get("output_b64"), str):       # native workers: the bytes exactly
                output = base64.b64decode(b["output_b64"], validate=True)
            elif ok and isinstance(b.get("output"), str):         # the browser: its string, as UTF-8
                output = b["output"].encode("utf-8")
            elif ok:
                raise ValueError("an ok result needs `output` (text) or `output_b64`")
            else:
                output = b""
            return {"accepted": board.result(str(b["worker"]), str(b["job"]), int(b["shard"]), ok, output,
                                             str(b.get("log", "")), float(b.get("ms", 0)))}
        if op == "job":
            ranges = [(int(a), int(n)) for a, n in b["ranges"]]
            cwd = b.get("cwd")
            return {"job": board.add_job(str(b["key"]), str(b["fingerprint"]), [str(x) for x in b.get("args", [])],
                                         ranges, str(cwd) if cwd else None, str(b.get("item", "")))}
        if op == "bundle":
            board.set_bundle(str(b["key"]), str(b["state"]), str(b.get("error", "")), str(b.get("wasm", "")),
                             str(b.get("note", "")))
            return {"ok": True}
        if op == "claim":
            return board.claim(str(b["job"]), bool(b.get("rerun", True)))
        if op == "state":
            return board.state(str(b["job"]))
        if op == "close":
            board.close(str(b["job"]))
            return {"ok": True}
        if op == "status":
            live = board.live(b.get("key"))
            return {"live": len(live), "browsers": sum(w.kind == "browser" for w in live), **board.summary()}
        raise ValueError(f"unknown op {op}")


def make_server(r: settings.Remote, board: Board, port: int | None = None, bind: str | None = None) -> ThreadingHTTPServer:
    handler = type("H", (Handler,), {"board": board, "token": r.secret(), "bundles": r.state / "bundles",
                                     "page": {"project": r.project, "accent": r.accent}, "door": Door()})
    server = ThreadingHTTPServer((bind if bind is not None else r.bind, r.port if port is None else port), handler)
    server.daemon_threads = True
    return server


def _daemon_alive(control: Path) -> bool:
    import psutil
    try:
        lock = json.loads((control / ".conductor" / "daemon.json").read_text(encoding="utf-8"))
        return psutil.pid_exists(int(lock["pid"]))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _write_now(r: settings.Remote, line: str | None) -> None:
    """The inbox's *Now* line. A reader holding the file (Windows refuses the replace or
    unlink then) costs one 2 s beat, never the broker: aicomplete's died of it, 95."""
    path = r.control.joinpath(*settings.NOW_LINE)
    try:
        if line is None:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(line + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    except OSError:
        pass


def main_load(control: Path) -> dict | None:
    """The main box's load from the daemon's latest memory sample while it is fresh (what
    the inbox's load line shows): `{cpu, top, text}`, else None. One small file read."""
    from .. import config, load, memory
    try:
        cfg = config.load(control)
        rec = memory.latest(cfg.state_dir)
        if not (memory.fresh(rec, cfg) and rec.get("load")):
            return None
        sample = rec["load"]
        return {"cpu": sample.get("cpu"), "top": sample.get("top") or [], "text": load.text(sample),
                "high": bool(sample.get("high"))}
    except Exception:                            # noqa: BLE001 - a page nicety, never the broker's end
        return None


def running_items(control: Path) -> list[str]:
    """The items running on the main box now (`running/<repo>/<name>.run.json`), as
    `<repo>/<name>`: the Cluster panel names them while no farm job is out yet."""
    try:
        return sorted(f"{p.parent.name}/{p.name[:-len('.run.json')]}"
                      for p in (control / "running").glob("*/*.run.json"))
    except OSError:
        return []


def write_status(r: settings.Remote, info: dict, board: Board) -> None:
    """`status.json`, for the app's chip and Workers tab (`remote/app.py`): the broker's
    address and start, and `Board.panel`. Written by the loop that already runs every 2 s;
    the app reads it at its own refresh, so nothing new polls."""
    try:
        status = {"at": time.time(), "pid": info["pid"], "port": info["port"], "url": info["url"],
                  "url_ip": info.get("url_ip"), "started": info["started"], "handover": info.get("handover", False),
                  "main_slots": r.main_slots, **board.panel()}
        tmp = r.state / "status.json.tmp"
        tmp.write_text(json.dumps(status) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, r.state / "status.json")
    except OSError:                              # the app's view, never the broker's end
        pass


_swept, _diag_error = [0.0], [""]


def write_diag(r: settings.Remote, board: Board) -> None:
    """`remote-diag.md` at the control folder's root, when its text changes (`diag.render`),
    and once an hour the page logs past their week. A planner's read, never the broker's end."""
    try:
        if time.time() - _swept[0] > 3600:
            _swept[0] = time.time()
            diag.sweep(r.state)
        diag.write_digest(r.control, diag.render(r.project, board.panel(r.main_slots), r.state))
    except Exception as exc:                     # noqa: BLE001
        if repr(exc) != _diag_error[0]:          # once, not every 2 s
            _diag_error[0] = repr(exc)
            say(f"remote-diag.md not written: {exc!r}")


def say(text: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {text}", flush=True)


def _write_info(r: settings.Remote, info: dict) -> None:
    tmp = r.state / "broker.json.tmp"
    tmp.write_text(json.dumps(info, indent=1) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, r.state / "broker.json")


class CodeWatch:
    """Conductor's own code, read as the daemon reads it (`daemon_restart`): `due()` gives a
    changed version that is committed, checked out cleanly and imports, else None."""

    def __init__(self, probe=None, blockers=None, imports=None):
        from .. import daemon_restart as dr
        self.probe = probe or dr.code_version
        self.blockers = blockers or dr.checkout_blockers
        self.imports = imports or (lambda cwd: dr.imports_ok(cwd, "import conductor.remote.broker"))
        self.running = self.probe()              # None (not a git checkout): never restarts
        self.at = time.monotonic()
        self.refused: dict[str, float] = {}

    def due(self, cwd: Path) -> str | None:
        if self.running is None or time.monotonic() - self.at < CODE_CHECK_SECONDS:
            return None
        self.at = time.monotonic()
        v = self.probe()
        if v is None or v == self.running or time.monotonic() - self.refused.get(v, -1e9) < CODE_RETRY_SECONDS:
            return None
        if self.blockers():
            return None                          # uncommitted or mid-rebase: the next check
        if error := self.imports(cwd):
            self.refused[v] = time.monotonic()
            say(f"conductor's code changed but does not import ({error}); keeping this broker")
            return None
        return v


def handover(r: settings.Remote, server: ThreadingHTTPServer, board: Board, info: dict, stay: bool) -> bool:
    """Start the next broker (it waits for this one to exit), stop taking calls, wait for
    those inside, save the board and name the new pid in `broker.json`. False, and still
    serving, if the new one can't be started."""
    from .. import procs
    cmd = [sys.executable, "-P", "-m", "conductor.remote.broker", str(r.control), "--after", str(os.getpid())]
    # Said before the spawn: the new broker's handle on the log starts at its end as it is then,
    # so a line written here afterwards would be overwritten by the new broker's first.
    say("conductor's code changed: handing over to a new broker")
    try:
        new = procs.spawn_detached(cmd + (["--stay"] if stay else []), r.control, r.state / "broker.log")
    except OSError as exc:
        say(f"the new broker did not start ({exc}); keeping this one")
        return False
    server.RequestHandlerClass.door.close()
    server.shutdown()
    server.server_close()
    board.save(r.state / BOARD)
    _write_info(r, {**info, "pid": new.pid, "starting": True})
    return True


def _wait_gone(pid: int) -> None:
    import psutil
    end = time.time() + AFTER_WAIT_SECONDS
    while psutil.pid_exists(pid) and time.time() < end:
        time.sleep(0.1)


def serve(control: Path, stay: bool = False, after: int | None = None, watch: CodeWatch | None = None) -> int:
    r = settings.load(control)
    if r is None:
        print("no [remote] section: nothing to serve", file=sys.stderr)
        return 2
    if after:
        _wait_gone(after)
    r.state.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - JOB_KEEP_DAYS * 86400
    for old in (r.state / "jobs").glob("j*") if (r.state / "jobs").is_dir() else []:
        if old.stat().st_mtime < cutoff:
            shutil.rmtree(old, ignore_errors=True)
    board = Board(r.state, r.lease_seconds, r.fresh_seconds, r.bundle_js, native_argv=r.native,
                  seed_seconds=r.seed_seconds)
    restored = board.restore(r.state / BOARD)
    try:
        server = make_server(r, board)
    except OSError as exc:
        say(f"cannot serve on {r.bind}:{r.port}: {exc} (another project's broker? give each project its own `port`)")
        return 1
    port, token = server.server_address[1], r.secret()
    url = f"http://{join_host(r)}:{port}/w/{token}/"
    info = {"pid": os.getpid(), "port": port, "url": url, "url_ip": f"http://{lan_ip()}:{port}/w/{token}/",
            "local": f"http://127.0.0.1:{port}/w/{token}/", "digest": r.digest(), "started": time.time(),
            "handover": bool(after)}
    _write_info(r, info)
    say(f"broker pid {os.getpid()} serving {url}" + (f" (after {after})" if after else "")
        + (f"; carried on {len(board.jobs)} job(s) from the saved board" if restored and board.jobs else ""))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    watch = watch or CodeWatch()
    gone_since, handed = None, False
    try:
        while True:
            time.sleep(2)
            _write_now(r, board.now_line())
            board.load = main_load(r.control)
            board.items = running_items(r.control)
            write_status(r, info, board)
            write_diag(r, board)
            try:
                board.save(r.state / BOARD)
            except OSError:
                pass
            if watch.due(r.control) and handover(r, server, board, info, stay):
                handed = True
                break
            if stay or _daemon_alive(r.control):
                gone_since = None
            elif gone_since is None:
                gone_since = time.time()
            elif time.time() - gone_since > DAEMON_GONE_SECONDS:
                say("daemon gone; broker exits")
                break
    finally:
        if not handed:                           # a handover leaves these to the new broker
            _write_now(r, None)
            (r.state / "status.json").unlink(missing_ok=True)
            server.shutdown()
            try:
                if json.loads((r.state / "broker.json").read_text(encoding="utf-8")).get("pid") == os.getpid():
                    (r.state / "broker.json").unlink()
            except (OSError, ValueError):
                pass
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    after = int(args[args.index("--after") + 1]) if "--after" in args else None
    rest = [a for i, a in enumerate(args) if a != "--stay" and a != "--after" and (i == 0 or args[i - 1] != "--after")]
    sys.exit(serve(Path(rest[0] if rest else "."), stay="--stay" in args, after=after))
