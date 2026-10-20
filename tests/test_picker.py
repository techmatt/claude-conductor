"""Pickers: manifest validation, the report's `pick:` line, the localhost server's routes
and saves, the `pick:` signal, the inbox's Picks section and `conductor pick`."""

import http.client
import json
import os
import queue
import re
import threading
import time
from pathlib import Path

import pytest

from conductor import cli, config, executor, inbox, picker, picks, procs, state
from conductor.events import EventLog
from conductor.util import stamp

REF = "code/picks/m.json"


def manifest_data() -> dict:
    return {"name": "art_alts", "title": "Hero art", "intro": "pick one",
            "rows": [{"id": "hero", "label": "Hero", "note": "idle pose",
                      "options": [{"key": "current", "image": "a.png"},
                                  {"key": "1", "image": "b.png", "label": "warmer"}]},
                     {"id": "logo", "label": "Logo",
                      "options": [{"key": "current", "image": "c.svg"},
                                  {"key": "2", "image": "../art/d.png"}]}]}


def write_manifest(repo, data=None, rel: str = "picks/m.json") -> None:
    """The manifest at `rel` in the repo's main checkout, with the images it names."""
    folder = repo / "picks"
    folder.mkdir(parents=True, exist_ok=True)
    (repo / "art").mkdir(exist_ok=True)
    (folder / "a.png").write_bytes(b"\x89PNG-a")
    (folder / "b.png").write_bytes(b"\x89PNG-b")
    (folder / "c.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    (repo / "art" / "d.png").write_bytes(b"\x89PNG-d")
    text = data if isinstance(data, str) else json.dumps(manifest_data() if data is None else data)
    (repo / rel).write_text(text, encoding="utf-8")


@pytest.fixture
def bare(tmp_path):
    """A control folder and repo with no git and no daemon: enough for `picks` and a Server."""
    control, repo = tmp_path / "control", tmp_path / "code"
    control.mkdir()
    repo.mkdir()
    (control / "conductor.toml").write_text('project = "t"\n\n[repos]\ncode = "../code"\n', encoding="utf-8")
    cfg = config.load(control)
    log = EventLog(cfg.events_db)
    yield cfg, log
    log.close()


@pytest.fixture
def served(bare):
    """A Server with the good manifest open; the browser is a list."""
    cfg, log = bare
    write_manifest(cfg.repos["code"])
    urls: list[str] = []
    srv = picker.Server(cfg, log, opener=urls.append)
    url = srv.open(REF, by="test")
    yield srv, url, urls, cfg, log
    srv.close()


def request(srv, method: str, path: str, body=None, headers=None):
    """(status, headers, body bytes) of one request to the server, sent as is."""
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    try:
        conn.request(method, path, body=body, headers={"Host": f"127.0.0.1:{srv.port}", **(headers or {})})
        resp = conn.getresponse()
        return resp.status, resp.headers, resp.read()
    finally:
        conn.close()


def post_save(srv, rows, name: str = "art_alts", ctype: str = "application/json"):
    body = json.dumps({"rows": rows}).encode("utf-8")
    return request(srv, "POST", f"/{srv.token}/{name}/save", body, {"Content-Type": ctype})


def choice(hero: str = "current", logo: str = "current", note: str = "") -> list[dict]:
    return [{"id": "hero", "key": hero, "note": note}, {"id": "logo", "key": logo, "note": ""}]


# ---------------------------------------------------------------- the manifest
def test_good_manifest_loads(bare):
    cfg, _ = bare
    write_manifest(cfg.repos["code"])
    m = picks.load_manifest(cfg, REF)
    assert (m.ref, m.name, m.title, m.intro) == (REF, "art_alts", "Hero art", "pick one")
    assert [r.id for r in m.rows] == ["hero", "logo"] and m.rows[0].note == "idle pose"
    assert [o.key for o in m.rows[0].options] == ["current", "1"] and m.rows[0].options[1].label == "warmer"
    assert m.rows[1].options[1].image == (cfg.repos["code"] / "art" / "d.png").resolve()


def _set(path: str, value):
    """A mutation of the good manifest: `rows.0.options.1.image` = value (None deletes)."""
    def mutate(d):
        *head, last = [int(p) if p.isdigit() else p for p in path.split(".")]
        for p in head:
            d = d[p]
        if value is None:
            del d[last]
        else:
            d[last] = value
    return mutate


def _dup_row(d):
    d["rows"][1]["id"] = "hero"


def _dup_key(d):
    d["rows"][0]["options"][1]["key"] = "current"


@pytest.mark.parametrize("change, reason", [
    ("{not json", "not valid JSON"),
    ("[1, 2]", "must be a JSON object"),
    (_set("name", None), "name: None must be a slug"),
    (_set("name", "Art Alts"), "must be a slug"),
    (_set("title", ""), "title: a non-empty string is required"),
    (_set("rows", []), "rows: a non-empty list is required"),
    (_set("rows.0.options", []), "rows[0].options: a non-empty list is required"),
    (_set("rows.0.label", None), "rows[0].label: a non-empty string is required"),
    (_dup_row, "rows[1].id: 'hero' is used by an earlier row"),
    (_dup_key, "rows[0].options[1].key: 'current' is used by an earlier option"),
    (_set("rows.0.options.0.key", "a b"), "may not hold whitespace"),
    (_set("rows.0.options.1.image", "gone.png"), "rows[0].options[1].image: missing file picks/gone.png"),
    (_set("rows.0.options.1.image", "../../outside.png"), "rows[0].options[1].image: '../../outside.png' leaves the repo"),
    (_set("rows.0.options.1.image", "/picks/a.png"), "must be a path relative to the manifest"),
    (_set("rows.0.options.1.image", "C:/x/a.png" if os.name == "nt" else "/x/a.png"),
     "must be a path relative to the manifest"),
    (_set("rows.0.options.1.image", "notes.txt"), "is not one of"),
])
def test_manifest_refusals(bare, change, reason):
    cfg, _ = bare
    if isinstance(change, str):
        write_manifest(cfg.repos["code"], change)
    else:
        data = manifest_data()
        change(data)
        write_manifest(cfg.repos["code"], data)
    (cfg.repos["code"] / "picks" / "notes.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        picks.load_manifest(cfg, REF)
    assert reason in str(exc.value) and str(exc.value).startswith(f"pick {REF}: ")


@pytest.mark.parametrize("ref, reason", [
    ("elsewhere/picks/m.json", "'elsewhere' is not a repo"),
    ("code", "expects `<repo>/<manifest path>`"),
    ("code/../control/conductor.toml", "must be relative to the repo root"),
    ("code/picks/none.json", "no manifest file"),
])
def test_manifest_ref_refusals(bare, ref, reason):
    cfg, _ = bare
    write_manifest(cfg.repos["code"])
    with pytest.raises(ValueError) as exc:
        picks.load_manifest(cfg, ref)
    assert reason in str(exc.value)


def test_ref_is_normalised(bare):
    cfg, _ = bare
    write_manifest(cfg.repos["code"])
    for ref in ("code/picks\\m.json", "code/./picks//m.json", " code/picks/m.json "):
        assert picks.load_manifest(cfg, ref).ref == REF


def _link(link, target) -> bool:
    """A directory junction (Windows) or symlink at `link`; False when neither can be made."""
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
        return True
    except (ImportError, OSError):
        pass
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except OSError:
        return False


def test_manifest_in_a_linked_shared_folder(bare, tmp_path):
    # A Drive-homed `shared` folder is a junction out of the main checkout; what lies
    # behind it still counts as the repo's.
    cfg, _ = bare
    outside = tmp_path / "gdrive" / "art"
    outside.mkdir(parents=True)
    if not _link(cfg.repos["code"] / "art", outside):
        pytest.skip("cannot make a junction or symlink here")
    (outside / "a.png").write_bytes(b"\x89PNG-a")
    (outside / "b.png").write_bytes(b"\x89PNG-b")
    data = {"name": "shared_art", "title": "t",
            "rows": [{"id": "r", "label": "R", "options": [{"key": "current", "image": "a.png"},
                                                           {"key": "1", "image": "b.png"}]}]}
    (outside / "m.json").write_text(json.dumps(data), encoding="utf-8")
    m = picks.load_manifest(cfg, "code/art/m.json")
    assert m.rows[0].options[1].image.read_bytes() == b"\x89PNG-b"
    # A tracked manifest whose images sit behind the link loads too.
    data["rows"][0]["options"] = [{"key": "current", "image": "../art/a.png"}]
    (cfg.repos["code"] / "picks").mkdir()
    (cfg.repos["code"] / "picks" / "t.json").write_text(json.dumps(data), encoding="utf-8")
    assert picks.load_manifest(cfg, "code/picks/t.json").rows[0].options[0].image.is_file()


# ---------------------------------------------------------------- the report header
def test_header_pick_line(tmp_path):
    r = tmp_path / "r.md"
    r.write_text("status: done\nblocks: none\ncarry: none\npick: picks/m.json\n\nbody\n", encoding="utf-8")
    header, problem = executor.read_header(r)
    assert problem is None and header["pick"] == "picks/m.json"
    r.write_text("---\nstatus: done\nblocks: none\ncarry: none\npick: `picks/m.json`\n---\nbody\n", encoding="utf-8")
    header, problem = executor.read_header(r)
    assert problem is None and header["pick"] == "picks/m.json"
    r.write_text("status: done\nblocks: none\ncarry: none\n\nbody\n", encoding="utf-8")
    header, problem = executor.read_header(r)
    assert problem is None and "pick" not in header
    r.write_text("status: done\nblocks: none\ncarry: none\n\nsome text\npick: picks/m.json\n", encoding="utf-8")
    header, problem = executor.read_header(r)
    assert problem is None and "pick" not in header          # only as the fourth header line
    # A body that opens with a header-like line is still body.
    r.write_text("status: done\nblocks: none\ncarry: none\n\nStatus: all green after the fix\n", encoding="utf-8")
    header, problem = executor.read_header(r)
    assert problem is None and header["status"] == "done"
    r.write_text("status: done\nblocks: none\ncarry: none\n\nPick: went with the warmer palette\n", encoding="utf-8")
    header, problem = executor.read_header(r)
    assert problem is None and "pick" not in header


# ---------------------------------------------------------------- the server's routes
def test_open_logs_and_opens_once(served):
    srv, url, urls, cfg, log = served
    assert urls == [url] and url == f"http://127.0.0.1:{srv.port}/{srv.token}/art_alts/"
    [ev] = log.query(kinds=["pick_open"])
    assert ev.detail == {"name": "art_alts", "manifest": REF, "url": url, "opened": True, "by": "test"}


def test_routes_serve_page_manifest_and_images(served):
    srv, _, _, _, _ = served
    base = f"/{srv.token}/art_alts/"
    status, headers, body = request(srv, "GET", base)
    assert status == 200 and headers["Content-Type"].startswith("text/html") and b"<html" in body.lower()
    assert headers["X-Content-Type-Options"] == "nosniff"
    status, headers, body = request(srv, "GET", base + "manifest.json")
    assert status == 200 and headers["Content-Type"].startswith("application/json")
    m = json.loads(body)
    assert m["name"] == "art_alts" and m["ref"] == REF and m["saved"] is None
    assert [o["src"] for o in m["rows"][1]["options"]] == ["img/1/0", "img/1/1"]
    assert m["rows"][0]["options"][1] == {"key": "1", "label": "warmer", "src": "img/0/1"}
    status, headers, body = request(srv, "GET", base + "img/0/1")
    assert (status, headers["Content-Type"], body) == (200, "image/png", b"\x89PNG-b")
    assert headers["Cache-Control"] == "no-cache"
    status, headers, _ = request(srv, "GET", base + "img/1/0")
    assert status == 200 and headers["Content-Type"] == "image/svg+xml"
    assert headers["Content-Security-Policy"] == "sandbox"
    assert request(srv, "GET", base + "img/1/1")[2] == b"\x89PNG-d"       # ../art, inside the repo


def test_page_never_alters_an_image():
    # The art must look as it will in use: no rule touching a tile, an image or the
    # overlay may dim, filter, tint or blend it. The selection shows outside the image.
    page = (Path(picker.__file__).parent / "web" / "picker.html").read_text(encoding="utf-8")
    css = page.split("<style>", 1)[1].split("</style>", 1)[0]
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", css)
    touched = [(sel.strip(), body) for sel, body in rules if re.search(r"\bimg\b|\.tile|\.overlay|\.row", sel)]
    assert any(sel == ".tile.sel" and "outline-color" in body for sel, body in touched)
    for sel, body in touched:
        assert not re.search(r"opacity|filter|mix-blend|background-blend", body), sel
    assert not re.search(r"\.style\.(opacity|filter)|::(before|after)", page)


def test_routes_refuse_everything_else(served):
    srv, _, _, cfg, _ = served
    t = srv.token
    for path in ("/wrongtoken/art_alts/", f"/{t}/other/", f"/{t}/art_alts", f"/{t}/art_alts/img/2/0",
                 f"/{t}/art_alts/img/0/2", f"/{t}/art_alts/img/0", f"/{t}/art_alts/img/0/0/x",
                 f"/{t}/art_alts/img/0/0?x=1", f"/{t}/art_alts/img/-1/0",
                 f"/{t}/art_alts/img/../../conductor.toml", f"/{t}/art_alts/../../conductor.toml",
                 f"/{t}/art_alts/img/%2e%2e/%2e%2e/conductor.toml", f"/{t}/%2e%2e/conductor.toml",
                 f"/{t}/art_alts/manifest.json/x", f"/{t}/art_alts/save", f"/{t}/", "/", "/conductor.toml"):
        status, _, body = request(srv, "GET", path)
        assert status == 404 and b"project" not in body, path
    base = f"/{t}/art_alts/"
    for host in ("evil.example", f"evil.example:{srv.port}", "127.0.0.1", f"127.0.0.1:{srv.port + 1}"):
        assert request(srv, "GET", base, headers={"Host": host})[0] == 404, host
    assert request(srv, "GET", base, headers={"Host": f"localhost:{srv.port}"})[0] == 200
    for method in ("HEAD", "DELETE", "OPTIONS", "PUT", "PATCH"):
        assert request(srv, method, base)[0] == 404, method
    assert request(srv, "POST", base + "manifest.json", b"{}", {"Content-Type": "application/json"})[0] == 404


def test_deleted_image_is_404(served):
    srv, _, _, cfg, _ = served
    (cfg.repos["code"] / "picks" / "b.png").unlink()
    assert request(srv, "GET", f"/{srv.token}/art_alts/img/0/1")[0] == 404
    assert request(srv, "GET", f"/{srv.token}/art_alts/img/0/0")[0] == 200


# ---------------------------------------------------------------- saving
def test_save_writes_choices_and_drain_logs(served):
    srv, _, _, cfg, log = served
    # `changed` is worked out by the server, whatever the page says.
    rows = [{"id": "hero", "key": "1", "note": "  warmer is better  ", "changed": False},
            {"id": "logo", "key": "current", "note": ""}]
    status, _, body = post_save(srv, rows)
    assert status == 200
    res = json.loads(body)
    assert (res["changed"], res["notes"]) == (1, 1) and res["clock"] and res["saved_at"]
    path = picks.choices_path(cfg.control, "art_alts")
    raw = path.read_bytes()
    assert b"\r" not in raw and raw.endswith(b"}\n")
    data = json.loads(raw)
    assert data["name"] == "art_alts" and data["title"] == "Hero art" and data["manifest"] == REF
    assert (data["changed"], data["notes"], data["saved_at"]) == (1, 1, res["saved_at"])
    assert data["rows"] == [{"id": "hero", "key": "1", "changed": True, "note": "warmer is better"},
                            {"id": "logo", "key": "current", "changed": False, "note": ""}]
    # The handler thread queued the event; only drain() (this thread) writes the log.
    assert log.query(kinds=["pick_saved"]) == []
    [ev] = srv.drain()
    assert ev.kind == "pick_saved" and ev.paths == ["picks/art_alts.choices.json"]
    assert ev.detail == {"name": "art_alts", "manifest": REF, "changed": 1, "notes": 1, "rows": 2}
    assert srv.drain() == []
    # The page reopens on what was saved.
    m = json.loads(request(srv, "GET", f"/{srv.token}/art_alts/manifest.json")[2])
    assert m["saved"]["rows"][0] == {"id": "hero", "key": "1", "changed": True, "note": "warmer is better"}
    # Saving again overwrites.
    assert post_save(srv, choice(logo="2"))[0] == 200
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [r["key"] for r in data["rows"]] == ["current", "2"] and (data["changed"], data["notes"]) == (1, 0)
    assert [e.detail["changed"] for e in srv.drain()] == [1]


def test_save_refusals(served):
    srv, _, _, cfg, _ = served
    cases = [
        (choice(hero="nope"), "'nope' is not one of its options"),
        (choice()[:1], "row 'logo' is missing"),
        (choice() + [{"id": "extra", "key": "x"}], "no row 'extra'"),
        (choice() + [{"id": "hero", "key": "1"}], "given twice"),
        ([{"id": "hero", "key": "current", "note": "x" * (picks.MAX_NOTE_CHARS + 1)}, choice()[1]], "at most"),
    ]
    for rows, reason in cases:
        status, _, body = post_save(srv, rows)
        assert status == 400 and reason in json.loads(body)["error"], reason
    status, _, body = post_save(srv, choice(), ctype="text/plain")
    assert status == 400 and "application/json" in json.loads(body)["error"]
    status, _, _ = request(srv, "POST", f"/{srv.token}/art_alts/save", b"{nope", {"Content-Type": "application/json"})
    assert status == 400
    assert post_save(srv, choice(), name="other")[0] == 404
    assert not picks.choices_path(cfg.control, "art_alts").exists() and srv.drain() == []


def test_saved_choices_apply_to_a_changed_manifest(served):
    srv, _, _, cfg, _ = served
    assert post_save(srv, [{"id": "hero", "key": "1", "note": "keep"}, {"id": "logo", "key": "2", "note": ""}])[0] == 200
    data = manifest_data()
    data["rows"][0]["options"] = data["rows"][0]["options"][:1]          # key "1" is gone
    write_manifest(cfg.repos["code"], data)
    srv.open(REF)                                                       # re-pick: re-validated, replaced
    saved = json.loads(request(srv, "GET", f"/{srv.token}/art_alts/manifest.json")[2])["saved"]
    assert saved["rows"][0] == {"id": "hero", "key": "current", "changed": False, "note": "keep"}
    assert saved["rows"][1]["key"] == "2"


def test_saved_choices_carry_a_clock(served):
    srv, _, _, _, _ = served
    assert post_save(srv, choice())[0] == 200
    saved = json.loads(request(srv, "GET", f"/{srv.token}/art_alts/manifest.json")[2])["saved"]
    assert saved["clock"].endswith(("am", "pm"))


def test_deeply_nested_save_is_400(served):
    srv, _, _, _, _ = served
    status, _, body = request(srv, "POST", f"/{srv.token}/art_alts/save", b"[" * 200000,
                              {"Content-Type": "application/json"})
    assert status == 400 and b"nested" in body


def test_a_name_belongs_to_one_manifest(served):
    srv, _, urls, cfg, _ = served
    write_manifest(cfg.repos["code"], rel="picks/other.json")       # same name, art_alts
    with pytest.raises(ValueError, match="already served for code/picks/m.json"):
        srv.open("code/picks/other.json")
    assert post_save(srv, choice())[0] == 200
    # A fresh server (a restarted daemon) still refuses: the choices file is m.json's.
    other = picker.Server(cfg, srv.log, opener=urls.append)
    try:
        with pytest.raises(ValueError, match="holds the choices for code/picks/m.json"):
            other.open("code/picks/other.json")
        assert other.open(REF)
    finally:
        other.close()


def test_a_malformed_saved_at_is_ignored(bare):
    cfg, log = bare
    (cfg.control / "picks").mkdir()
    for n, value in enumerate(["yesterday", 5, "2026-10-18T16:52:03"]):
        (cfg.control / "picks" / f"x{n}.choices.json").write_text(json.dumps(
            {"name": f"x{n}", "manifest": f"code/x{n}.json", "saved_at": value, "rows": []}), encoding="utf-8")
    assert picks.scan_choices(cfg.control) == {}
    assert "## Picks" not in inbox.render(cfg, log, state.scan(cfg, log))


def test_opener_failure_is_pick_failed(bare):
    cfg, log = bare
    write_manifest(cfg.repos["code"])

    def broken(url):
        raise OSError("no browser")
    srv = picker.Server(cfg, log, opener=broken)
    try:
        url = srv.open(REF)
        [ev] = log.query(kinds=["pick_failed"])
        assert ev.detail["manifest"] == REF and url in ev.detail["error"] and "no browser" in ev.detail["error"]
        assert log.query(kinds=["pick_open"]) == [] and request(srv, "GET", f"/{srv.token}/art_alts/")[0] == 200
    finally:
        srv.close()


# ---------------------------------------------------------------- the `pick:` signal
def send(env, text: str, name: str = "s.signal") -> None:
    env.signal(name, text)
    env.tick_until(lambda: not (env.control / "signals" / name).exists())


def test_pick_signal_opens_once_and_reuses_the_port(env, monkeypatch):
    urls: list[str] = []
    monkeypatch.setattr(picker.webbrowser, "open", urls.append)
    write_manifest(env.repo)
    send(env, f"by: app\npick: {REF}")
    [ev] = env.events("pick_open")
    assert ev.detail["by"] == "app" and ev.detail["manifest"] == REF and ev.paths == ["signals/s.signal"]
    assert urls == [ev.detail["url"]]
    port = env.d.picker.port
    send(env, f"pick: {REF}", "t.signal")
    assert env.d.picker.port == port and len(urls) == 2 and urls[1] == urls[0]
    assert "by" not in env.events("pick_open")[-1].detail
    srv = env.d.picker
    assert request(srv, "GET", f"/{srv.token}/art_alts/manifest.json")[0] == 200


def test_pick_signal_refusals(env, monkeypatch):
    urls: list[str] = []
    monkeypatch.setattr(picker.webbrowser, "open", urls.append)
    data = manifest_data()
    data["rows"][0]["options"][1]["image"] = "gone.png"
    write_manifest(env.repo, data)
    send(env, f"pick: {REF}", "a")
    assert (env.control / "signals" / "a.bad").is_file()
    assert "rows[0].options[1].image: missing file picks/gone.png" in env.events("invalid")[-1].detail["error"]
    for i, value in enumerate(("nope", "elsewhere/picks/m.json", "code/a b.json", "code/")):
        send(env, f"pick: {value}", f"m{i}")
        assert (env.control / "signals" / f"m{i}.bad").is_file(), value
        assert "pick: expects `<repo>/<manifest path>`" in env.events("invalid")[-1].detail["error"], value
    assert urls == [] and env.events("pick_open") == [] and env.d.picker.port is None


def test_server_closes_when_the_daemon_stops(env, monkeypatch):
    monkeypatch.setattr(picker.webbrowser, "open", lambda url: True)
    write_manifest(env.repo)
    send(env, f"pick: {REF}")
    srv = env.d.picker
    port = srv.port
    assert request(srv, "GET", f"/{srv.token}/art_alts/")[0] == 200
    env.signal("stop.signal", "stop: daemon")
    env.d.run_forever()
    # (No connect attempt to prove it: a refused connect costs ~2s on Windows.)
    srv.thread.join(timeout=5)
    assert srv.httpd is None and srv.port is None and not srv.thread.is_alive() and port
    env.d = env.new_daemon()


# ---------------------------------------------------------------- the inbox
def picks_section(env) -> list[str]:
    text = inbox.render(env.d.cfg, env.d.log, state.scan(env.d.cfg, env.d.log))
    if "## Picks" not in text:
        return []
    return [ln for ln in text.split("## Picks\n", 1)[1].split("\n\n", 1)[0].splitlines() if ln]


def test_inbox_waiting_opened_saved(env):
    write_manifest(env.repo)
    env.d.log.append("finish", item="code/001_art", repo="code", status="done", pick=REF)
    assert picks_section(env) == [f"- code/001_art waiting for Matt — signal `pick: {REF}`"]
    [p] = picks.pending(env.cfg, state.scan(env.cfg, env.d.log), env.d.log)
    assert (p.item, p.state, p.name) == ("code/001_art", "waiting", None)
    srv = picker.Server(env.d.cfg, env.d.log, opener=lambda url: True)
    try:
        srv.open(REF)
        [line] = picks_section(env)
        assert line.startswith("- code/001_art opened ") and line.endswith(f", not saved — `{REF}`")
        assert post_save(srv, choice(hero="1", note="yes"))[0] == 200
        srv.drain()
        [line] = picks_section(env)
        assert line.startswith("- art_alts saved ") and line.endswith(
            ": 1 changed, 1 note; `picks/art_alts.choices.json` (code/001_art)")
        [p] = picks.pending(env.cfg, state.scan(env.cfg, env.d.log), env.d.log)
        assert (p.state, p.changed, p.notes, p.rows, p.choices) == ("saved", 1, 1, 2, "picks/art_alts.choices.json")
    finally:
        srv.close()
    # A later item reusing the manifest path starts again at waiting.
    time.sleep(1.1)                                     # stamps have second precision
    env.d.log.append("finish", item="code/002_more", repo="code", status="done", pick=REF)
    lines = picks_section(env)
    assert f"- code/002_more waiting for Matt — signal `pick: {REF}`" in lines


def test_a_settled_item_still_waits_until_saved(env):
    # Reviewed and then a checkpoint, never picked: still waiting, in the inbox and app.
    from conductor.app import model
    log = env.d.log
    write_manifest(env.repo)
    log.append("finish", item="code/001_art", repo="code", status="done", pick=REF)
    log.append("reviewed", item="code/001_art", repo="code")
    for n in range(2, 13):                              # pushes 001 past FINISHED_SHOWN
        log.append("finish", item=f"code/{n:03d}_x", repo="code", status="done")
        log.append("reviewed", item=f"code/{n:03d}_x", repo="code")
    log.append("checkpoint")
    view = state.scan(env.cfg, log)
    assert view.settled("code/001_art")
    assert picks_section(env) == [f"- code/001_art waiting for Matt — signal `pick: {REF}`"]
    project = model.Project(env.control)
    try:
        snap = project.snapshot()
    finally:
        project.close()
    assert snap.to_pick == 1
    [rs] = [r for r in snap.repos if r.name == "code"]
    assert "code/001_art" in [f.item for f in rs.finished]
    assert "code/001_art" not in [f.item for f in rs.earlier]
    # Saved: it settles like any other item.
    srv = picker.Server(env.d.cfg, log, opener=lambda url: True)
    try:
        srv.open(REF)
        assert post_save(srv, choice())[0] == 200
    finally:
        srv.close()
    assert all("001_art" not in ln for ln in picks_section(env))
    assert picks.pending(env.cfg, state.scan(env.cfg, log), log) == []


@pytest.mark.slow
def test_end_to_end_report_pick_to_saved(env, monkeypatch):
    urls: list[str] = []
    monkeypatch.setattr(picker.webbrowser, "open", urls.append)
    data = manifest_data()
    for row in data["rows"]:
        row["options"] = [{**o, "image": o["image"].replace("../art/", "")} for o in row["options"]]
    env.queue("001_art", "\n".join([
        "FAKE commit picks/a.png a", "FAKE commit picks/b.png b", "FAKE commit picks/c.svg <svg/>",
        "FAKE commit picks/d.png d", f"FAKE commit picks/m.json {json.dumps(data)}", "FAKE pick picks/m.json"]))
    env.tick_until(lambda: env.finished("code/001_art") and (env.repo / "picks" / "m.json").is_file())
    assert env.finished("code/001_art").detail["pick"] == REF
    assert picks_section(env) == [f"- code/001_art waiting for Matt — signal `pick: {REF}`"]
    send(env, f"by: app\npick: {REF}")
    [ev] = env.events("pick_open")
    assert ev.item == "code/001_art" and urls == [ev.detail["url"]]
    assert picks_section(env)[0].startswith("- code/001_art opened ")
    srv = env.d.picker
    assert post_save(srv, choice(logo="2"))[0] == 200
    env.tick_until(lambda: env.events("pick_saved"))
    assert env.events("pick_saved")[0].item == "code/001_art"
    assert picks_section(env)[0].startswith("- art_alts saved ")


# ---------------------------------------------------------------- `conductor pick`
def test_cli_with_daemon_alive_writes_the_signal(env):
    write_manifest(env.repo)
    lock = env.cfg.state_dir / "daemon.json"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": os.getpid(), "create_time": procs.create_time(os.getpid()),
                                "started": stamp()}), encoding="utf-8")
    assert cli.main(["pick", REF, "--control", str(env.control)]) == 0
    [sig] = (env.control / "signals").glob("cli-*.signal")
    assert sig.read_text(encoding="utf-8") == f"by: cli\npick: {REF}\n"


