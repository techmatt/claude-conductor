"""124: the built-in worker test, per-worker tallies, counts that survive a reconnect, and
the farm reminder for a `remote` item running the native sim directly."""

import json
import shutil
import subprocess

import pytest

from conductor import rules, talk
from conductor.remote import selftest
from conductor.remote.board import WORKER_FORGET_SECONDS, Board

from .test_remote_board import board, gated, job


def test_the_shortcut_matches_the_loop_and_the_bundle():
    assert selftest.expected(3, 2) == f"3 {selftest.one(3)}\n4 {selftest.one(4)}\n"
    assert selftest.reference() == f"0 {selftest.one(0)}"
    assert selftest.key().startswith(selftest.PREFIX) and selftest.is_test(selftest.key())
    node = shutil.which("node")
    if node:                                        # the browser's arithmetic, bit for bit
        url = selftest.SOURCE.resolve().as_uri()
        out = subprocess.run([node, "--input-type=module", "-e",
                              f"import('{url}').then(m => process.stdout.write(m.run(7, 3, '') + '|' + m.fingerprint()))"],
                             capture_output=True, text=True, timeout=60).stdout
        assert out == selftest.expected(7, 3) + "|" + selftest.reference()


@pytest.mark.slow
def test_the_main_box_loop_is_the_real_cost():
    assert selftest.slow(5) == selftest.one(5)


def test_a_test_job_checks_every_copy_and_tallies_each_worker(tmp_path):
    b, clock = board(tmp_path, lease=600)
    k = selftest.key()
    jid = b.add_job(k, selftest.reference(), [], [(0, 2), (2, 2), (4, 2)], None, "worker test")
    b.set_bundle(k, "ready", wasm=selftest.reference())
    b.hello("tab", "browser", cores=2)
    t = b.poll("tab")
    assert (t["task"], t["module"]) == ("gate", "selftest.js")
    assert b.gate("tab", k, selftest.reference())
    s0 = b.poll("tab")
    assert s0["task"] == "shard" and s0["module"] == "selftest.js"
    assert b.result("tab", jid, s0["shard"], True, selftest.expected(s0["first"], s0["count"]).encode(), ms=40)
    s1 = b.poll("tab")
    assert not b.result("tab", jid, s1["shard"], True, b"wrong\n", ms=40)      # a wrong copy never lands
    while "shard" in (c := b.claim(jid)):             # the main box runs what is left (tab failed one)
        b.result("main", jid, c["shard"], True, selftest.expected(c["first"], c["count"]).encode(), ms=900)
    st = b.state(jid)
    assert st["complete"]
    tally = st["tally"]
    assert tally["tab"] == {"offered": 2, "finished": 1, "ms": 40, "credited": 1, "mismatched": 1}
    assert tally["main"]["credited"] == 2 and st["labels"]["main"] == "main box"
    rows = {w["id"]: w for w in selftest.rows(tally, st["labels"])}
    assert rows["tab"]["s_shard"] == 0.04 and "1 did not match" in selftest.line(rows["tab"])


def test_the_main_box_overtaking_a_held_shard_is_counted(tmp_path):
    b, clock = board(tmp_path, lease=600)
    jid = job(b, n=1)
    gated(b, "lr")
    t = b.poll("lr")
    b.claim(jid)                                      # nothing free: a tail copy of lr's shard
    b.result("main", jid, t["shard"], True, b"x", ms=5)
    assert b.state(jid)["tally"]["lr"]["overtaken"] == 1
    assert not b.result("lr", jid, t["shard"], True, b"x", ms=9)            # lr's copy came second
    assert b.state(jid)["tally"]["lr"]["finished"] == 1


