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


def test_the_page_view_counts_the_job_and_compares_rates(tmp_path):
    b, clock = board(tmp_path)
    tab = "tab"
    assert b.view(tab)["job"] is None
    jid = b.add_job("k1", "fp", [], [(i * 10, 10) for i in range(4)], ".", item="code/160_sim")
    b.set_bundle("k1", "ready", wasm="wasm-fp")
    b.hello(tab, "browser", cores=3, label="Edge on Win32")
    b.hello("lr", "local-remote", cores=2)
    assert b.poll(tab)["task"] == "gate" and b.gate(tab, "k1", "wasm-fp")
    t = b.poll(tab)
    clock.t += 10
    assert b.result(tab, jid, t["shard"], True, b"x\n", ms=600.0)          # 60 ms a seed
    c = b.claim(jid)
    assert b.result("main", jid, c["shard"], True, b"y\n", ms=500.0)       # 50 ms a seed
    b.poll(tab)                                                           # one shard out on the tab
    b.hello("lr", "local-remote", cores=2)                               # still heard from
    v = b.view(tab)
    j = v["job"]
    assert (j["item"], j["shards"], j["done"], j["mine"], j["out"], j["pending"]) == ("code/160_sim", 4, 2, 1, 1, 1)
    assert (j["seeds"], j["seeds_done"], j["eta"]) == (40, 20, 10.0)
    assert v["gate"] == {"key": "k1", "state": "pass", "reference": "wasm", "value": "wasm-fp"}
    assert v["main"]["ms_seed"] == 50.0
    assert [(w["id"], w["cores"]) for w in v["workers"]] == [("lr", 2)]  # the others, not the asker
    b.load = {"cpu": 40, "top": [], "text": "CPU 40%", "high": False}
    assert b.view(tab)["load"]["cpu"] == 40


def test_without_a_wasm_reference_a_browser_meets_the_native_one(tmp_path):
    b, _ = board(tmp_path)
    jid = job(b)
    b.set_bundle("k1", "ready")
    b.hello("tab", "browser")
    assert b.poll("tab")["task"] == "gate" and b.gate("tab", "k1", "fp")
    assert b.state(jid)["fingerprints"] == {"native": "fp", "wasm": None}


def test_a_saved_board_carries_jobs_leases_and_gates_to_a_new_broker(tmp_path):
    b, clock = board(tmp_path)
    jid = job(b)
    gated(b, "w1")
    s0 = b.poll("w1")["shard"]
    assert b.result("w1", jid, s0, True, b"x", ms=100)
    out = b.poll("w1")["shard"]                        # out on w1 when the broker restarts
    b.save(tmp_path / "board.json")
    b2 = Board(tmp_path, lease_seconds=30, fresh_seconds=5, native_argv=["sim"], clock=clock)
    assert b2.restore(tmp_path / "board.json")
    assert b2.result("w1", jid, out, True, b"y", ms=100)    # the shard in flight lands on the new one
    assert b2.state(jid)["done"] == [s0, out]
    assert b2.poll("w1")["task"] == "shard"            # gate kept: straight to the last shard
    assert b2.add_job("k1", "fp", [], [(0, 1)], ".") != jid  # ids never repeat
    assert not Board(tmp_path).restore(tmp_path / "missing.json")


def test_a_failed_worker_re_gates_on_a_fresh_hello_or_a_new_broker_under_the_same_id(tmp_path):
    b, clock = board(tmp_path)
    job(b)
    b.hello("w1", "browser")
    b.set_bundle("k1", "ready")
    assert b.poll("w1")["task"] == "gate"
    assert not b.gate("w1", "k1", "wrong")             # refused before a fix
    b.hello("w1", "browser")                           # a beat: still refused
    assert b.poll("w1")["task"] == "idle"
    b.hello("w1", "browser", fresh=True)               # the page reloaded, same worker id
    assert b.poll("w1")["task"] == "gate"
    assert b.gate("w1", "k1", "fp")
    assert b.poll("w1")["task"] == "shard"
    assert not b.gate("w1", "k1", "wrong again")
    b.save(tmp_path / "board.json")                    # a new broker (a code change) takes up the board
    b2 = Board(tmp_path, lease_seconds=30, fresh_seconds=5, native_argv=["sim"], clock=clock)
    assert b2.restore(tmp_path / "board.json")
    assert b2.poll("w1")["task"] == "gate"             # no new tab, no new id
    assert b2.gate("w1", "k1", "fp") and b2.poll("w1")["task"] == "shard"


