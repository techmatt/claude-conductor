"""`farm-seeds` end to end against an in-process broker and local-remote workers, with
`fake_sim.py` as the project's native command."""

import json
import threading
import time

import pytest

from conductor.remote import broker, client, settings, worker
from conductor.remote.board import Board

from .conftest import git, init_repo, remote_section


@pytest.fixture
def farm(tmp_path, monkeypatch):
    """A control folder with `[remote]`, a code repo, and a broker serving on a free port."""
    control, repo = tmp_path / "control", tmp_path / "code"
    init_repo(repo, {"README.md": "x\n"})

    class F:
        servers, stops = [], []

        def setup(self, **over):
            control.mkdir(exist_ok=True)
            (control / "conductor.toml").write_text('project = "t"\n[repos]\ncode = "../code"\n' + remote_section(**over),
                                                    encoding="utf-8")
            self.r = settings.load(control)
            self.board = Board(self.r.state, self.r.lease_seconds, self.r.fresh_seconds, self.r.bundle_js,
                               native_argv=self.r.native)
            server = broker.make_server(self.r, self.board)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.servers.append(server)
            port = server.server_address[1]
            self.r.state.mkdir(parents=True, exist_ok=True)
            (self.r.state / "broker.json").write_text(json.dumps(
                {"pid": 0, "local": f"http://127.0.0.1:{port}/w/{self.r.secret()}/"}), encoding="utf-8")
            monkeypatch.setenv(settings.CONTROL_VAR, str(control))
            monkeypatch.chdir(repo)
            return self

        def worker(self, wid, **kw):
            stop = threading.Event()
            self.stops.append(stop)
            threading.Thread(target=worker.work, args=(client.Broker.find(self.r), wid, kw.pop("cores", 1),
                                                       self.r.fingerprint), kwargs={**kw, "stop": stop},
                             daemon=True).start()
            end = time.time() + 10
            while not any(w.id == wid for w in self.board.live()) and time.time() < end:
                time.sleep(0.02)

        def run(self, first, count, *args, out="out.txt"):
            code = client.main([str(first), str(count), str(tmp_path / out), *args])
            return code, (tmp_path / out).read_bytes() if (tmp_path / out).exists() else None

        def local(self, first, count, *args):
            path = tmp_path / "local.txt"
            client.run_native(self.r.native, repo, first, count, path, list(args))
            return path.read_bytes()

        def record(self):
            return json.loads(max((self.r.state / "jobs").glob("*/job.json"), key=lambda p: p.stat().st_mtime)
                              .read_text(encoding="utf-8"))

    f = F()
    yield f
    for s in f.stops:
        s.set()
    for s in f.servers:
        s.shutdown()


def test_a_job_splits_across_the_main_box_and_a_worker_and_merges_byte_identical(farm, capsys):
    farm.setup()
    farm.worker("lr1", cores=2)
    code, out = farm.run(3, 95, "--sleep", "0.01")
    assert code == 0
    assert out == farm.local(3, 95)
    rec = farm.record()
    assert [s["first"] for s in rec["shards"]] == list(range(3, 98, 10))
    assert {s["worker"] for s in rec["shards"]} == {"main", "lr1"}
    assert rec["engines"] == ["native"] and rec["mixed"] is False
    assert "lr1" in "".join(p.name for p in (farm.r.state / "jobs").glob("*/logs/*.log"))
    assert "local-remote lr1" in capsys.readouterr().err


def test_output_bytes_are_kept_exactly(farm):
    farm.setup()
    farm.worker("lr1")
    code, out = farm.run(0, 40, "--crlf", "--sleep", "0.005")
    assert code == 0 and b"\r\n" in out and out == farm.local(0, 40, "--crlf")


def test_an_argument_with_a_space_stays_local(farm, capsys):
    farm.setup()
    farm.worker("lr1")
    assert farm.run(0, 40, "a b")[0] == 0
    assert "holds a space" in capsys.readouterr().err


def test_with_no_worker_the_main_box_runs_every_shard(farm, capsys):
    farm.setup(main_slots="3")
    code, out = farm.run(0, 50)
    assert (code, out) == (0, farm.local(0, 50))
    rec = farm.record()
    assert len(rec["shards"]) == 5 and {s["worker"] for s in rec["shards"]} == {"main"}
    assert rec["references"] == {"native": "fp-1234", "wasm": None, "differ": False}
    assert "main box 5 (native)" in capsys.readouterr().err


