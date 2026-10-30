"""Receipts: every planner write gets `receipts/<its path>.md`, accepted or refused."""

import os
import time

from conductor import daemon_receipts, daemon_signals
from conductor.daemon_receipts import rel_for
from conductor.util import read_text, split_front_matter


def receipt(env, write_rel: str) -> tuple[dict, str]:
    meta, body = split_front_matter(read_text(env.control / rel_for(write_rel)))
    return meta, body


def test_receipt_paths_are_named_by_the_write():
    assert rel_for("signals/planner-20261021-101500.signal") == "receipts/signals/planner-20261021-101500.md"
    assert rel_for("requests/20261021-1015-state") == "receipts/requests/20261021-1015-state.md"
    assert rel_for("queue/app/012_x.md") == "receipts/queue/app/012_x.md"
    assert rel_for("queue/app/012_x.md.bad") == "receipts/queue/app/012_x.md"
    assert rel_for("queue/app/012_x.amend.20261021-1015.md") == "receipts/queue/app/012_x.amend.20261021-1015.md"
    assert rel_for("questions/app__012_x__1.answer.md") == "receipts/questions/app__012_x__1.answer.md"
    assert rel_for("proposed/013_y.md") == "receipts/proposed/013_y.md"


def test_signal_receipts_accepted_refused_and_cut_short(env):
    assert (env.control / "receipts" / ".gitignore").read_text(encoding="utf-8") == "*\n"
    assert not [w for w in env.events("warning") if "git-ignored" in w.detail.get("note", "")]
    env.signal("planner-1.signal", "by: planner\ncheckpoint: one\n")
    env.signal("planner-2.signal", "by: planner\nfrobnicate: x\n")
    env.signal("planner-3.signal", "by: planner\ncheckpoint: two\napprove: 999_nothing\n")
    env.signal("planner-4.signal", "by: planner\nstop: daemon\n")      # logs nothing while it applies
    env.d.tick()
    meta, body = receipt(env, "signals/planner-1.signal")
    [cp] = [e for e in env.events("checkpoint") if e.detail["label"] == "one"]
    assert meta["outcome"] == "accepted" and meta["result"] == "applied" and meta["events"] == [cp.id]
    assert meta["write"] == "signals/planner-1.signal" and f"#{cp.id}" in body
    meta, _ = receipt(env, "signals/planner-2.signal")
    assert meta["outcome"] == "refused" and "unknown signal key 'frobnicate'" in meta["reason"]
    meta, _ = receipt(env, "signals/planner-3.signal")
    assert meta["outcome"] == "refused" and meta["applied"] == ["checkpoint: two"]
    assert "no item '999_nothing'" in meta["reason"] and len(meta["events"]) == 2
    meta, _ = receipt(env, "signals/planner-4.signal")
    assert meta["outcome"] == "accepted" and meta["events"] == []


def test_request_receipts_applied_unchanged_and_failed(env, monkeypatch):
    from conductor import request
    monkeypatch.setattr(request, "SAME_WAIT_SECONDS", 0.0)

    def write(stamp: str, files: dict[str, str]) -> None:
        d = env.control / "requests" / stamp
        d.mkdir(parents=True)
        for name, text in files.items():
            (d / name).write_text(text, encoding="utf-8")

    write("20261021-1000-state", {"request.md": "---\ntargets: [state.md]\nmessage: \"state: v2\"\n---\n",
                                  "state.md": "# state v2\n"})
    write("20261021-1001-same", {"request.md": "---\ntargets: [state.md]\nmessage: again\n---\n",
                                 "state.md": "# state v2\n"})
    write("20261021-1002-bad", {"request.md": "---\ntargets: [state.md]\nmessage: x\n---\n",
                                "state.md": "x\n", "stray.md": "y\n"})
    env.d.tick()
    meta, body = receipt(env, "requests/20261021-1000-state")
    [commit] = [e for e in env.events("commit") if e.detail["subject"] == "state: v2"]
    assert meta["outcome"] == "accepted" and meta["result"] == f"committed {commit.detail['sha'][:8]}"
    assert commit.id in meta["events"] and "request" in body
    assert receipt(env, "requests/20261021-1001-same")[0]["result"].startswith(
        "nothing to commit: every target is identical to HEAD")
    assert env.events("request")[-1].detail["identical"] is True
    meta, _ = receipt(env, "requests/20261021-1002-bad")
    assert meta["outcome"] == "refused" and "not listed as targets: ['stray.md']" in meta["reason"]


