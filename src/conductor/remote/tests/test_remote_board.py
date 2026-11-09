"""The broker's board: gates, leases, re-runs and first-wins, on a hand-driven clock."""

import math

from conductor.remote.board import Board


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def board(tmp_path, **kw):
    clock = Clock()
    return Board(tmp_path, lease_seconds=kw.get("lease", 30), fresh_seconds=kw.get("fresh", 5), native_argv=["sim"],
                 clock=clock), clock


def job(b, n=3, key="k1", fp="fp"):
    return b.add_job(key, fp, ["--x"], [(i * 10, 10) for i in range(n)], ".")


def gated(b, wid, kind="local-remote", key="k1"):
    b.hello(wid, kind)
    assert b.poll(wid)["task"] == "gate"
    assert b.gate(wid, key, "fp")


def test_gate_comes_first_and_a_failure_gets_no_shards(tmp_path):
    b, _ = board(tmp_path)
    jid = job(b)
    b.hello("good", "local-remote")
    b.hello("bad", "local-remote")
    assert b.poll("good")["task"] == "gate"
    assert b.poll("good")["task"] == "idle"          # its gate is pending
    assert b.gate("good", "k1", "fp")
    t = b.poll("good")
    assert (t["task"], t["first"], t["args"], t["native"]) == ("shard", 0, ["--x"], {"cwd": ".", "argv": ["sim"]})
    assert b.poll("bad")["task"] == "gate"
    assert not b.gate("bad", "k1", "nope")
    assert b.poll("bad")["task"] == "idle"
    assert b.state(jid)["gate_failures"][0]["got"] == "nope"
    assert "nope" in (tmp_path / "gates.log").read_text(encoding="utf-8")
    assert [w.id for w in b.live("k1")] == ["good"]
    assert "1 failed the fingerprint gate" in b.now_line()


def test_a_browser_waits_for_the_bundle(tmp_path):
    b, _ = board(tmp_path)
    job(b)
    b.hello("tab", "browser", cores=4)
    assert b.poll("tab")["task"] == "idle"
    b.set_bundle("k1", "ready")
    t = b.poll("tab")
    assert (t["task"], t["bundle"]) == ("gate", "bundle/k1/")
    assert "native" not in t


def test_an_ungated_worker_is_refused_and_writes_no_log(tmp_path):
    b, _ = board(tmp_path)
    jid = job(b)
    b.hello("w", "local-remote")
    assert not b.result("w", jid, 0, True, b"x", log="spam")
    assert not b.state(jid)["done"]
    assert not (b.jobs[jid].folder / "logs").exists()


def test_lease_timeout_and_a_silent_worker_release_shards(tmp_path):
    b, clock = board(tmp_path, lease=30, fresh=5)
    jid = job(b, n=1)
    gated(b, "a")
    gated(b, "c")
    assert b.poll("a")["task"] == "shard"
    clock.t += 3
    b.state(jid)                                      # the main box's heartbeat
    b.hello("c", "local-remote")
    assert b.poll("c")["task"] == "idle"              # a's lease is live
    clock.t += 3                                      # a not heard from in 6s > fresh 5s
    b.state(jid)
    assert b.poll("c")["task"] == "shard"
    for _ in range(8):                                # c's lease times out though c still polls
        clock.t += 4
        b.state(jid)
        b.hello("c", "local-remote")
    b.hello("a", "local-remote")
    assert b.poll("a")["task"] == "shard"


def test_the_main_box_reruns_a_shard_out_on_a_worker_and_the_first_copy_wins(tmp_path):
    b, _ = board(tmp_path)
    jid = job(b, n=2)
    gated(b, "w")
    assert b.poll("w")["shard"] == 0
    c = b.claim(jid)
    assert (c["shard"], c["rerun"]) == (1, False)
    assert b.result("main", jid, 1, True, b"one\n")
    c = b.claim(jid)
    assert (c["shard"], c["rerun"]) == (0, True)      # dry: re-run the worker's shard
    assert b.claim(jid) == {"wait": True}             # nothing else to take
    assert b.result("main", jid, 0, True, b"zero\r\n")
    assert not b.result("w", jid, 0, True, b"late\n")  # the worker's copy loses
    assert b.claim(jid) == {"done": True}
    s = b.state(jid)
    assert s["complete"] and [x["worker"] for x in s["shards"]] == ["main", "main"]
    assert (b.jobs[jid].folder / "shards" / "000000000000.out").read_bytes() == b"zero\r\n"   # bytes kept