def test_a_worker_joining_mid_job_takes_pending_shards(farm):
    farm.setup()
    threading.Timer(0.5, farm.worker, args=("late",), kwargs={"cores": 2}).start()
    code, out = farm.run(0, 120, "--sleep", "0.01")    # 12 shards of 0.1 s; the main box alone: ~1.2 s
    assert (code, out) == (0, farm.local(0, 120))
    assert "late" in {s["worker"] for s in farm.record()["shards"]}


def test_a_small_job_and_a_dead_broker_stay_local(farm, capsys):
    farm.setup(seed_seconds="0.01")
    farm.worker("lr1")
    assert farm.run(0, 50)[0] == 0
    assert "under 5s" in capsys.readouterr().err
    farm.setup()
    (farm.r.state / "broker.json").write_text(json.dumps({"local": "http://127.0.0.1:9/w/x/"}), encoding="utf-8")
    t = time.time()
    code, out = farm.run(0, 50)
    assert time.time() - t < 1.5 and code == 0 and out == farm.local(0, 50)
    assert "broker unreachable" in capsys.readouterr().err


def test_a_worker_that_goes_quiet_holding_shards_costs_only_a_rerun(farm):
    farm.setup()
    farm.worker("lr1", hang_after=1)            # takes a second shard and never answers
    code, out = farm.run(0, 60, "--sleep", "0.01")
    assert (code, out) == (0, farm.local(0, 60, "--sleep", "0.01"))
    assert all(s["worker"] in ("main", "lr1") for s in farm.record()["shards"])


def test_a_worker_failing_the_fingerprint_gate_gets_nothing_and_is_reported(farm, capsys):
    farm.setup()
    farm.worker("liar", bad_fingerprint=True)
    code, out = farm.run(0, 40, "--sleep", "0.01")
    assert (code, out) == (0, farm.local(0, 40))
    rec = farm.record()
    assert {s["worker"] for s in rec["shards"]} == {"main"}
    assert rec["gate_failures"][0]["worker"] == "liar"
    assert "liar failed the fingerprint gate" in capsys.readouterr().err


def test_failed_shards_on_a_worker_are_run_elsewhere(farm):
    farm.setup()
    farm.worker("flaky", fail_every=2, cores=2)
    code, out = farm.run(0, 80, "--sleep", "0.005")
    assert (code, out) == (0, farm.local(0, 80))


def test_a_native_failure_fails_the_job(farm, capsys):
    farm.setup()
    farm.worker("lr1")
    code, _ = farm.run(0, 40, "--fail")
    assert code != 0
    assert "failed" in capsys.readouterr().err


@pytest.mark.slow           # a call caught mid-shutdown waits out its socket timeout
def test_the_broker_dying_mid_job_finishes_the_rest_locally(farm, capsys, monkeypatch):
    monkeypatch.setattr(client, "PATIENCE", 1.0)        # a broker that stays gone, after the wait for a restart
    farm.setup()
    farm.worker("lr1")
    server = farm.servers[-1]

    def die():
        server.shutdown()
        server.server_close()
    threading.Timer(0.4, die).start()
    code, out = farm.run(0, 100, "--sleep", "0.01")
    assert (code, out) == (0, farm.local(0, 100))
    assert "broker went away" in capsys.readouterr().err