def test_a_waiting_request_has_a_pending_receipt_saying_what_it_waits_for(env):
    d = env.control / "requests" / "20261021-1100-two"
    d.mkdir(parents=True)
    (d / "request.md").write_text("---\ntargets: [state.md, notes.md]\nmessage: two\n---\n", encoding="utf-8")
    (d / "state.md").write_text("# state v3\n", encoding="utf-8")
    later = env.control / "requests" / "20261021-1101-after"
    later.mkdir(parents=True)
    (later / "request.md").write_text("---\ntargets: [state.md]\nmessage: after\n---\n", encoding="utf-8")
    (later / "state.md").write_text("# state v4\n", encoding="utf-8")
    old = time.time() - 5                               # settled: only a target is missing
    for p in [*d.iterdir(), *later.iterdir(), d, later]:
        os.utime(p, (old, old))
    env.d.tick()
    # Waiting 2s or more for its own files: pending, naming them.
    meta, _ = receipt(env, "requests/20261021-1100-two")
    assert meta["outcome"] == "pending" and meta["result"] == "waiting for targets missing: notes.md"
    meta, _ = receipt(env, "requests/20261021-1101-after")
    assert meta["outcome"] == "pending" and "an earlier request still waiting on ['state.md']" in meta["result"]
    (d / "notes.md").write_text("notes\n", encoding="utf-8")
    os.utime(d / "notes.md", (old, old))
    env.d.tick()
    meta, _ = receipt(env, "requests/20261021-1100-two")
    assert meta["outcome"] == "accepted" and meta["result"].startswith("committed ")
    assert receipt(env, "requests/20261021-1101-after")[0]["outcome"] == "accepted"


def test_a_shared_number_is_named_in_the_receipt_and_never_guessed(env):
    env.queue("080_watch", "FAKE report done", depends_on="001_gone")      # conductor's, say: holds
    env.d.tick()
    env.queue("080_feature", "do it")
    env.queue("081_next", "do it", depends_on="080")
    env.d.tick()
    meta, _ = receipt(env, "queue/code/080_feature.md")
    assert meta["result"] == ("queued as code/080_feature; starts now; "      # 080_feature sorts first
                              "its number is also code/080_watch's: name it in full, never by number")
    from conductor import items, state
    known = state.scan(env.d.cfg, env.d.log).known()
    assert items.resolve_ref("080", "code", known) is None
    assert items.resolve_ref("080_feature", "code", known) == "code/080_feature"
    assert sorted(items.ref_matches("080", "code", known)) == ["code/080_feature", "code/080_watch"]


