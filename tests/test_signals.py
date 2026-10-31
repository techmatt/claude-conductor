"""Signal verbs: approve, cancel, reorder, reviewed, stop: daemon, and `by:` attribution."""

import os
import time

import pytest

from conductor import config, daemon, daemon_signals, items, procs
from conductor.daemon import Daemon


def propose(env, name: str, body: str = "FAKE report done", repo: str = "code", **meta) -> None:
    fm = "\n".join(f"{k}: {v}" for k, v in {"repo": repo, **meta}.items())
    p = env.control / "proposed" / f"{name}.md"
    p.write_text(f"---\n{fm}\n---\n{body}\n", encoding="utf-8")


def held(env) -> None:
    """A red item holds the `code` queue: whatever is queued after it stays put."""
    env.queue("001_bad", "FAKE report red")
    env.tick_until(lambda: env.finished("code/001_bad"))


def names(folder) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def queue_names(env) -> list[str]:
    return sorted(p.stem for p in (env.control / "queue" / "code").glob("*.md"))


def send(env, text: str, name: str = "s.signal") -> None:
    env.signal(name, text)
    # A just-written file can carry an mtime a hair ahead of time.time(): tick until taken.
    env.tick_until(lambda: not (env.control / "signals" / name).exists())


def bad(env, name: str = "s.signal") -> bool:
    return (env.control / "signals" / f"{name}.bad").is_file()


# ---------------------------------------------------------------- approve
def test_approve_queues_the_proposal_and_it_runs(env):
    propose(env, "001_hello", "FAKE commit a.txt a")
    send(env, "by: app\napprove: 001")
    [ap] = env.events("approve")
    assert ap.item == "code/001_hello" and ap.repo == "code" and ap.detail["from"] == "proposed/001_hello.md"
    assert ap.detail["by"] == "app"
    assert not (env.control / "proposed" / "001_hello.md").exists()
    env.tick_until(lambda: env.finished("code/001_hello"))
    assert env.finished("code/001_hello").detail["status"] == "done"
    assert env.events("enqueue")[0].id > ap.id and "by" not in env.events("enqueue")[0].detail


def test_approve_refusals(env):
    propose(env, "001_nowhere", repo="elsewhere")
    send(env, "approve: 001_nowhere", "a")
    assert bad(env, "a") and "not a configured repo" in env.events("invalid")[-1].detail["error"]
    propose(env, "002_broken", "")                                  # empty body: not a valid item
    send(env, "approve: 002", "b")
    assert bad(env, "b") and "prompt body is empty" in env.events("invalid")[-1].detail["error"]
    env.queue("003_dup", "FAKE report done", stage="false")
    propose(env, "003_dup")
    send(env, "approve: 003_dup", "c")
    assert bad(env, "c") and "already exists" in env.events("invalid")[-1].detail["error"]
    send(env, "approve: 009", "d")
    assert bad(env, "d")
    assert not env.events("approve")
    assert {"001_nowhere.md", "002_broken.md", "003_dup.md"} <= set(names(env.control / "proposed"))


# ---------------------------------------------------------------- cancel
def test_cancel_queued_and_proposed_keeps_the_file(env):
    held(env)
    env.queue("002_a", "FAKE report done")
    env.queue("003_b", "FAKE report done")
    propose(env, "005_p")
    env.d.tick()
    send(env, "by: app\ncancel: code/002_a\ncancel: 005")
    c1, c2 = env.events("cancel")
    assert (c1.item, c1.repo, c1.detail["from"], c1.detail["by"]) == ("code/002_a", "code", "queue", "app")
    assert (c2.item, c2.repo, c2.detail["from"]) == ("proposed/005_p", "code", "proposed")
    kept = sorted((env.control / ".conductor" / "cancelled").glob("*.md"))
    assert [p.name.split("__", 1)[1] for p in kept] == ["code__002_a.md", "proposed__005_p.md"]
    assert c1.detail["kept"] == f".conductor/cancelled/{kept[0].name}"
    assert queue_names(env) == ["003_b"] and not list((env.control / "proposed").glob("*.md"))
    # The same name queued again is a new item, enqueued again.
    env.queue("002_a", "FAKE report done")
    env.d.tick()
    assert [e.item for e in env.events("enqueue")].count("code/002_a") == 2
    inbox = (env.control / "inbox.md").read_text(encoding="utf-8")
    assert "code/002_a cancelled from queue" in inbox and "(by app)" in inbox


@pytest.mark.slow
def test_cancel_refuses_running_and_ambiguous(env):
    env.queue("001_long", env.hold() + "\nFAKE report done")
    env.tick_until(lambda: env.events("start"))
    send(env, "cancel: 001", "a")
    assert bad(env, "a") and "is running" in env.events("invalid")[-1].detail["error"]
    assert "code/001_long" in env.d.runs and not env.events("cancel")
    env.queue("002_x", "FAKE report done", stage="false")
    env.queue("002_y", "FAKE report done", stage="false")
    send(env, "cancel: 002", "b")
    assert bad(env, "b") and "ambiguous" in env.events("invalid")[-1].detail["error"]
    env.release()
    env.tick_until(lambda: env.finished("code/001_long"))
    assert env.finished("code/001_long").detail["status"] == "done"