def test_a_non_finite_time_never_leaves_a_shard_half_done(tmp_path):
    b, _ = board(tmp_path)
    jid = job(b, n=1)
    gated(b, "w")
    b.poll("w")
    assert b.result("w", jid, 0, True, b"x\n", ms=math.nan)
    assert b.claim(jid) == {"done": True}


def test_a_failed_result_frees_the_shard_and_logs_come_back(tmp_path):
    b, _ = board(tmp_path)
    jid = job(b, n=1)
    gated(b, "w")
    b.poll("w")
    assert not b.result("w", jid, 0, False, log="boom")
    assert "boom" in (b.jobs[jid].folder / "logs" / "w.log").read_text(encoding="utf-8")
    assert b.claim(jid)["shard"] == 0


def test_an_abandoned_job_stops_being_served_then_goes(tmp_path):
    b, clock = board(tmp_path, lease=30, fresh=5)
    old = job(b, n=2)
    clock.t += 1
    new = job(b, n=2, key="k2")
    gated(b, "w", key="k1")
    clock.t += 6                                       # old's main box went quiet; new's still beats
    b.state(new)
    b.hello("w", "local-remote")
    t = b.poll("w")
    assert (t["task"], t["key"]) == ("gate", "k2")
    clock.t += 30
    b.state(new)
    b.hello("w", "local-remote")
    b.poll("w")
    assert old not in b.jobs and new in b.jobs


def test_now_line_only_while_a_worker_is_connected(tmp_path):
    b, clock = board(tmp_path, fresh=5)
    assert b.now_line() is None
    b.hello("tab", "browser", cores=7)
    b.hello("lr", "local-remote", cores=2)
    assert b.now_line() == "remote workers: 2 connected (1 browser, 1 local-remote), 9 cores"
    clock.t += 6
    assert b.now_line() is None


def test_a_new_fingerprint_for_a_key_drops_old_gates(tmp_path):
    b, _ = board(tmp_path)
    job(b)
    gated(b, "w")
    job(b, fp="fp2")
    assert b.poll("w")["task"] == "gate"


def test_each_kind_is_gated_against_its_own_reference(tmp_path):
    b, _ = board(tmp_path)
    jid = job(b, fp="native-fp")
    b.set_bundle("k1", "ready", wasm="wasm-fp")
    b.hello("tab", "browser")
    b.hello("lr", "local-remote")
    assert b.poll("tab")["task"] == "gate" and b.poll("lr")["task"] == "gate"
    assert not b.gate("tab", "k1", "native-fp")        # a browser reproducing the native answer is wrong
    assert b.gate("lr", "k1", "native-fp")
    note = b.state(jid)["gate_failures"][0]
    assert (note["reference"], note["expected"]) == ("wasm", "wasm-fp")
    b.hello("tab2", "browser")
    assert b.poll("tab2")["task"] == "gate" and b.gate("tab2", "k1", "wasm-fp")
    assert b.poll("tab2")["task"] == "shard"
    assert b.state(jid)["fingerprints"] == {"native": "native-fp", "wasm": "wasm-fp"}
    job(b, fp="native-fp2")                            # a new native answer keeps the bundle's
    assert b.state(jid)["fingerprints"]["wasm"] == "wasm-fp"
    b.set_bundle("k1", "ready", wasm="wasm-fp3")       # a new wasm answer re-gates browsers only
    assert b.poll("tab2")["task"] == "gate"


def test_without_a_wasm_reference_a_browser_meets_the_native_one(tmp_path):
    b, _ = board(tmp_path)
    jid = job(b)
    b.set_bundle("k1", "ready")
    b.hello("tab", "browser")
    assert b.poll("tab")["task"] == "gate" and b.gate("tab", "k1", "fp")
    assert b.state(jid)["fingerprints"] == {"native": "fp", "wasm": None}