def test_queue_item_rewrite_and_amendment_receipts(env):
    item = env.queue("002_waits", "do it", depends_on="001_first")      # holds: its dependency never runs
    bad = env.queue("003_bad", "do it", nope="x")
    env.d.tick()
    meta, _ = receipt(env, "queue/code/002_waits.md")
    assert meta["outcome"] == "accepted" and meta["result"] == "queued as code/002_waits; held: unknown dependency 001_first"
    assert meta["written_ms"] == item.stat().st_mtime_ns // 1_000_000
    meta, _ = receipt(env, "queue/code/003_bad.md")
    assert meta["outcome"] == "refused" and "unknown front-matter fields: ['nope']" in meta["reason"]
    assert not bad.with_suffix(".md.bad").exists()
    env.queue("002_waits", "do it differently", depends_on="001_first")
    later = time.time() + 5
    os.utime(item, (later, later))                  # a rewrite in the same tick's second still differs in mtime
    env.d.tick()
    meta, _ = receipt(env, "queue/code/002_waits.md")
    assert meta["result"] == "rewritten: it runs the new text"
    assert meta["written_ms"] == item.stat().st_mtime_ns // 1_000_000
    amend = env.control / "queue" / "code" / "002_waits.amend.20261021-1015.md"
    amend.write_text("---\nby: planner\n---\n\nAlso: say hello.\n", encoding="utf-8")
    env.d.tick()
    meta, _ = receipt(env, "queue/code/002_waits.amend.20261021-1015.md")
    [am] = env.events("amended")
    assert meta["outcome"] == "accepted" and meta["events"] == [am.id]
    assert meta["result"] == f"amendment #{am.id}, queued: appended to the prompt at launch"
    empty = env.control / "queue" / "code" / "002_waits.amend.empty.md"
    empty.write_text("---\nby: planner\n---\n\n", encoding="utf-8")
    env.d.tick()
    meta, _ = receipt(env, "queue/code/002_waits.amend.empty.md")
    assert meta["outcome"] == "refused" and meta["reason"] == "the amendment is empty"
    # Its delivery, when it comes, is added to the amendment's receipt.
    dv = env.d.log.append("delivered", item="code/002_waits", via="prompt", amend=am.id, waited_seconds=3)
    meta, body = receipt(env, "queue/code/002_waits.amend.20261021-1015.md")
    assert meta["result"] == f"delivered #{am.id} (prompt, after 3s)" and meta["events"] == [am.id, dv.id]
    assert f"#{dv.id}" in body


def test_answer_and_late_answer_receipts(env):
    rel = "questions/code__001_x__1.answer.md"
    env.d.log.append("answer", item="code/001_x", paths=[rel], question="code__001_x__1", text="yes")
    assert receipt(env, rel)[0]["result"] == "answered: the executor has it"
    env.d.log.append("late", item="code/001_x", paths=[rel], question="code__001_x__1", text="no", outcome="default")
    meta, _ = receipt(env, rel)
    assert meta["outcome"] == "refused" and "already resolved (default)" in meta["reason"]


def test_proposed_receipts_once_per_version(env):
    proposed = env.control / "proposed"
    ok, bad = proposed / "010_idea.md", proposed / "011_elsewhere.md"
    ok.write_text("---\nrepo: code\n---\n\nan idea\n", encoding="utf-8")
    bad.write_text("---\nrepo: nowhere\n---\n\nx\n", encoding="utf-8")
    env.d.tick()
    meta, _ = receipt(env, "proposed/010_idea.md")
    assert meta["outcome"] == "accepted" and "approve: 010_idea" in meta["result"] and meta["events"] == []
    assert "`repo: nowhere` is not a configured repo" in receipt(env, "proposed/011_elsewhere.md")[0]["reason"]
    path = env.control / rel_for("proposed/010_idea.md")
    path.write_text("kept\n", encoding="utf-8")
    env.d.tick()
    assert path.read_text(encoding="utf-8") == "kept\n"       # unchanged proposal: not rewritten
    env.d.log.close()
    env.d = env.new_daemon()                                  # nor after a restart, by its written_ms
    bad_receipt = env.control / rel_for("proposed/011_elsewhere.md")
    before = bad_receipt.read_text(encoding="utf-8")
    env.d.tick()
    assert bad_receipt.read_text(encoding="utf-8") == before