@pytest.mark.slow
def test_cancel_staged_head_discards_its_stage(env):
    env.queue("001_long", env.hold() + "\nFAKE report done")
    env.queue("002_next", "FAKE stage-sleep 30\nFAKE report done")
    env.tick_until(lambda: env.events("stage"))
    pid = env.events("stage")[0].detail["pid"]
    send(env, "by: planner\ncancel: 002_next")
    assert not procs.alive(pid, None)
    warn = [e for e in env.events("warning") if "stage discarded" in e.detail.get("note", "")]
    assert warn and warn[0].detail["by"] == "planner"
    assert env.events("cancel")[0].item == "code/002_next"
    assert not env.worktree("002_next").exists()
    assert not list((env.control / "running" / "code").glob("*.stage.json"))
    env.release()
    env.tick_until(lambda: env.finished("code/001_long"))
    for _ in range(5):
        env.d.tick()
    assert not env.d.log.last("start", item="code/002_next")


# ---------------------------------------------------------------- reorder
@pytest.mark.slow
def test_reorder_queue(env):
    held(env)
    env.queue("002_a", "FAKE report done")
    env.queue("003_b", "FAKE report done")
    env.queue("004_c", "FAKE report done")
    env.d.tick()
    send(env, "by: app\nreorder: code 004 002")
    [r] = env.events("reorder")
    assert r.repo == "code" and r.detail["by"] == "app"
    assert r.detail["before"] == ["002_a", "004_c"] and r.detail["after"] == ["002_c", "004_a"]
    assert queue_names(env) == ["002_c", "003_b", "004_a"]
    env.d.tick()
    enq = [e.item for e in env.events("enqueue")]
    assert "code/002_c" in enq and "code/004_a" in enq
    send(env, "clear: code", "clear")
    env.tick_until(lambda: env.finished("code/004_a"))
    starts = [e.item for e in env.events("start")][1:]
    assert starts == ["code/002_c", "code/003_b", "code/004_a"]


def test_reorder_carries_pending_amendments(env):
    held(env)
    env.queue("002_a", "FAKE report done")
    env.queue("003_b", "FAKE report done")
    env.queue("004_c", "FAKE report done")
    env.d.tick()
    qdir = env.control / "queue" / "code"
    future = time.time() + 60               # unsettled: still waiting when the reorder runs
    for name in ("002_a.amend.md", "002_a.amend.x.md", "003_b.amend.md", "004_c.amend.md"):
        (qdir / name).write_text(f"---\nby: planner\n---\n\nfor {name.split('.')[0]}\n", encoding="utf-8")
        os.utime(qdir / name, (future, future))
    send(env, "reorder: code 004 002 003")          # a rotation: all three renamed
    assert queue_names(env) == ["002_c", "002_c.amend", "003_a", "003_a.amend", "003_a.amend.x",
                                "004_b", "004_b.amend"]
    for p in qdir.glob("*.amend*.md"):
        os.utime(p, (time.time() - 5, time.time() - 5))
    env.tick_until(lambda: len(env.events("amended")) == 4)
    got = sorted((e.item, e.detail["text"]) for e in env.events("amended"))
    assert got == [("code/002_c", "for 004_c"), ("code/003_a", "for 002_a"), ("code/003_a", "for 002_a"),
                   ("code/004_b", "for 003_b")]
    assert not any("left in place" in e.detail.get("note", "") for e in env.events("warning"))


def test_reorder_keeps_padding_and_moves_back(env):
    held(env)
    env.queue("010_a", "FAKE report done")
    env.queue("0020_b", "FAKE report done")
    env.d.tick()
    send(env, "reorder: code 0020_b 010_a")
    assert queue_names(env) == ["0020_a", "010_b"]
    send(env, "reorder: code 0020 010", "back")
    assert queue_names(env) == ["0020_b", "010_a"]
    env.d.tick()
    assert [e.item for e in env.events("enqueue")].count("code/010_a") == 2   # retired, then back


def test_reorder_refusals(env):
    held(env)
    env.queue("002_a", "FAKE report done")
    env.queue("003_b", "FAKE report done", depends_on="002")
    env.queue("004_c", "FAKE report done")
    env.d.tick()
    for n, text in enumerate(["reorder: code 004 002",            # 003 depends on 002
                              "reorder: code 004 004",            # listed twice
                              "reorder: code 004 009",            # unknown
                              "reorder: nowhere 004",             # not a repo
                              "reorder: code"]):                  # nothing listed
        send(env, text, f"r{n}")
        assert bad(env, f"r{n}"), text
    assert "depends_on" in env.events("invalid")[-5].detail["error"]
    assert queue_names(env) == ["002_a", "003_b", "004_c"] and not env.events("reorder")


