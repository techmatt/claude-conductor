"""The broker: its own process, started by the daemon for a project with `[remote]`
(`hook.start_broker`). It serves the job API and the browser worker page on the LAN,
behind a shared token in every path (`/w/<token>/…`); the main box's own calls (jobs,
claims, bundles) are taken from loopback only. It exits once the daemon has been gone a
minute, and a crash costs nothing but speed: `farm-seeds` runs whatever is left locally.

    python -m conductor.remote.broker <control folder> [--stay]

`--stay` (tests, a manual run): serve until killed, whether a daemon runs or not.
"""

from __future__ import annotations

import base64
import binascii
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

from . import settings
from .board import Board

PAGE = Path(__file__).with_name("page")
MAX_BODY = 16 * 1024 * 1024    # a shard's output, base64'd, fits well under this
FILE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,120}$")
RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM\d|LPT\d)(\..*)?$", re.I)     # Windows device names
JOB_KEEP_DAYS = 7
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".wasm": "application/wasm",
         ".json": "application/json", ".css": "text/css; charset=utf-8"}
DAEMON_GONE_SECONDS = 60.0
LOCAL_ONLY = {"job", "bundle", "claim", "state", "close", "status"}


def lan_ip() -> str:
    """This box's address on the LAN (no packet is sent), else loopback."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


class Handler(BaseHTTPRequestHandler):
    server_version = "conductor-remote"
    timeout = 30                 # a slow or stalled client loses its connection, not a thread forever
    board: Board
    token: str
    bundles: Path
    page: dict                   # the worker page's fixed facts: project, accent

    def log_message(self, fmt, *args):     # quiet: the broker log keeps only what matters
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
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
            return self._file(self.bundles / rest[1] / rest[2])
        if rest == ["api", "status"] and self._local():
            return self._json(self.board.summary())
        self._send(404, b"not found", "text/plain")

    def _file(self, path: Path) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            return self._send(404, b"not found", "text/plain")
        self._send(200, data, TYPES.get(path.suffix, "application/octet-stream"))

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

    def _op(self, op: str, b: dict):
        board = self.board
        if op == "hello":
            if b.get("kind") == "local-remote" and not self._local():
                raise ValueError("a local-remote worker runs on the main box")
            reply = board.hello(str(b["worker"]), str(b["kind"]), int(b.get("cores", 1)), str(b.get("label", "")))
            if b.get("view"):                    # the worker page: what it shows, with the hello it already sends
                reply |= {"page": self.page, "view": board.view(str(b["worker"]))}
            return reply
        if op == "poll":
            return board.poll(str(b["worker"]))
        if op == "gate":
            return {"pass": board.gate(str(b["worker"]), str(b["key"]), str(b["value"]))}
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
            board.set_bundle(str(b["key"]), str(b["state"]), str(b.get("error", "")), str(b.get("wasm", "")))
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
                                     "page": {"project": r.project, "accent": r.accent}})
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
    path = r.control.joinpath(*settings.NOW_LINE)
    if line is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(line + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


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


def serve(control: Path, stay: bool = False) -> int:
    r = settings.load(control)
    if r is None:
        print("no [remote] section: nothing to serve", file=sys.stderr)
        return 2
    r.state.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - JOB_KEEP_DAYS * 86400
    for old in (r.state / "jobs").glob("j*") if (r.state / "jobs").is_dir() else []:
        if old.stat().st_mtime < cutoff:
            shutil.rmtree(old, ignore_errors=True)
    board = Board(r.state, r.lease_seconds, r.fresh_seconds, r.bundle_js, native_argv=r.native)
    try:
        server = make_server(r, board)
    except OSError as exc:
        print(f"{time.strftime('%H:%M:%S')} cannot serve on {r.bind}:{r.port}: {exc} (another project's broker? "
              "give each project its own `port`)", flush=True)
        return 1
    port = server.server_address[1]
    url = f"http://{lan_ip()}:{port}/w/{r.secret()}/"
    info = {"pid": os.getpid(), "port": port, "url": url, "local": f"http://127.0.0.1:{port}/w/{r.secret()}/",
            "digest": r.digest(), "started": time.time()}
    tmp = r.state / "broker.json.tmp"
    tmp.write_text(json.dumps(info, indent=1) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, r.state / "broker.json")
    print(f"{time.strftime('%H:%M:%S')} broker pid {os.getpid()} serving {url}", flush=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    gone_since = None
    try:
        while True:
            time.sleep(2)
            _write_now(r, board.now_line())
            board.load = main_load(r.control)
            if stay or _daemon_alive(r.control):
                gone_since = None
            elif gone_since is None:
                gone_since = time.time()
            elif time.time() - gone_since > DAEMON_GONE_SECONDS:
                print(f"{time.strftime('%H:%M:%S')} daemon gone; broker exits", flush=True)
                break
    finally:
        _write_now(r, None)
        server.shutdown()
        try:
            if json.loads((r.state / "broker.json").read_text(encoding="utf-8")).get("pid") == os.getpid():
                (r.state / "broker.json").unlink()
        except (OSError, ValueError):
            pass
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    sys.exit(serve(Path(next((a for a in args if a != "--stay"), ".")), stay="--stay" in args))
