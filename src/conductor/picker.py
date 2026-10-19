"""The picker server: one small localhost HTTP server per daemon, serving any number of
pickers (`picks`), started on the first `pick:` signal.

    GET  /<token>/<name>/                 the page (web/picker.html)
    GET  /<token>/<name>/manifest.json    the manifest as the page reads it, with `saved`
    GET  /<token>/<name>/img/<r>/<o>      the image of row r, option o (indexes)
    POST /<token>/<name>/save             {"rows": [{"id", "key", "note"}]} → choices file

Everything else is a 404: another token, name or index, an extra path part, a method
other than these, a `Host` that is not this server. No part of a URL is ever joined to
a file path; images are found by index in the manifest validated at `pick:` time. The
token keeps other pages in the browser from finding the server; a save must be JSON,
which a cross-site form cannot send without a preflight the server never answers.

Handler threads touch only the files and a queue: the events they produce are appended
by `drain`, on the thread that owns the event log (the daemon's loop, or the CLI's).
"""

from __future__ import annotations

import json
import queue
import re
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import picks
from .config import Config
from .util import clock

PAGE = Path(__file__).with_name("web") / "picker.html"
MAX_BODY_BYTES = 1024 * 1024
INDEX = re.compile(r"\d{1,6}")


class Server:
    """`open(ref)` validates the manifest, starts the server if it is not up, and opens
    the picker in the default browser. Re-opening a manifest re-validates it and
    replaces what is served, so an executor's update shows."""

    def __init__(self, cfg: Config, log, opener=None):
        """`opener(url)` opens a URL; default: the default browser (`webbrowser.open`)."""
        self.cfg, self.log, self.opener = cfg, log, opener
        self.token = secrets.token_urlsafe(16)
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.entries: dict[str, tuple[picks.Manifest, str | None]] = {}    # name → (manifest, item)
        self.events: queue.SimpleQueue = queue.SimpleQueue()
        self.lock = threading.Lock()

    @property
    def port(self) -> int | None:
        return self.httpd.server_address[1] if self.httpd else None

    def url(self, name: str) -> str:
        return f"http://127.0.0.1:{self.port}/{self.token}/{name}/"

    def start(self) -> None:
        if self.httpd is not None:
            return
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        httpd.daemon_threads = True
        httpd.owner = self
        self.httpd = httpd
        self.thread = threading.Thread(target=httpd.serve_forever, name="conductor-picker", daemon=True)
        self.thread.start()

    def close(self) -> None:
        if self.httpd is None:
            return
        httpd, self.httpd = self.httpd, None
        httpd.shutdown()
        httpd.server_close()

    def _item(self, ref: str) -> str | None:
        """The finished item whose report named this manifest (the latest), if any."""
        item = None
        for ev in self.log.query(kinds=["finish"]):
            if ev.detail.get("pick") == ref:
                item = ev.item
        return item

    def open(self, ref: str, by: str | None = None, rel: str | None = None) -> str:
        """Serve the manifest `ref` names and open its page; the URL. A bad manifest
        raises ValueError, a server that cannot start OSError, before anything is logged."""
        manifest = picks.load_manifest(self.cfg, ref)
        # A name is a URL and a choices file: two manifests sharing one would swap pages
        # and overwrite each other's choices, so the second is refused.
        served = self.entries.get(manifest.name)
        if served is not None and served[0].ref != manifest.ref:
            raise ValueError(f"pick {manifest.ref}: the name {manifest.name!r} is already served for "
                             f"{served[0].ref}; give this manifest a name of its own")
        saved = picks.load_choices(self.cfg.control, manifest.name)
        if saved is not None and saved["manifest"] != manifest.ref:
            raise ValueError(f"pick {manifest.ref}: picks/{manifest.name}.choices.json holds the choices for "
                             f"{saved['manifest']}; give this manifest a name of its own")
        self.start()
        item = self._item(manifest.ref)
        self.entries[manifest.name] = (manifest, item)
        url = self.url(manifest.name)
        extra = {"by": by} if by else {}
        paths = [rel] if rel else []
        try:
            ok = (self.opener or webbrowser.open)(url)
        except Exception as exc:            # noqa: BLE001 - a browser problem is an event, not a crash
            self.log.append("pick_failed", item=item, paths=paths, manifest=manifest.ref, name=manifest.name,
                            url=url, error=f"opening {url} failed: {exc!r}", **extra)
            return url
        self.log.append("pick_open", item=item, paths=paths, name=manifest.name, manifest=manifest.ref,
                        url=url, opened=ok is not False, **extra)
        return url

    def drain(self) -> list:
        """Append the events the handler threads produced; the events appended."""
        out = []
        while True:
            try:
                kind, item, paths, detail = self.events.get_nowait()
            except queue.Empty:
                return out
            out.append(self.log.append(kind, item=item, paths=paths, **detail))

    # Called on handler threads: files and the queue only.
    def manifest_json(self, manifest: picks.Manifest) -> dict:
        saved = picks.load_choices(self.cfg.control, manifest.name)
        saved = picks.applied(manifest, saved if saved and saved["manifest"] == manifest.ref else None)
        if saved:
            saved["clock"] = clock(saved["saved_at"])
        return {
            "name": manifest.name, "title": manifest.title, "intro": manifest.intro, "ref": manifest.ref,
            "rows": [{"id": row.id, "label": row.label, "note": row.note,
                      "options": [{"key": o.key, "label": o.label, "src": f"img/{r}/{i}"}
                                  for i, o in enumerate(row.options)]}
                     for r, row in enumerate(manifest.rows)],
            "saved": saved,
        }

    def save(self, manifest: picks.Manifest, item: str | None, body) -> dict:
        with self.lock:                     # two tabs saving at once share one temp file
            data = picks.save_choices(self.cfg.control, manifest, body)
        rel = f"picks/{manifest.name}.choices.json"
        self.events.put(("pick_saved", item, [rel], {
            "name": manifest.name, "manifest": manifest.ref, "changed": data["changed"],
            "notes": data["notes"], "rows": len(data["rows"])}))
        return {"saved_at": data["saved_at"], "clock": clock(data["saved_at"]),
                "changed": data["changed"], "notes": data["notes"]}