@pytest.mark.slow
def test_reorder_refuses_running_item(env):
    env.queue("001_long", env.hold() + "\nFAKE report done")
    env.queue("002_a", "FAKE report done", stage="false")
    env.tick_until(lambda: env.events("start"))
    send(env, "reorder: code 002 001")
    assert bad(env) and "running" in env.events("invalid")[-1].detail["error"]
    env.release()
    env.tick_until(lambda: env.finished("code/002_a"))


@pytest.mark.slow
def test_reorder_renumbered_stage_is_discarded(env):
    env.queue("001_long", env.hold() + "\nFAKE report done")
    env.queue("002_s", "FAKE stage-sleep 30\nFAKE report done")
    env.queue("003_t", "FAKE report done", stage="false")
    env.tick_until(lambda: env.events("stage"))
    pid = env.events("stage")[0].detail["pid"]
    send(env, "reorder: code 003 002")
    env.tick_until(lambda: not procs.alive(pid, None))      # a killed process goes a moment later
    assert any("renumbered" in e.detail.get("note", "") for e in env.events("warning"))
    assert queue_names(env) == ["002_t", "003_s"]
    env.release()
    try:
        env.tick_until(lambda: env.finished("code/002_t"))
        assert [e.item for e in env.events("start")] == ["code/001_long", "code/002_t"]
    finally:
        for run in env.d.stages.values():        # 003_s may be staging again (30s sleep)
            procs.kill_tree(run.pid())


def test_reorder_proposed(env):
    propose(env, "001_x")
    propose(env, "002_y")
    send(env, "reorder: proposed 002 001")
    [r] = env.events("reorder")
    assert r.repo == "proposed" and r.detail["after"] == ["001_y", "002_x"]
    assert names(env.control / "proposed") == ["001_y.md", "002_x.md"]


class Crash(BaseException):
    pass


@pytest.mark.slow
@pytest.mark.parametrize("survive", [1, 3])        # mid temp phase; mid final phase
def test_reorder_finishes_after_a_crash(env, monkeypatch, survive):
    held(env)
    env.queue("002_a", "FAKE report done")
    env.queue("003_b", "FAKE report done")
    env.d.tick()
    real, calls = daemon.os.rename, []

    def flaky(src, dst):
        if len(calls) == survive:
            raise Crash
        calls.append(dst)
        real(src, dst)

    monkeypatch.setattr(daemon.os, "rename", flaky)
    env.signal("s.signal", "by: app\nreorder: code 003 002")
    with pytest.raises(Crash):
        env.tick_until(lambda: False, timeout=10)
    monkeypatch.setattr(daemon.os, "rename", real)
    folder = env.control / "queue" / "code"
    assert any(items.REORDER_TEMP in n for n in names(folder))
    assert len(queue_names(env)) == 1                         # a temp name is never an item
    env.d.log.close()
    env.d = env.new_daemon()
    assert queue_names(env) == ["002_b", "003_a"] and not any(items.REORDER_TEMP in n for n in names(folder))
    [r] = env.events("reorder")
    assert r.detail["note"] == "completed after a restart" and r.detail["by"] == "app"
    assert not (env.control / ".conductor" / "reorder.json").exists()
    # The interrupted signal file is set aside, never re-applied (that would undo the swap).
    assert bad(env) and not (env.control / "signals" / "s.signal").exists()
    env.d.tick()
    assert queue_names(env) == ["002_b", "003_a"]


def test_stray_reorder_temp_is_restored(env):
    folder = env.control / "queue" / "code"
    (folder / f"002_a{items.REORDER_TEMP}003_a").write_text("---\nrepo: code\n---\nFAKE report done\n",
                                                           encoding="utf-8")
    env.d.log.close()
    env.d = env.new_daemon()
    assert queue_names(env) == ["003_a"]
    assert any("restored" in e.detail.get("note", "") for e in env.events("warning"))


# ---------------------------------------------------------------- by:, validation
def test_lines_are_checked_before_any_applies(env):
    send(env, "by: app\nclear: code\nbogus: x")
    assert bad(env) and not env.events("clear")
    [inv] = env.events("invalid")
    assert inv.detail["by"] == "app" and "bogus" in inv.detail["error"]


def test_a_failing_verb_keeps_earlier_lines(env):
    send(env, "read: latest   # inline comment\napprove: 042")
    assert env.events("read") and bad(env)
    assert env.events("invalid")[-1].detail["applied"] == ["read: latest"]


# ---------------------------------------------------------------- stop
@pytest.mark.slow
def test_stop_daemon_leaves_executors_running(env):
    env.queue("001_long", env.hold() + "\nFAKE report done")
    env.tick_until(lambda: env.events("start"))
    pid = env.events("start")[0].detail["pid"]
    env.d.runs.clear()
    env.d.log.close()
    d = Daemon(config.load(env.control), echo=env.lines.append, notifier=env.notifier)
    env.signal("stop.signal", "by: app\nstop: daemon")
    d.run_forever()                               # returns on its own
    env.d = env.new_daemon()
    [stop] = env.events("daemon_stop")
    assert stop.detail["reason"] == "signal" and stop.detail["by"] == "app"
    assert stop.detail["running"] == ["code/001_long"]
    assert procs.alive(pid, None) and not (env.control / ".conductor" / "daemon.json").exists()
    assert not (env.control / "signals" / "stop.signal").exists()
    env.release()
    env.tick_until(lambda: env.finished("code/001_long"))
    assert env.finished("code/001_long").detail["status"] == "done"