def test_any_planner_write_cuts_the_nap_short(env, monkeypatch):
    """Every receipt kind as fast as a queue item's (Word Warriors session 11): a proposal,
    a request or a signal ends the poll's sleep, not just a request."""
    import threading
    env.d.cfg.poll_seconds = 5
    env.d.tick()
    for rel, text in [("proposed/012_idea.md", "---\nrepo: code\n---\n\nan idea\n"),
                      ("requests/20261022-1000/request.md", "---\ntargets: [state.md]\nmessage: x\n---\n"),
                      ("signals/planner-1.signal", "by: planner\ncheckpoint: x\n"),
                      ("backlog/code/later.md", "---\nrepo: code\n---\n\nlater\n")]:
        path = env.control / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        timer = threading.Timer(0.1, path.write_text, (text,), {"encoding": "utf-8"})
        start = time.monotonic()
        timer.start()
        env.d._nap()
        timer.join()
        assert time.monotonic() - start < 1.5, rel
    # A file still settling (younger than SIGNAL_SETTLE_SECONDS, 0 in tests) ends the nap once it settles.
    monkeypatch.setattr(daemon_signals, "SIGNAL_SETTLE_SECONDS", 1.0)
    path.write_text("---\nrepo: code\n---\n\nlater again\n", encoding="utf-8")
    start = time.monotonic()
    env.d._nap()
    assert time.monotonic() - start < 1.5


def test_a_planner_signal_receipt_lands_within_a_second_and_a_half(env, monkeypatch):
    """The daemon's loop (tick, nap) with the real settles: a `planner-*` signal's receipt is
    there as fast as a request's (Word Warriors session 11 saw 2-3s), another signal waits 1s."""
    import threading
    monkeypatch.setattr(daemon_signals, "SIGNAL_SETTLE_SECONDS", 1.0)
    monkeypatch.setattr(daemon_signals, "PLANNER_SIGNAL_SETTLE_SECONDS", 0.25)
    env.d.cfg.poll_seconds = 2
    env.d.tick()
    for name, low in [("planner-20261022-100000.signal", 0), ("app-20261022-100000.signal", 1.0)]:
        got = env.control / rel_for(f"signals/{name}")
        timer = threading.Timer(0.1, env.signal, (name, "by: planner\ncheckpoint: fast\n"))
        start = time.monotonic()
        timer.start()
        while not got.exists() and time.monotonic() - start < 5:
            env.d._nap()
            env.d.tick()
        took = time.monotonic() - 0.1 - start
        assert got.exists() and low <= took < low + 1.5, (name, took)
        assert receipt(env, f"signals/{name}")[0]["outcome"] == "accepted"


def test_a_signal_written_with_a_request_is_not_left_a_whole_poll(env, monkeypatch):
    """Word Warriors session 12: a signal and a request in one bridge call; the tick that
    applied the request found the signal too young, and the nap after it, the signal by then
    of age, slept the whole poll: its receipt came ~2.1s after the request's."""
    monkeypatch.setattr(daemon_signals, "PLANNER_SIGNAL_SETTLE_SECONDS", 0.25)
    env.d.cfg.poll_seconds = 2
    env.d.tick()
    req = env.control / "requests" / "20261022-1120-x"
    req.mkdir(parents=True)
    (req / "state.md").write_text("# state\n\nnew\n", encoding="utf-8")
    env.signal("planner-20261022-112000.signal", "by: planner\ncheckpoint: with a request\n")
    (req / "request.md").write_text('---\ntargets: [state.md]\nmessage: "state: x"\n---\n', encoding="utf-8")
    env.d.tick()
    assert receipt(env, "requests/20261022-1120-x")[0]["outcome"] == "accepted"
    assert (env.control / "signals" / "planner-20261022-112000.signal").exists()     # too young then
    time.sleep(0.3)                                 # a live tick's own length: of age before the nap
    start = time.monotonic()
    env.d._nap()
    assert time.monotonic() - start < 0.2
    env.d.tick()
    assert receipt(env, "signals/planner-20261022-112000.signal")[0]["outcome"] == "accepted"
    start = time.monotonic()                        # nothing settling now: a full nap again
    env.d.cfg.poll_seconds = 0.5
    env.d._nap()
    assert time.monotonic() - start >= 0.45