def test_a_worker_keeps_its_counts_across_an_hours_silence_and_a_restart(tmp_path):
    # aicomplete's b370663d983 won 35 shards at 4:25-4:54am and rejoined at 8:31am with 0.
    b, clock = board(tmp_path)
    jid = job(b, n=2)
    gated(b, "tab", kind="local-remote")
    for _ in range(2):
        t = b.poll("tab")
        assert b.result("tab", jid, t["shard"], True, b"x", ms=5)
    first = b.workers["tab"].first_seen
    clock.t += WORKER_FORGET_SECONDS + 600
    b.hello("other", "local-remote")                  # anyone's hello forgets the silent one
    assert "tab" not in b.workers and b.past["tab"]["shards"] == 2
    b.save(tmp_path / "board.json")
    b2 = Board(tmp_path, clock=clock)
    assert b2.restore(tmp_path / "board.json") and b2.past["tab"]["shards"] == 2
    b2.hello("tab", "local-remote")
    w = b2.workers["tab"]
    assert (w.shards, w.first_seen) == (2, first) and "tab" not in b2.past


def test_a_remote_item_running_the_native_sim_gets_one_reminder(tmp_path):
    native, flags = ["aic-sim"], ["--seed", "--games"]
    start = {"tool_name": "mcp__conductor__start", "tool_input": {
        "command": "out/249_x/sim.exe experiment spec.json --seed 48568 --games 4 --threads 6 --out out/keep/r"}}
    assert rules.native_run(start, native, flags).startswith("out/249_x/sim.exe")
    for quiet in ("farm-seeds 1 50 out/x p248", "cargo build --release -p aic-sim",
                  "target/release/aic-sim.exe farm --fingerprint", "python scripts/x.py --seed 4"):
        assert rules.native_run({"tool_name": "Bash", "tool_input": {"command": quiet}}, native, flags) is None, quiet
    direct = {"tool_name": "Bash", "tool_input": {"command": "target/release/aic-sim.exe farm p --seed 1 --games 9 --out o"}}
    out = json.loads(talk.hook(tmp_path, "code/001_a", start, native, flags))
    assert "`farm-seeds <first seed> <count> <out file>" in out["hookSpecificOutput"]["additionalContext"]
    assert talk.hook(tmp_path, "code/001_a", direct, native, flags) is None             # once per item
    assert talk.hook(tmp_path, "code/001_a", direct) is None                             # not a remote item
    log = talk.farm_file(tmp_path, "code/001_a").read_text(encoding="utf-8").splitlines()
    assert len(log) == 2 and json.loads(log[0])["tool"] == "mcp__conductor__start"


def test_the_workers_tab_line_and_the_digest_show_the_last_test(tmp_path):
    from conductor.remote import app, diag
    T = 1794858249.0
    assert app.test_text(None, 0).startswith("Test workers runs")
    assert "running since" in app.test_text({"at": T, "running": True}, T + 10)
    done = {"at": T, "ok": True, "seconds": 5.2, "note": "", "workers": selftest.rows(
        {"main": {"offered": 1, "finished": 1, "credited": 1, "ms": 5100},
         "t1": {"offered": 39, "finished": 39, "credited": 39, "ms": 780}}, {"main": "main box", "t1": "Chrome"})}
    text = app.test_text(done, T + 100)
    assert ": passed in 5 s" in text and "Chrome: offered 39, finished 39, credited 39, 0.02 s a shard; all matched" in text
    (tmp_path / selftest.RESULT).write_text(json.dumps(done), encoding="utf-8")
    assert "- Chrome: offered 39" in diag.render("p", {"workers": []}, tmp_path, now=T + 100)


def test_the_hook_is_told_the_native_sim_and_its_seed_flags(tmp_path):
    from conductor import remote
    (tmp_path / "conductor.toml").write_text(
        'project = "p"\n[remote]\nnative = ["target/release/aic-sim.exe", "farm", "{args}", "--seed", "{first}", '
        '"--games", "{count}", "--out", "{out}"]\nfingerprint = ["target/release/aic-sim.exe", "--fp"]\n', encoding="utf-8")
    names, flags = remote.native_hint(tmp_path)
    assert (names, flags) == (["aic-sim"], ["--seed", "--games"])
    args = json.loads(talk.hook_settings("py", tmp_path, "code/001_a", native=names, flags=flags))["hooks"]["PostToolUse"][0]["hooks"][0]["args"]
    assert args[-3:] == ["--native=aic-sim", "--native-flag=--seed", "--native-flag=--games"]
    assert remote.native_hint(tmp_path / "nowhere") == ([], [])