def test_stop_needs_daemon(env):
    send(env, "stop: everything")
    assert bad(env) and env.d.stop_requested is None


# ---------------------------------------------------------------- crash and failure safety
def journal(env, name: str):
    return env.control / ".conductor" / name


def test_a_crash_after_a_reorder_line_never_applies_the_file_twice(env, monkeypatch):
    held(env)
    env.queue("002_a", "FAKE report done")
    env.queue("003_b", "FAKE report done")
    env.d.tick()
    real = Daemon._apply_signal

    def crashy(self, key, value, rel):
        if key == "clear":
            raise Crash
        real(self, key, value, rel)

    monkeypatch.setattr(Daemon, "_apply_signal", crashy)
    env.signal("s.signal", "reorder: code 003 002\nclear: code")
    with pytest.raises(Crash):
        env.tick_until(lambda: False, timeout=10)
    monkeypatch.setattr(Daemon, "_apply_signal", real)
    assert not journal(env, "reorder.json").exists() and journal(env, "signal.json").exists()
    env.d.log.close()
    env.d = env.new_daemon()
    assert bad(env) and not journal(env, "signal.json").exists()
    env.d.tick()
    assert queue_names(env) == ["002_b", "003_a"]
    assert len(env.events("reorder")) == 1 and not env.events("clear")
    assert any("interrupted part-way" in e.detail.get("note", "") for e in env.events("warning"))


def test_a_reorder_whose_rename_fails_is_finished_on_a_later_tick(env, monkeypatch):
    held(env)
    env.queue("002_a", "FAKE report done")
    env.queue("003_b", "FAKE report done")
    env.d.tick()
    real, locked = daemon_signals.retry_io, {"on": True}

    def held_open(fn, *args, **kwargs):
        if locked["on"] and fn is daemon.os.rename and str(args[0]).endswith("003_b.md"):
            raise PermissionError(32, "being used by another process")
        return real(fn, *args, **kwargs)

    monkeypatch.setattr(daemon_signals, "retry_io", held_open)
    send(env, "reorder: code 003 002")
    assert not bad(env) and not env.events("reorder")
    assert journal(env, "reorder.json").exists() and env.d._reorder_pending() == "code"
    assert any("interrupted" in e.detail.get("note", "") for e in env.events("warning"))
    send(env, "reorder: code 002 003", "again")                # refused while one is pending
    assert bad(env, "again") and "still being finished" in env.events("invalid")[-1].detail["error"]
    locked["on"] = False
    env.d.tick()
    [r] = env.events("reorder")
    assert "later tick" in r.detail["note"] and queue_names(env) == ["002_b", "003_a"]
    assert not journal(env, "reorder.json").exists() and env.d._reorder_pending() is None


def test_reorder_refuses_to_put_an_item_before_its_dependency(env):
    held(env)
    env.queue("002_a", "FAKE report done")
    env.queue("003_c", "FAKE report done")
    env.queue("004_b", "FAKE report done", depends_on="003")
    env.d.tick()
    send(env, "reorder: code 004 002")                        # 004_b would become 002_b, before 003_c
    assert bad(env) and "hold forever" in env.events("invalid")[-1].detail["error"]
    assert queue_names(env) == ["002_a", "003_c", "004_b"]


def test_reorder_never_reuses_an_id_that_ran(env):
    held(env)                                                 # done/code/001_bad
    env.queue("001_a", "FAKE report done")
    env.queue("004_bad", "FAKE report done")
    env.d.tick()
    send(env, "reorder: code 004 001")                        # 004_bad would become 001_bad
    assert bad(env) and "ran or are running" in env.events("invalid")[-1].detail["error"]
    assert queue_names(env) == ["001_a", "004_bad"]


@pytest.mark.slow
def test_a_stage_discard_that_cannot_remove_the_worktree_still_drops_the_stage(env, monkeypatch):
    env.queue("001_long", env.hold() + "\nFAKE report done")
    env.queue("002_next", "FAKE stage-sleep 30\nFAKE report done")
    env.tick_until(lambda: env.events("stage"))
    real = daemon.worktrees.discard

    def locked(*args, **kwargs):
        raise PermissionError(32, "being used by another process")

    monkeypatch.setattr(daemon.worktrees, "discard", locked)
    send(env, "cancel: 002_next")
    monkeypatch.setattr(daemon.worktrees, "discard", real)
    assert not bad(env) and env.events("cancel")[0].item == "code/002_next"
    assert not list((env.control / "running" / "code").glob("*.stage.json"))
    assert any("not discarded" in e.detail.get("note", "") for e in env.events("warning"))
    real(env.d.cfg, "code", env.worktree("002_next"), "conductor/002_next")
    env.release()
    env.tick_until(lambda: env.finished("code/001_long"))