def test_a_request_written_after_queue_items_is_not_left_behind_their_launch(env, monkeypatch):
    """Word Warriors 2026-10-22: one bridge call wrote two queue items, then a request. The tick
    the items woke found no `request.md` yet and spent its seconds creating the launched item's
    worktree; the request's receipt came ~6s after it. It is now handled around each launch."""
    from conductor import daemon as daemon_mod
    req = env.control / "requests" / "20261022-1747-calls"
    seen: list[bool] = []
    real = daemon_mod.Daemon.launch

    def launch(self, item, stage=None):
        seen.append((env.control / rel_for(f"requests/{req.name}")).is_file())
        req.mkdir(parents=True)                         # lands while the launch makes its worktree
        (req / "state.md").write_text("# state\n\ncalls\n", encoding="utf-8")
        (req / "request.md").write_text('---\ntargets: [state.md]\nmessage: "state: calls"\n---\n', encoding="utf-8")
        real(self, item, stage)

    monkeypatch.setattr(daemon_mod.Daemon, "launch", launch)
    env.queue("001_a", env.hold())
    env.d.tick()
    assert seen == [False] and receipt(env, f"requests/{req.name}")[0]["outcome"] == "accepted"
    [r] = env.events("request")
    assert r.id > env.events("start")[0].id         # in the same tick, right after the launch
    env.release()


def test_an_impossible_dependency_is_warned_of_at_write_time(env):
    """Accepted, with a `warning` naming why it will hold (Word Warriors 098 → 100 → 102)."""
    (env.control / "done" / "code").mkdir(parents=True, exist_ok=True)
    (env.control / "done" / "code" / "001_p.md").write_text("x\n", encoding="utf-8")
    env.d.log.append("finish", item="code/001_p", repo="code", status="partial")
    env.queue("010_a", "do it", depends_on="011_b")         # held at the head: nothing launches
    env.queue("011_b", "do it")
    env.queue("012_c", "do it")
    env.queue("013_d", "do it", depends_on="010")           # earlier in its queue: fine
    env.d.tick()
    meta, _ = receipt(env, "queue/code/010_a.md")
    assert meta["outcome"] == "accepted" and meta["result"].startswith("queued as code/010_a; held: depends on code/011_b")
    assert "`011_b` names code/011_b, later in its own queue" in meta["warning"]
    assert "warning" not in receipt(env, "queue/code/011_b.md")[0]
    assert "warning" not in receipt(env, "queue/code/013_d.md")[0]
    env.signal("planner-1.signal", "by: planner\ncancel: 012_c\n")
    env.d.tick()
    env.queue("014_e", "do it", depends_on="[001_p, 012_c]")
    env.d.tick()
    meta, _ = receipt(env, "queue/code/014_e.md")
    assert meta["outcome"] == "accepted" and "code/001_p, which ended partial" in meta["warning"]
    assert "code/012_c, which was cancelled" in meta["warning"]
    [ev] = [e for e in env.events("enqueue") if e.item == "code/014_e"]
    from conductor import inbox
    assert "warning: depends_on can't be met" in inbox.describe(ev)
    env.queue("010_a", "do it again")                       # a rewrite that drops it: no warning
    env.d.tick()
    assert "warning" not in receipt(env, "queue/code/010_a.md")[0]


def test_old_receipts_are_pruned(env, monkeypatch):
    old = env.control / "receipts" / "signals" / "planner-0.md"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text("old\n", encoding="utf-8")
    stale = time.time() - daemon_receipts.KEEP_SECONDS - 60
    os.utime(old, (stale, stale))
    env.signal("planner-1.signal", "by: planner\ncheckpoint: one\n")
    env.d.tick()
    assert not old.exists() and (env.control / "receipts" / "signals" / "planner-1.md").is_file()
    assert (env.control / "receipts" / ".gitignore").is_file()
    many = [env.d.log.append("warning", note=f"n{i}") for i in range(daemon_receipts.LINES + 2)]
    env.d._write_receipt("signals/planner-2.signal", many, result="applied")
    meta, body = receipt(env, "signals/planner-2.signal")
    assert len(meta["events"]) == len(many) and body.strip().splitlines()[-1] == "- … and 2 more (every id is in `events`)"