class Handler(BaseHTTPRequestHandler):
    server_version = "conductor-picker"
    sys_version = ""

    @property
    def owner(self) -> Server:
        return self.server.owner

    def log_message(self, format, *args) -> None:       # noqa: A002 - the base class's name
        pass

    def __getattr__(self, name: str):
        # Every method but GET and POST (HEAD, PUT, DELETE, OPTIONS, ...) is a 404.
        if name.startswith("do_"):
            return self._not_found
        raise AttributeError(name)

    def _send(self, code: int, body: bytes, ctype: str, **headers: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in headers.items():
            self.send_header(key.replace("_", "-"), value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8",
                   Cache_Control="no-store")

    def _not_found(self) -> None:
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def _route(self) -> tuple[picks.Manifest, str | None, list[str], str] | None:
        """(manifest, item, the parts after the name, the query) for a request to this
        server's token and a served name; else None."""
        port = self.owner.port
        if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return None
        path, _, query = self.path.partition("?")
        parts = path.split("/")
        if len(parts) < 4 or parts[0] or not secrets.compare_digest(parts[1], self.owner.token):
            return None
        entry = self.owner.entries.get(parts[2])
        if entry is None:
            return None
        return entry[0], entry[1], parts[3:], query

    def do_GET(self) -> None:
        route = self._route()
        if route is None:
            return self._not_found()
        manifest, _, rest, query = route
        if rest == [""]:
            try:
                page = PAGE.read_bytes()
            except OSError:
                return self._not_found()
            return self._send(200, page, "text/html; charset=utf-8", Cache_Control="no-store")
        if rest == ["manifest.json"]:
            return self._json(200, self.owner.manifest_json(manifest))
        if len(rest) == 3 and rest[0] == "img" and not query and all(INDEX.fullmatch(x) for x in rest[1:]):
            r, o = int(rest[1]), int(rest[2])
            if r >= len(manifest.rows) or o >= len(manifest.rows[r].options):
                return self._not_found()
            image = manifest.rows[r].options[o].image
            try:
                data = image.read_bytes() if image.is_file() else None
            except OSError:
                data = None
            if data is None:
                return self._not_found()
            extra = {"Content_Security_Policy": "sandbox"} if image.suffix.lower() == ".svg" else {}
            return self._send(200, data, picks.IMAGE_TYPES[image.suffix.lower()], Cache_Control="no-cache", **extra)
        return self._not_found()

    def do_POST(self) -> None:
        route = self._route()
        if route is None:
            return self._not_found()
        manifest, item, rest, query = route
        if rest != ["save"] or query:
            return self._not_found()
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return self._json(400, {"error": "a save must be application/json"})
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            return self._json(400, {"error": "Content-Length is required"})
        if not 0 <= length <= MAX_BODY_BYTES:
            return self._json(400, {"error": f"the body is over {MAX_BODY_BYTES // 1024} KB"})
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            result = self.owner.save(manifest, item, body)
        except RecursionError:
            return self._json(400, {"error": "the body is nested too deeply"})
        except (ValueError, UnicodeDecodeError) as exc:
            return self._json(400, {"error": str(exc)})
        except OSError as exc:
            return self._json(500, {"error": f"could not write the choices file: {exc}"})
        return self._json(200, result)


def serve(cfg: Config, log, ref: str, by: str | None = "cli", until_saved: bool = False,
          opener=None, echo=print, poll: float = 0.2) -> int:
    """`conductor pick` with no daemon running: serve the picker here until Ctrl+C (or,
    with `until_saved`, the first save), appending events as the daemon would."""
    srv = Server(cfg, log, opener)
    try:
        try:
            url = srv.open(ref, by=by)
        except OSError as exc:
            log.append("pick_failed", manifest=ref, error=f"the picker server could not start: {exc}",
                       **({"by": by} if by else {}))
            return 1
        echo(f"serving the picker at {url} — Ctrl+C stops it")
        while True:
            for ev in srv.drain():
                if until_saved and ev.kind == "pick_saved":
                    return 0
            time.sleep(poll)
    except KeyboardInterrupt:
        srv.drain()
        return 0
    finally:
        srv.close()