def test_a_reorder_retires_the_old_ids_holds_in_the_inbox(env):
    from conductor import inbox, state
    held(env)
    env.queue("002_a", "FAKE report done")
    env.queue("003_b", "FAKE report done")
    env.d.tick()
    assert env.d.log.last("hold", item="code/002_a")
    send(env, "reorder: code 003 002")
    env.d.tick()
    text = inbox.render(env.d.cfg, env.d.log, state.scan(env.d.cfg, env.d.log))
    holds = text.split("## Holds", 1)[1].split("\n## ", 1)[0]
    assert "code/002_a" not in holds and "code/002_b" in holds


def test_a_tick_that_fails_on_a_locked_file_recovers(env, monkeypatch):
    env.d.log.close()
    d = Daemon(config.load(env.control), echo=env.lines.append, notifier=env.notifier)
    real, calls = Daemon.process_requests, []

    def flaky(self):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError(32, "being used by another process")
        env.signal("stop.signal", "stop: daemon")
        real(self)

    monkeypatch.setattr(Daemon, "process_requests", flaky)
    d.run_forever()                               # survives the failed tick, then stops
    env.d = env.new_daemon()
    assert any("a tick failed" in e.detail.get("note", "") for e in env.events("warning"))
    assert env.events("daemon_stop")[-1].detail["reason"] == "signal"


# ---------------------------------------------------------------- reviewed
@pytest.mark.slow
def test_reviewed_marks_a_finished_report_and_survives_restart(env):
    from conductor import inbox, state
    env.queue("001_a", "FAKE report done")
    env.queue("002_b", "FAKE report partial")
    env.tick_until(lambda: env.finished("code/002_b"))
    send(env, "read: latest", "read")                       # the read mark reviews nothing
    view = state.scan(env.d.cfg, env.d.log)
    assert [e.item for e in view.unreviewed()] == ["code/001_a", "code/002_b"]
    text = inbox.render(env.d.cfg, env.d.log, view)
    section = text.split("## Unreviewed reports", 1)[1].split("\n## ", 1)[0]
    assert section.index("code/001_a") < section.index("code/002_b")
    assert "reports/code__001_a.md" in section and "carry:" in section and "**partial**" in section
    send(env, "by: planner\nreviewed: 001")
    [r] = env.events("reviewed")
    assert (r.item, r.detail["by"], r.detail["status"]) == ("code/001_a", "planner", "done")
    assert r.detail["finish"] == env.finished("code/001_a").id
    # Derived from the log: a new daemon sees the same.
    env.d.log.close()
    env.d = env.new_daemon()
    assert [e.item for e in state.scan(env.d.cfg, env.d.log).unreviewed()] == ["code/002_b"]
    send(env, "reviewed: code/001_a", "again")              # already reviewed: a no-op with a note
    assert not bad(env, "again") and len(env.events("reviewed")) == 1
    assert "already reviewed" in env.events("warning")[-1].detail["note"]
    send(env, "reviewed: code/002", "last")
    env.d.write_inbox(force=True)
    assert "## Unreviewed reports" not in (env.control / "inbox.md").read_text(encoding="utf-8")


@pytest.mark.slow
def test_slow_line_rides_the_finish_into_the_inbox(env):
    from conductor import inbox, state
    slow = "tests ~25m, every change ran the full lane, split the browser tests out"
    env.queue("001_a", f"FAKE slow {slow}")
    env.queue("002_b", "FAKE slow none")
    env.queue("003_c", "FAKE report done")                  # an older-style report: no slow: line
    env.tick_until(lambda: env.finished("code/003_c"))
    assert [env.finished(f"code/{i}").detail["slow"] for i in ("001_a", "002_b", "003_c")] == [slow, "none", None]
    assert not env.events("red")
    text = inbox.render(env.d.cfg, env.d.log, state.scan(env.d.cfg, env.d.log))
    section = text.split("## Unreviewed reports", 1)[1].split("\n## ", 1)[0]
    lines = {ln.split()[1]: ln for ln in section.splitlines() if ln.startswith("- ")}
    assert f" · slow: {slow}" in lines["code/001_a"]
    assert "slow:" not in lines["code/002_b"] and "slow:" not in lines["code/003_c"]
    assert f"slow: {slow}" in inbox.describe(env.finished("code/001_a"))


@pytest.mark.slow
def test_reviewed_refusals(env):
    env.queue("001_long", env.hold() + "\nFAKE report done")
    env.queue("002_q", "FAKE report done", stage="false")
    env.tick_until(lambda: env.events("start"))
    for n, text in enumerate(["reviewed: 001",              # running
                              "reviewed: code/002_q",       # queued
                              "reviewed: 009",              # unknown
                              "reviewed: nowhere/001"]):    # not a repo
        send(env, text, f"r{n}")
        assert bad(env, f"r{n}"), text
    errors = [e.detail["error"] for e in env.events("invalid")]
    assert "has not finished" in errors[0] and "has not finished" in errors[1]
    assert not env.events("reviewed")
    env.release()
    env.tick_until(lambda: env.finished("code/002_q"))