def test_a_lease_is_sized_to_its_shard_and_renewed_while_its_worker_runs_it(tmp_path):
    """No `lease_seconds`: 161's tab took 2-6 min a shard against a flat 60 s lease, and the
    main box re-ran 33 of 48. A lease is LEASE_FACTOR times the shard at the slowest rate seen
    (or `seed_seconds`), and a worker that says it is still running the shard renews it."""
    clock = Clock()
    b = Board(tmp_path, fresh_seconds=5, native_argv=["sim"], clock=clock, seed_seconds=28)
    jid = b.add_job("k1", "fp", [], [(0, 4), (4, 4), (8, 4)], ".")
    job_ = b.jobs[jid]
    assert b.lease_for(job_, job_.shards[0]) == 4 * 4 * 28          # 448 s before any rate is known
    b.seed_seconds = 0.01                              # a guess far too low: the 60 s floor
    assert b.lease_for(job_, job_.shards[0]) == 60.0
    gated(b, "tab", kind="local-remote")
    gated(b, "old", kind="local-remote")
    assert b.poll("tab")["shard"] == 0 and b.poll("old")["shard"] == 1
    for _ in range(100):                               # 300 s: both heard from every 3 s
        clock.t += 3
        b.hello("tab", "local-remote")
        b.renew("tab", [[jid, 0]])                     # the tab says it is still at shard 0
        b.hello("old", "local-remote")                 # an old page says nothing of its shards
        b.state(jid)
    c = b.claim(jid, rerun=False)                      # old's lease ran out at 60 s; tab's held
    assert (c["shard"], c["rerun"]) == (1, True)
    assert b.claim(jid, rerun=False)["shard"] == 2
    assert b.claim(jid, rerun=False) == {"wait": True}
    assert b.claim(jid)["rerun"] is True and len(b.reruns) == 2   # shard 0 only at the tail
    b.renew("ghost", [[jid, 0], ["nope", 1], [jid, 99], "x"])     # nothing it holds: no change
    job_.rates["main"] = 30_000.0                      # a measured rate: 4 seeds at 30 s, 4 times
    assert b.lease_for(job_, job_.shards[0]) == 480.0
    b2 = Board(tmp_path, lease_seconds=60, fresh_seconds=5, clock=clock, seed_seconds=28)
    assert b2.lease_for(job_, job_.shards[0]) == 60    # an explicit lease_seconds wins


def test_the_page_is_told_to_drop_a_closed_jobs_shard_or_one_done_elsewhere(tmp_path):
    b, _ = board(tmp_path)
    jid = job(b, n=3)
    gated(b, "tab", kind="local-remote")
    s0, s1 = b.poll("tab")["shard"], b.poll("tab")["shard"]
    running = [[jid, s0], [jid, s1], ["nope"], "x"]
    assert b.view("tab", running)["drop"] == []
    assert b.result("main", jid, s1, True, b"m")       # the main box's copy of s1 is in
    assert b.view("tab", running)["drop"] == [[jid, s1]]
    b.close(jid)
    assert b.view("tab", running)["drop"] == [[jid, s0], [jid, s1]]
    assert b.view("tab")["drop"] == []


def test_one_state_for_a_browser_from_commit_to_job_end(tmp_path):
    """A farm job from the start, as every view sees one worker (97): the bundle waited for,
    built, the gate, the page's own download and compile, working, and idle again."""
    b, clock = board(tmp_path, fresh=10)
    b.hello("tab", "browser", cores=4, label="Matt's laptop")
    b.items = ["code/163_reread"]
    v = b.view("tab")
    assert (v["me"]["state"], v["job"], v["items"]) == ("idle", None, ["code/163_reread"])
    assert v["main"]["state"] == "idle"
    jid = job(b, key="abcdef1234")
    assert b.state_of("tab")["state"] == "preparing" and "waiting for the bundle" in b.state_of("tab")["detail"]
    clock.t += 1
    b.set_bundle("abcdef1234", "building")
    s = b.state_of("tab")
    assert (s["state"], s["step"], s["since"], s["key"]) == ("preparing", "building", 1001, "abcdef1234")
    assert s["detail"] == "the broker is building the bundle for abcdef1"
    assert b.view("tab")["job"]["bundle"] == {"state": "building", "error": "", "since": 1001, "note": ""}
    clock.t += 40
    b.hello("tab", "browser", cores=4, label="Matt's laptop")
    b.claim(jid, rerun=False)                                   # the main box's job stays alive (and runs a shard)
    b.set_bundle("abcdef1234", "ready", wasm="fp")
    assert b.state_of("tab")["state"] == "idle"                 # until it polls: the gate is next
    assert b.poll("tab")["task"] == "gate"
    assert (b.state_of("tab")["state"], b.state_of("tab")["step"]) == ("preparing", "gating")
    b.gate("tab", "abcdef1234", "fp")
    clock.t += 1
    b.hello("tab", "browser", cores=4, label="Matt's laptop",
            report={"state": "preparing", "step": "compiling", "key": "abcdef1234", "for": 3})
    s = b.state_of("tab")
    assert (s["state"], s["step"], s["since"]) == ("preparing", "compiling", 1039)
    assert s["detail"] == "compiling the bundle for abcdef1"
    sid = b.poll("tab", label="Matt's laptop")["shard"]
    assert b.state_of("tab") | {"detail": ""} == {"state": "working", "out": 1, "detail": ""}
    b.claim(jid, rerun=False)
    assert b.view("tab")["main"]["state"] == "working"
    rows = {w["id"]: w for w in b.panel()["workers"]}
    assert rows["tab"]["state"] == "working" and rows["main"]["state"] == "working"
    b.hello("tab", "browser", cores=4, label="Matt's laptop")   # the report clears with the next hello
    assert b.result("tab", jid, sid, True, b"x", ms=100)
    assert b.jobs[jid].shards[sid].done["label"] == "Matt's laptop"
    b.close(jid)
    assert b.state_of("tab")["state"] == "idle" and b.view("tab")["main"]["state"] == "idle"
    clock.t += 11
    assert b.state_of("tab")["state"] == "offline"