def test_cli_bad_ref_exits_1(env, capsys):
    write_manifest(env.repo)
    assert cli.main(["pick", "code/picks/none.json", "--control", str(env.control)]) == 1
    assert cli.main(["pick", "elsewhere/m.json", "--control", str(env.control)]) == 1
    assert "no manifest file" in capsys.readouterr().err
    assert not list((env.control / "signals").glob("*.signal"))
    # Red like the signal's `invalid`, so the inbox and the app show the refusal.
    fails = env.events("pick_failed")
    assert [e.detail["manifest"] for e in fails] == ["code/picks/none.json", "elsewhere/m.json"]
    assert "no manifest file" in fails[0].detail["error"] and fails[0].detail["by"] == "cli"


def test_cli_until_saved_serves_in_the_foreground(env, monkeypatch):
    urls: queue.SimpleQueue = queue.SimpleQueue()
    monkeypatch.setattr(picker.webbrowser, "open", urls.put)
    write_manifest(env.repo)
    result: list[int] = []
    t = threading.Thread(target=lambda: result.append(
        cli.main(["pick", REF, "--control", str(env.control), "--until-saved"])), daemon=True)
    t.start()
    url = urls.get(timeout=20)
    port, token = int(url.split(":")[2].split("/")[0]), url.split("/")[3]

    class Srv:                                          # what request() needs of a server
        pass
    srv = Srv()
    srv.port, srv.token = port, token
    assert post_save(srv, choice(hero="1"))[0] == 200
    t.join(timeout=20)
    assert result == [0]
    [op] = env.events("pick_open")
    assert op.detail["by"] == "cli"
    [sv] = env.events("pick_saved")
    assert sv.detail["changed"] == 1