# ---------------------------------------------------------------- checkpoints
@pytest.mark.slow
def test_a_checkpoint_hides_settled_items_and_carries_the_rest(env):
    from conductor import state
    from conductor.app import model
    env.queue("001_old", "FAKE report done")
    env.queue("002_unrev", "FAKE report partial")
    env.queue("003_bad", "FAKE report red")
    env.tick_until(lambda: env.finished("code/003_bad"))
    env.queue("004_next", "FAKE report done")                # held behind the red
    send(env, "by: planner\nreviewed: 001\nreviewed: 003", "rev")
    send(env, "by: planner\ncheckpoint: session start # two", "cp")
    [cp] = env.events("checkpoint")
    assert (cp.detail["label"], cp.detail["by"]) == ("session start # two", "planner")
    # Only the unreviewed report gets a warning, naming it.
    warned = [e for e in env.events("warning") if e.detail.get("checkpoint") == cp.id]
    assert [e.item for e in warned] == ["code/002_unrev"] and "unreviewed" in warned[0].detail["note"]
    view = state.scan(env.d.cfg, env.d.log)
    assert view.checkpoint.id == cp.id
    # 001 settled; 002 unreviewed and 003 (its red still holds the queue) carry forward.
    assert [i for i in view.finishes if view.settled(i)] == ["code/001_old"]
    current, _ = model.finished_items(view, "code")
    earlier, total = model.earlier_items(view, "code")
    assert [f.name for f in current] == ["003_bad", "002_unrev"]
    assert [f.name for f in earlier] == ["001_old"] and total == 1
    assert [i.name for i in view.queued["code"]] == ["004_next"]
    env.d.write_inbox(force=True)
    text = (env.control / "inbox.md").read_text(encoding="utf-8")
    assert f'Current checkpoint: checkpoint #{cp.id} "session start # two"' in text.splitlines()[4]
    unrev = text.split("## Unreviewed reports", 1)[1].split("\n## ", 1)[0]
    assert "code/002_unrev" in unrev and f"carried from before checkpoint #{cp.id}" in unrev
    # Reviewed after the checkpoint: still this session's, until the next checkpoint.
    send(env, "reviewed: 002", "rev2")
    assert not state.scan(env.d.cfg, env.d.log).settled("code/002_unrev")
    send(env, "checkpoint:", "cp2")
    cp2 = env.events("checkpoint")[-1]
    assert cp2.detail["label"] == "" and cp2.detail["by"] == "unknown"
    assert not [e for e in env.events("warning") if e.detail.get("checkpoint") == cp2.id]
    view = state.scan(env.d.cfg, env.d.log)
    assert sorted(i for i in view.finishes if view.settled(i)) == ["code/001_old", "code/002_unrev"]
    send(env, "clear: code", "clr")                            # the red no longer holds: it settles too
    assert state.scan(env.d.cfg, env.d.log).settled("code/003_bad")


@pytest.mark.slow
def test_session_writes_a_checkpoint_the_digest_and_moves_the_read_mark(env, tmp_path, monkeypatch):
    from conductor import session
    root = tmp_path / "conductor"                      # a stand-in for conductor's own repo
    (root / "reports").mkdir(parents=True)
    (root / "planner-notes.md").write_text("## Status\n\nLast report reviewed by the planner: 12_x\n", encoding="utf-8")
    for name, text in [("3_old", "status: done"), ("12_x", "status: done"), ("12a_y", "# 12a\n\nstatus: partial"),
                       ("13_z", "# 13\n\nno header")]:
        (root / "reports" / f"{name}_report.md").write_text(text + "\n", encoding="utf-8")
    monkeypatch.setattr(session, "CONDUCTOR_ROOT", root)
    env.queue("001_feat", "FAKE slow tests ~20m, full lane each time, split it\nFAKE report done")
    env.queue("002_perf", "FAKE report done", template="test_perf")
    env.queue("003_more", "FAKE slow tests ~25m, again; build ~16m, cold cache, warm it\n"
                          "FAKE friction stale test command, retried\nFAKE report done")
    env.queue("004_last", "FAKE friction stale test command; no port in the README\nFAKE report done")
    env.tick_until(lambda: env.finished("code/004_last"))
    (env.repo / "CLAUDE.md").write_text("one\ntwo\n", encoding="utf-8", newline="\n")
    send(env, "by: planner\nsession: start", "planner-1.signal")
    cp = env.events("checkpoint")[-1]
    assert (cp.detail["label"], cp.detail["session"], cp.detail["by"]) == ("start", "session.md", "planner")
    read = env.events("read")[-1]
    assert read.id > cp.id and read.detail["session"] == cp.id and read.detail["through"] == read.id - 1
    text = (env.control / "session.md").read_text(encoding="utf-8")
    assert text.startswith("# Session start — ") and "`signals/planner-1.signal`" in text
    assert "Mark read:" not in text and "code/004_last finished" in text      # the inbox since the old mark
    reports = text.split("## Unreviewed reports", 1)[1].split("\n## ", 1)[0]
    assert "conductor's 12a_y **partial**" in reports and "conductor's 13_z **no status line**" in reports
    assert "12_x" not in reports and "3_old" not in reports and "## Conductor reports" not in text
    facts = text.split("## Maintenance facts", 1)[1]
    assert "- test cleanup: 002_perf, " in facts     # queued as `test_perf`, an alias and "· 2 non-template item(s) finished since" in facts
    assert "- bug hunt: never · 3 non-template item(s) finished" in facts
    assert "- `CLAUDE.md`: 0.0 KB, 2 lines" in facts and "- unused audit notes: 0" in facts
    assert "repeated in the last 4 report(s): tests (001, 003)" in facts
    assert "- `friction:` repeated in the last 4 report(s): stale test command (003, 004)" in facts
    assert "- workflow retro: never · 3 non-template item(s) finished since\n" in facts      # no trigger yet
    assert "- parked edits waiting: 0" in facts and "friction: stale test command; no port" in text
    # The next session's digest starts after this one's read mark.
    send(env, "session: again", "planner-2.signal")
    text = (env.control / "session.md").read_text(encoding="utf-8")
    assert "code/004_last finished" not in text and '"again"' in text