def test_the_page_and_bundles_need_the_token_and_main_box_calls_need_loopback(farm):
    import urllib.error
    import urllib.request
    farm.setup()
    base = client.Broker.find(farm.r).url
    assert b"Conductor worker" in urllib.request.urlopen(base).read()
    port = base.split(":")[2].split("/")[0]
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/w/wrong-token/")
    assert e.value.code == 404
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(base + "bundle/..%2f..%2fx/conductor.toml")
    bundle = farm.r.state / "bundles" / "k1"
    bundle.mkdir(parents=True)
    (bundle / "b.wasm").write_bytes(b"\0asm")
    resp = urllib.request.urlopen(base + "bundle/k1/b.wasm")
    assert resp.headers["Content-Type"] == "application/wasm"
    for name in ("farm.mjs", "farm.cjs", "farm.js"):          # a module script of another type won't load
        (bundle / name).write_text("export {}\n", encoding="utf-8")
        assert urllib.request.urlopen(base + f"bundle/k1/{name}").headers["Content-Type"].startswith("text/javascript")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(base + "bundle/k1/NUL")
    assert e.value.code == 404

    def post(op, body, ctype="application/json", **headers):
        req = urllib.request.Request(base + "api/" + op, data=body, method="POST",
                                     headers={"Content-Type": ctype, **headers})
        try:
            return urllib.request.urlopen(req, timeout=5).status
        except urllib.error.HTTPError as err:
            return err.code
    assert post("status", b"{}") == 200
    assert post("status", b"{}", "text/plain") == 415                  # no simple cross-origin POST
    assert post("job", b"{}", Origin="http://evil.example") == 403     # a page on this box can't drive it
    assert post("hello", b"[1]") == 400
    req = urllib.request.Request(base + "api/hello", method="POST", headers={"Content-Type": "application/json"},
                                 data=json.dumps({"worker": "tab", "kind": "browser", "view": True}).encode())
    reply = json.loads(urllib.request.urlopen(req, timeout=5).read())
    assert reply["page"] == {"project": "t", "accent": settings.ACCENT, "version": broker.page_version()} and reply["view"]["job"] is None
    assert post("result", b'{"worker": "x", "job": "j", "shard": 0, "ok": true}') == 400   # no output
    import socket
    with socket.create_connection(("127.0.0.1", int(port)), timeout=5) as s:   # a negative length
        s.sendall(f"POST /w/{farm.r.secret()}/api/poll HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                  "Content-Length: -1\r\n\r\n".encode())
        assert b" 413 " in s.recv(200)


def test_a_build_runs_once_per_key_and_is_cached(farm, tmp_path):
    py = (tmp_path / "build.py")
    py.write_text("import sys, pathlib\np = pathlib.Path(sys.argv[1]); p.mkdir(parents=True)\n"
                  "(p / 'bundle.js').write_text('export default 1')\n"
                  "open(pathlib.Path(sys.argv[2]), 'a').write('built\\n')\n", encoding="utf-8")
    count = tmp_path / "builds.txt"
    import sys
    farm.setup(build=f'["{sys.executable.replace(chr(92), "/")}", "{py.as_posix()}", "{{bundle}}", "{count.as_posix()}"]')
    root, key = client.repo_key(tmp_path / "code")
    assert client.build_bundle(farm.r, key, root) == 0
    assert client.build_bundle(farm.r, key, root) == 0
    assert count.read_text() == "built\n"
    assert (farm.r.state / "bundles" / key / ".ok").exists()
    (tmp_path / "code" / "README.md").write_text("changed\n")
    assert client.repo_key(tmp_path / "code")[1].startswith(key + "-")      # a dirty tree is its own key
    git(tmp_path / "code", "checkout", "README.md")


def test_no_build_starts_while_no_browser_is_connected(farm, tmp_path):
    import sys
    count = tmp_path / "builds.txt"
    farm.setup(build=f'["{sys.executable.replace(chr(92), "/")}", "-c", '
                     f'"open(r\'{count.as_posix()}\', \'a\').write(\'built\')", "{{bundle}}"]')
    farm.worker("lr1")
    assert farm.run(0, 60, "--sleep", "0.01")[0] == 0
    time.sleep(0.5)
    assert not count.exists() and not (farm.r.state / "bundles").exists()


@pytest.mark.skipif(not __import__("shutil").which("node"), reason="node is not installed")
def test_the_wasm_reference_is_the_bundles_own_fingerprint_run_in_node(farm):
    farm.setup()
    bundle = farm.r.state / "bundles" / "k1"
    bundle.mkdir(parents=True)
    (bundle / "x_bg.wasm").write_bytes(b"\0asm\1\0\0\0")
    (bundle / "bundle.js").write_text(
        "let got;\nexport default async function init(o) { got = o.module_or_path.length; }\n"
        "export function fingerprint() { return `wasm-${got}`; }\n", encoding="utf-8")
    assert client.wasm_reference(farm.r, "k1") == ("wasm-8", "")
    (bundle / "bundle.js").write_text("export default 1", encoding="utf-8")
    assert client.wasm_reference(farm.r, "k1") == ("wasm-8", "")          # cached beside the bundle
    other = farm.r.state / "bundles" / "k2"
    other.mkdir()
    (other / "bundle.js").write_text("export default async () => {};\nthrow new Error('boom');\n", encoding="utf-8")
    value, why = client.wasm_reference(farm.r, "k2")
    assert value == "" and "boom" in why
    # A bundle whose init(input) wraps its argument itself and keeps its first promise
    # (aicomplete's farm.mjs): the object form rejects, and that rejection is what the same
    # module's init hands back ever after, so the raw bytes need a fresh import (96).
    wraps = farm.r.state / "bundles" / "k3"
    wraps.mkdir()
    (wraps / "farm_bg.wasm").write_bytes(b"\0asm\1\0\0\0")
    (wraps / "farm.mjs").write_text(
        "let got, ready;\nasync function inner({ module_or_path: m }) {\n"
        "  if (!(m instanceof Uint8Array)) throw new TypeError('Argument 0 must be a buffer source');\n"
        "  got = m.length;\n}\n"
        "export default function init(input) { return ready ??= inner({ module_or_path: input }); }\n"
        "export function fingerprint() { return `wrapped-${got}`; }\n", encoding="utf-8")
    farm.r.bundle_js = "farm.mjs"
    assert client.wasm_reference(farm.r, "k3") == ("wrapped-8", "")


@pytest.mark.slow           # waits out a broker gap: 7s serially
def test_a_broker_restarting_mid_job_loses_nothing(farm, capsys):
    """A handover in miniature: the old broker stops taking calls, saves its board and lets go
    of the port; a new one takes both up. `farm-seeds` and the worker wait out the gap."""
    farm.setup()
    farm.worker("lr1")
    old = farm.servers[-1]
    port = old.server_address[1]
    carried = []

    def restart():
        old.RequestHandlerClass.door.close()
        old.shutdown()
        old.server_close()
        farm.board.save(farm.r.state / broker.BOARD)
        time.sleep(1.0)                                   # the gap
        b2 = Board(farm.r.state, farm.r.lease_seconds, farm.r.fresh_seconds, farm.r.bundle_js,
                   native_argv=farm.r.native)
        carried.append(b2.restore(farm.r.state / broker.BOARD) and len(b2.jobs))
        new = broker.make_server(farm.r, b2, port=port)
        threading.Thread(target=new.serve_forever, daemon=True).start()
        farm.servers.append(new)
    threading.Timer(0.5, restart).start()
    code, out = farm.run(0, 300, "--sleep", "0.01")
    assert (code, out) == (0, farm.local(0, 300))
    err = capsys.readouterr().err
    assert carried == [1]                                 # the job was open, and the new broker took it up
    assert "broker went away" not in err and "running locally" not in err
    assert len(farm.record()["shards"]) == 30


def test_the_code_watch_hands_over_only_on_committed_code_that_imports(tmp_path):
    v = ["a"]
    blockers, broken = [], []
    w = broker.CodeWatch(probe=lambda: v[0], blockers=lambda: blockers, imports=lambda cwd: broken[0] if broken else None)
    w.at -= 60
    assert w.due(tmp_path) is None                        # unchanged
    v[0], w.at = "b", w.at - 60
    blockers.append("uncommitted")
    assert w.due(tmp_path) is None
    blockers.clear()
    broken.append("SyntaxError")
    w.at -= 60
    assert w.due(tmp_path) is None and "b" in w.refused   # refused for a while
    broken.clear()
    w.at -= 60
    assert w.due(tmp_path) is None
    w.refused.clear()
    w.at -= 60
    assert w.due(tmp_path) == "b"
    assert broker.CodeWatch(probe=lambda: None).due(tmp_path) is None   # not a checkout: never


def test_the_join_url_names_this_pc_unless_host_is_set(farm):
    import socket
    farm.setup()
    assert broker.join_host(farm.r) in (socket.gethostname(), broker.lan_ip())
    farm.setup(host='"192.168.9.9"')
    assert broker.join_host(farm.r) == "192.168.9.9"


def test_a_now_line_held_by_a_reader_never_kills_the_broker(farm, monkeypatch):
    farm.setup()
    path = farm.r.control.joinpath(*settings.NOW_LINE)
    broker._write_now(farm.r, "one")

    def refused(*a, **kw):
        raise PermissionError(5, "Access is denied")      # Windows, while the inbox reads it
    monkeypatch.setattr(broker.os, "replace", refused)
    broker._write_now(farm.r, "two")
    monkeypatch.setattr(type(path), "unlink", refused)
    broker._write_now(farm.r, None)
    assert path.read_text(encoding="utf-8") == "one\n"     # kept until the next beat