def test_a_failed_build_a_stuck_build_and_a_core_error_are_errors(tmp_path):
    b, clock = board(tmp_path)
    job(b, key="k1")
    b.hello("tab", "browser")
    b.set_bundle("k1", "failed", error="build exit 101: error[E0425]: cannot find value `x`")
    s = b.state_of("tab")
    assert s["state"] == "error" and s["detail"].endswith("cannot find value `x`")
    b.set_bundle("k1", "building")
    clock.t += 20 * 60 + 1
    b.hello("tab", "browser")
    b.claim(next(iter(b.jobs)))                         # the main box keeps its job alive
    assert b.state_of("tab")["state"] == "error" and "over 20 min" in b.state_of("tab")["detail"]
    b2, _ = board(tmp_path / "b2")
    b2.hello("tab", "browser", report={"state": "error", "error": "core 2: load failed", "for": 1})
    assert b2.state_of("tab") | {"since": 0} == {"state": "error", "since": 0, "key": None, "detail": "core 2: load failed"}
    b2.hello("tab", "browser", report={"state": "draining", "for": 5})
    assert b2.state_of("tab")["state"] == "draining"
    b2.hello("tab", "browser", cores=0, report={"state": "paused"})
    assert b2.state_of("tab") == {"state": "idle", "detail": "paused"}
    b2.hello("tab", "browser", report={"state": "nonsense", "step": "x"})
    assert b2.state_of("tab")["state"] == "idle"


def test_a_kept_shard_names_who_ran_it_beside_its_output(tmp_path):
    import json
    b, _ = board(tmp_path)
    jid = job(b, n=2)
    gated(b, "lr", kind="local-remote")
    sid = b.poll("lr", label="spare box")["shard"]
    assert b.result("lr", jid, sid, True, b"w")
    assert not b.result("main", jid, sid, True, b"m")   # a re-run that lost is not the kept copy
    who = json.loads((b.jobs[jid].folder / "shards" / f"{sid * 10:012d}.who.json").read_text(encoding="utf-8"))
    assert (who["worker"], who["engine"], who["label"]) == ("lr", "native", "spare box")


def test_the_job_record_counts_the_kept_copies_even_with_the_broker_gone(tmp_path, capsys):
    """162: the broker went away before the end, so the record had no state and called every
    shard the main box's (1080 native and 360 wasm games read "main box 8"). It now reads the
    note kept beside each shard's output."""
    import json
    from conductor.remote import client
    from conductor.remote.board import write_who
    folder = tmp_path / "j1"
    ranges = [(0, 4), (4, 4), (8, 4)]
    for a, _ in ranges:
        (folder / "shards").mkdir(parents=True, exist_ok=True)
        client.shard_path(folder, a).write_bytes(b"x")
    write_who(client.shard_path(folder, 0), {"worker": "b1", "kind": "browser", "engine": "wasm", "label": "laptop", "ms": 9})
    write_who(client.shard_path(folder, 4), client.MAIN_DONE)
    client._record(folder, "k", [], ranges, {}, tmp_path / "out", 1.0)    # no state: the broker was gone
    rec = json.loads((folder / "job.json").read_text(encoding="utf-8"))
    assert [s["worker"] for s in rec["shards"]] == ["b1", "main", "main"]
    assert rec["engines"] == ["native", "wasm"] and rec["mixed"]
    assert "browser laptop (b1) 1 (wasm), main box 2 (native)" in capsys.readouterr().err