def test_session_md_starts_with_the_project_links(env):
    env.configure('[links]\n"design doc" = "https://claude.ai/artifact/abc"\nboard = " https://x/b "\n\n'
                  '[repos]\ncode = "../code"\n')
    assert env.d.cfg.links == {"design doc": "https://claude.ai/artifact/abc", "board": "https://x/b"}
    send(env, "session: start", "planner-1.signal")
    text = (env.control / "session.md").read_text(encoding="utf-8")
    assert text.split("\n\n")[1:3] == ["## Links", "- design doc: https://claude.ai/artifact/abc\n- board: https://x/b"]
    (env.control / "conductor.toml").write_text('project = "t"\n[links]\nx = 3\n[repos]\ncode = "../code"\n',
                                               encoding="utf-8")
    with pytest.raises(config.ConfigError, match=r"`\[links\]` must be name = URL strings"):
        config.load(env.control)


def test_digest_copies_state_md_lines_a_start_raises(tmp_path):
    from conductor import session
    assert session.from_state(tmp_path)[-1] == "No `state.md` readable."
    (tmp_path / "state.md").write_text(
        "# p — state\n\n## Maintenance\n\nIntro saying what is due.\n\n- bug hunt: 080\n- general cleanup due:\n"
        "  11 items since 068\n\n## Waiting on Matt\n\n- the art pick\n\n"
        "## Pending fixes (session 9)\n\n**Conductor**\n- **Due from checkpoint #1:** merge notes\n"
        "- later, not yet\n\n## Paged\n\n- due but paged\n", encoding="utf-8")
    text = "\n".join(session.from_state(tmp_path))
    assert "### Waiting on Matt\n- the art pick\n" in text
    assert "### Open questions\nNo *Open questions* section." in text
    assert "### Pending fixes, due\n- **Due from checkpoint #1:** merge notes\n\n" in text and "later" not in text
    assert text.endswith("### Maintenance, due\n- general cleanup due:\n  11 items since 068")
    assert "Intro" not in text and "paged" not in text


def test_digest_keeps_planner_habits_apart_from_pending_fixes(tmp_path):
    from conductor import session
    assert session.planner_habits(tmp_path) == []
    (tmp_path / "state.md").write_text(
        "# p\n\n## Pending fixes\n\n- due: a parked edit\n\n## Planner habits\n\n"
        "- stage receipts before quoting them (due: always)\n\n## Paged\n\n- x\n", encoding="utf-8")
    fixes = "\n".join(session.from_state(tmp_path))
    assert "a parked edit" in fixes and "receipts" not in fixes
    assert session.planner_habits(tmp_path) == ["## Planner habits", "", session.HABITS_NOTE, "",
                                                "- stage receipts before quoting them (due: always)"]


@pytest.mark.slow
def test_digest_hides_reviewed_items_and_a_closed_session_and_closeout_moves_the_mark(env):
    from conductor import inbox, state
    env.queue("001_seen", "FAKE report done")
    env.queue("002_new", "FAKE report done")
    env.tick_until(lambda: env.finished("code/002_new"))
    send(env, "reviewed: 001", "rev")
    # A session that closed out before closeouts moved the mark: its requests stay settled.
    for kind, key, text in [("request", "message", "state: old news"), ("commit", "subject", "state: old news"),
                            ("request", "message", "closeout: s1 ends"), ("commit", "subject", "closeout: s1 ends"),
                            ("request", "message", "state: fresh")]:
        env.d.log.append(kind, **({"scope": "control"} if kind == "commit" else {}), **{key: text})
    text = inbox.render(env.d.cfg, env.d.log, state.scan(env.d.cfg, env.d.log))
    assert "code/002_new finished" in text and "code/001_seen" not in text
    assert "state: fresh" in text and "old news" not in text
    assert "closeout: s1 ends · committed" in text          # the request that passed over the rest, one line
    assert "hidden as settled: " in text and " of reviewed items" in text
    assert "2 passed over by `state.md` request #" in text     # old news and its commit
    req = env.control / "requests" / "20261021-1000"
    req.mkdir(parents=True)
    (req / "state.md").write_text("# state\n\nclosed\n", encoding="utf-8")
    (req / "request.md").write_text('---\ntargets: [state.md]\nmessage: "closeout: s2 ends"\n---\n', encoding="utf-8")
    env.tick_until(lambda: not req.exists())
    read = env.events("read")[-1]
    assert read.detail["closeout"] == "20261021-1000" and read.detail["through"] == read.id - 1
    text = inbox.render(env.d.cfg, env.d.log, state.scan(env.d.cfg, env.d.log))
    assert "code/002_new finished" not in text and "state: fresh" not in text


