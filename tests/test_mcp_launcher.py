"""`conductor mcp`'s launcher with calls sent together (141, docs/mcp.md *The launcher*):
every call answered with its own id, whatever happens to the worker."""

import io
import json
import sys
import threading
import time

from conductor import mcp_launcher, planner_mcp


def launcher(cmd, **kw) -> tuple[mcp_launcher.Launcher, io.StringIO]:
    out = io.StringIO()
    return mcp_launcher.Launcher(cmd, out=out, traffic=None, **kw), out


def replies(out: io.StringIO) -> list:
    return [json.loads(ln) for ln in out.getvalue().splitlines() if ln.strip()]


def init_line() -> str:
    return json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})


def call_line(mid, tool: str, args: dict) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": mid, "method": "tools/call", "params": {"name": tool, "arguments": args}})


def test_five_calls_sent_at_once_get_five_answers_each_with_its_id(env):
    lz, out = launcher(mcp_launcher.worker_cmd([str(env.control)]))
    lz.feed(init_line())
    reads = [("status", {}), ("read", {"what": "conductor-report"}), ("read", {"what": "repo", "name": "code"}),
             ("status", {"project": "test"}), ("read", {"what": "ledger"})]
    for n, (tool, args) in enumerate(reads, 1):
        lz.feed(call_line(n, tool, {"project": "test", **args} if tool == "read" else args))
    lz.stop()
    got = [r for r in replies(out) if r.get("id") != 0]
    assert sorted(r["id"] for r in got) == [1, 2, 3, 4, 5]
    by_id = {r["id"]: r["result"]["content"][0]["text"] for r in got}
    assert by_id[1].startswith("test:") and "Past the mark" in by_id[2] and by_id[3].startswith("code/ (tracked):")
    assert by_id[4].startswith("test — daemon")
    assert lz.starts == 1


def test_a_batch_is_answered_as_one_array(env):
    lz, out = launcher(mcp_launcher.worker_cmd([str(env.control)]))
    lz.feed(init_line())
    lz.feed("[" + call_line("a", "status", {}) + "," + call_line("b", "read", {"what": "ledger"}) + "]")
    lz.feed(call_line(7, "status", {}) + call_line(8, "status", {}))       # two run together on one line
    lz.stop()
    got = replies(out)
    batch = next(r for r in got if isinstance(r, list))
    assert sorted(r["id"] for r in batch) == ["a", "b"]
    assert sorted(r["id"] for r in got if isinstance(r, dict) and r.get("id") in (7, 8)) == [7, 8]


SILENT = "import sys\nfor n, line in enumerate(sys.stdin):\n    if n == {n}: sys.exit(3)\n"


def test_a_worker_that_dies_answers_every_call_it_had_with_an_error():
    lz, out = launcher([sys.executable, "-c", SILENT.format(n=2)])      # takes three calls, answers none
    for n in (1, 2, 3):
        lz.feed(call_line(n, "status", {}))
    end = time.time() + 20
    while len(replies(out)) < 3 and time.time() < end:
        time.sleep(0.05)
    got = replies(out)
    assert sorted(r["id"] for r in got) == [1, 2, 3]
    assert all("exited during the call" in r["error"]["message"] for r in got)
    lz.feed(call_line(4, "status", {}))                                  # the next call starts another
    assert lz.starts == 2
    lz.stop(wait=1)


def test_a_call_with_no_answer_in_time_gets_an_error_and_a_late_reply_is_dropped():
    lz, out = launcher([sys.executable, "-c", SILENT.format(n=-1)], call_seconds=0.5)
    lz.feed(call_line(1, "signal", {"lines": ["x"]}))
    end = time.time() + 10
    while not replies(out) and time.time() < end:
        time.sleep(0.05)
    (r,) = replies(out)
    assert r["id"] == 1 and "no answer from conductor's MCP worker in 0s (signal)" in r["error"]["message"]
    lz.stop(wait=1)


def test_writes_to_one_project_keep_their_order_while_reads_run_beside_them(monkeypatch):
    out = io.StringIO()
    server = planner_mcp.PlannerServer([], out=out)
    order, gate = [], threading.Event()

    def call(name, a):
        if name == "status":
            gate.set()                          # a read runs while the first write still waits
            return "read"
        if a["n"] == 1:
            gate.wait(5)
        time.sleep(0.02 * (5 - a["n"]))         # a later write is quicker: only the lane keeps the order
        order.append((a["project"], a["n"]))
        return "ok"
    monkeypatch.setattr(server, "call", call)
    for n in range(1, 5):
        server.submit(json.loads(call_line(n, "signal", {"project": "alpha", "n": n})))
    server.submit(json.loads(call_line(9, "status", {})))
    server.drain()
    assert [n for p, n in order if p == "alpha"] == [1, 2, 3, 4]
    assert sorted(json.loads(ln)["id"] for ln in out.getvalue().splitlines()) == [1, 2, 3, 4, 9]