@pytest.mark.slow
def test_digest_drops_pushes_folds_commits_and_reds_a_state_request_passed_over(env):
    """Word Warriors session 12's start: 263 events, ~60% settled. Pushes go, each request
    and its commit are one line, and a red a later `state.md` request passed over goes."""
    log = env.d.log
    log.append("tamper", changes=["old tamper"])
    log.append("memory_critical", level="critical", was="pressure")
    log.append("push", target="control", branch="main", remote="origin", sha="abc", why=["commit"])
    log.append("warning", note="seen in session 11")
    log.append("request", paths=["state.md"], request="r1", message="state: process 1", targets=["state.md"])
    log.append("commit", paths=["state.md"], scope="control", sha="1234567890ab", subject="state: process 1")
    log.append("request", paths=["docs/x.md"], request="r2", message="docs: x", targets=["docs/x.md"])
    log.append("commit", paths=["docs/x.md"], scope="control", sha="feedfacecafe", subject="docs: x")
    log.append("tamper", changes=["new tamper"])
    log.append("push", target="control", branch="main", remote="origin", sha="def", why=["commit"])
    log.append("finish", item="code/009_open", status="done")
    send(env, "by: planner\nsession: start", "planner-1.signal")
    text = (env.control / "session.md").read_text(encoding="utf-8")
    assert "pushed" not in text and "old tamper" not in text and "memory critical" not in text
    assert "seen in session 11" not in text and "new tamper" in text
    assert "request r1 applied: state: process 1 — state.md · committed 1234567890" in text
    assert "request r2 applied: docs: x — docs/x.md · committed feedfaceca (#" in text
    assert "control commit" not in text
    assert "2 pushes" in text and "passed over by `state.md` request #" in text
    assert "is still unreviewed at checkpoint" not in text and "- code/009_open **done**" in text


def test_a_rejected_signal_shows_until_acknowledged_or_a_checkpoint(env):
    """The app header's "N rejected", the inbox and the digest: rejected signals since the
    latest checkpoint, less those an `ack: invalid` acknowledged (Word Warriors' 7:59am picks)."""
    from conductor import inbox, state
    from conductor.events import live_invalids
    from conductor.app import actions
    send(env, "checkpoint: s", "cp")
    for n in (1, 2, 3):
        send(env, "by: app\nbogus: x", f"app-{n}.signal")
    bad_ids = [e.id for e in live_invalids(env.d.log)]
    assert len(bad_ids) == 3
    render = lambda: inbox.render(env.d.cfg, env.d.log, state.scan(env.d.cfg, env.d.log))
    assert "app-1.signal invalid" in render()
    send(env, f"by: planner\nack: invalid #{bad_ids[0]}", "planner-ack.signal")
    assert [e.id for e in live_invalids(env.d.log)] == bad_ids[1:]
    assert "app-1.signal invalid" not in render() and "app-2.signal invalid" in render()
    assert f"acknowledged invalid #{bad_ids[0]}" in render()
    send(env, f"by: planner\nack: invalid #{bad_ids[0]}", "planner-again.signal")    # already acknowledged
    assert bad(env, "planner-again.signal")
    path = actions.ack_invalid(env.control)                 # the app's Dismiss: all of them
    env.tick_until(lambda: not path.exists())
    assert live_invalids(env.d.log) == [] and "app-3.signal invalid" not in render()
    send(env, "by: app\nbogus: y", "app-4.signal")
    assert len(live_invalids(env.d.log)) == 1
    send(env, "checkpoint: next", "cp2")                    # a checkpoint clears it too
    assert live_invalids(env.d.log) == [] and "app-4.signal invalid" not in render()
    with pytest.raises(ValueError):
        env.d._parse_signal("ack: everything")


def test_events_since_checkpoint_on_the_cli(env, capsys):
    from conductor import cli
    send(env, "checkpoint: one", "cp")
    first = env.events("checkpoint")[0].id
    send(env, "checkpoint: two", "cp2")
    assert cli.main(["events", str(env.control), "--since", "checkpoint"]) == 0
    out = capsys.readouterr().out
    assert '"two"' in out and '"one"' not in out
    assert cli.main(["events", str(env.control), "--since", f"checkpoint {first}"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith(f"#{first} ") and '"two"' in out
    assert cli.main(["checkpoint", "next", "session", "--control", str(env.control)]) == 0
    [sig] = (env.control / "signals").glob("cli-*.signal")
    assert sig.read_text(encoding="utf-8") == "by: cli\ncheckpoint: next session\n"
