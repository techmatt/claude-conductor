import os
import subprocess
import time
from datetime import timedelta

import pytest

from conductor import executor, procs
from conductor.daemon import Daemon
from conductor.util import now, stamp

from conftest import git


@pytest.mark.slow
def test_items_run_in_order_one_at_a_time(env):
    env.queue("001_a", "FAKE sleep 0.5\nFAKE commit a.txt a")
    env.queue("002_b", "FAKE commit b.txt b")
    env.queue("003_c", "FAKE commit c.txt c", depends_on="001_a")
    env.tick_until(lambda: env.finished("code/003_c"))
    starts = [e.item for e in env.events("start")]
    assert starts == ["code/001_a", "code/002_b", "code/003_c"]
    seq = [(e.kind, e.item) for e in env.d.log.query(kinds=["start", "finish"])]
    assert seq == [(k, f"code/{n}") for n in ("001_a", "002_b", "003_c") for k in ("start", "finish")]
    assert all(e.detail["status"] == "done" for e in env.events("finish"))
    assert (env.control / "done" / "code" / "003_c.md").is_file()
    assert (env.control / "reports" / "code__001_a.log").is_file()
    assert len([e for e in env.events("commit") if e.detail["scope"] == "code"]) == 3


@pytest.mark.slow
def test_red_holds_queue_until_cleared(env):
    env.queue("001_bad", "FAKE report red")
    env.queue("002_next", "FAKE report done")
    env.tick_until(lambda: env.events("hold"))
    assert env.finished("code/001_bad").detail["status"] == "red"
    assert not env.d.log.last("start", item="code/002_next")
    inbox = (env.control / "inbox.md").read_text(encoding="utf-8")
    assert inbox.index("## Red") < inbox.index("## Holds") < inbox.index("## Finished")
    env.signal("clear.txt", "clear: code")
    env.tick_until(lambda: env.finished("code/002_next"))


def test_start_warns_about_runtime_paths_not_ignored(env):
    gi = env.control / ".gitignore"
    gi.write_text(gi.read_text(encoding="utf-8").replace("inbox.md\n", "").replace("signals/\n", ""),
                  encoding="utf-8", newline="\n")
    git(env.control, "commit", "-qam", "ignore less")
    assert not [e for e in env.events("warning") if "not git-ignored" in e.detail["note"]]
    env.d.log.close()
    env.d = env.new_daemon()
    [w] = [e for e in env.events("warning") if "not git-ignored" in e.detail["note"]]
    assert w.detail["note"] == "not git-ignored in the control folder: ['signals', 'inbox.md']"


def test_missing_report_is_red(env):
    env.queue("001_quiet", "FAKE report none")
    env.tick_until(lambda: env.finished("code/001_quiet"))
    assert "no report written" in env.finished("code/001_quiet").detail["reasons"]


@pytest.mark.slow
def test_after_red_continue_does_not_hold(env):
    env.queue("001_bad", "FAKE report red", after_red="continue")
    env.queue("002_next", "FAKE report done")
    env.tick_until(lambda: env.finished("code/002_next"))


def test_dependency_ended_red_holds(env):
    env.queue("001_bad", "FAKE report red", after_red="continue")
    env.queue("002_dep", "FAKE report done", depends_on="001")
    env.tick_until(lambda: env.events("hold"))
    assert "ended red" in env.events("hold")[0].detail["reason"]


def test_tamper_is_reverted_and_red(env):
    state = env.control / "state.md"
    env.queue("001_tamper", f"FAKE write {state.as_posix()} hacked\nFAKE report done")
    env.tick_until(lambda: env.finished("code/001_tamper"))
    assert state.read_text(encoding="utf-8") == "# state\n"
    tamper = env.events("tamper")
    assert tamper and tamper[0].item == "code/001_tamper" and tamper[0].paths == ["state.md"]
    assert env.finished("code/001_tamper").detail["status"] == "red"
    inbox = (env.control / "inbox.md").read_text(encoding="utf-8")
    first = inbox.split("## Red", 1)[1].strip().splitlines()[0]
    assert "changed the control folder" in first


def test_tamper_commit_is_reverted(env):
    head = git(env.control, "rev-parse", "HEAD").strip()
    body = (f"FAKE write {(env.control / 'state.md').as_posix()} hacked\n"
            f"FAKE write {(env.control / 'new.md').as_posix()} new\n")
    env.queue("001_commit", body)
    # The fake can only commit in cwd; commit in the control folder from here mid-run instead.
    env.d.tick()
    (env.control / "state.md").write_text("hacked\n", encoding="utf-8")
    git(env.control, "commit", "-qam", "evil")
    env.tick_until(lambda: env.finished("code/001_commit"))
    assert git(env.control, "rev-parse", "HEAD").strip() == head
    assert (env.control / "state.md").read_text(encoding="utf-8") == "# state\n"


def test_request_commits_and_is_removed(env):
    req = env.control / "requests" / "20261015-1200"
    req.mkdir(parents=True)
    (req / "state.md").write_bytes(b"# state\r\n\r\nupdated\r\n")
    (req / "request.md").write_text("---\ntargets: [state.md]\n---\nUpdate state\n", encoding="utf-8")
    env.d.tick()
    assert not req.exists()
    assert (env.control / "state.md").read_bytes() == b"# state\n\nupdated\n"
    assert git(env.control, "log", "-1", "--format=%s").strip() == "Update state"
    assert env.events("commit")[0].detail["scope"] == "control"


def test_bad_request_fails(env):
    req = env.control / "requests" / "bad"
    req.mkdir(parents=True)
    (req / "request.md").write_text("---\ntargets: [queue/x.md]\n---\nnope\n", encoding="utf-8")
    env.d.tick()
    assert env.events("request_failed")
    assert (env.control / "requests" / "bad.failed").is_dir()


def test_request_waits_for_late_target(env):
    req = env.control / "requests" / "20261015-1300"
    req.mkdir(parents=True)
    (req / "request.md").write_text("---\ntargets: [state.md, notes/plan.md]\n---\nOne call\n", encoding="utf-8")
    (req / "state.md").write_text("# state\n\none call\n", encoding="utf-8")
    env.d.tick()
    assert req.is_dir() and not env.events("request_failed") and not env.events("request")
    (req / "notes").mkdir()
    (req / "notes" / "plan.md").write_text("# plan\n", encoding="utf-8")
    env.d.tick()
    assert not req.exists() and not env.events("request_failed")
    assert env.events("request")[0].detail["targets"] == ["state.md", "notes/plan.md"]
    assert (env.control / "notes" / "plan.md").read_text(encoding="utf-8") == "# plan\n"
    assert git(env.control, "log", "-1", "--format=%s").strip() == "One call"


def test_request_missing_target_fails_after_wait(env, monkeypatch):
    from conductor import request
    monkeypatch.setattr(request, "REQUEST_WAIT_SECONDS", 0.3)
    req = env.control / "requests" / "late"
    req.mkdir(parents=True)
    (req / "request.md").write_text("---\ntargets: [state.md]\n---\nNever\n", encoding="utf-8")
    env.d.tick()
    assert req.is_dir() and not env.events("request_failed")
    env.tick_until(lambda: env.events("request_failed"))
    err = env.events("request_failed")[0].detail["error"]
    assert err.startswith("targets missing: state.md (waited ") and "nothing was applied" in err
    assert (env.control / "requests" / "late.failed").is_dir()


def test_a_fresh_request_applies_at_once_without_a_settle_timer(env, monkeypatch):
    # Files written this instant: request.md last, so its targets are whole. One tick applies it.
    from conductor import request
    monkeypatch.setattr(request, "FRESH_SECONDS", 2.0)
    req = env.control / "requests" / "20261015-1400"
    req.mkdir(parents=True)
    (req / "state.md").write_text("# state\n\nnow\n", encoding="utf-8")
    (req / "request.md").write_text("---\ntargets: [state.md]\n---\nNow\n", encoding="utf-8")
    env.d.tick()
    assert not req.exists() and env.events("request")


def test_an_unreadable_target_or_half_written_request_waits_without_a_receipt(env, monkeypatch):
    from pathlib import Path

    from conductor import request
    monkeypatch.setattr(request, "FRESH_SECONDS", 2.0)
    req = env.control / "requests" / "20261015-1410"
    req.mkdir(parents=True)
    (req / "state.md").write_text("# state\n\nheld\n", encoding="utf-8")     # not HEAD's: no wait for a changed copy
    (req / "request.md").write_text("---\ntargets: [state.md", encoding="utf-8")     # still being written
    env.d.tick()
    assert req.is_dir() and not env.events("request_failed")
    (req / "request.md").write_text("---\ntargets: [state.md]\n---\nHeld\n", encoding="utf-8")
    real = Path.read_bytes

    def held(p):                    # its writer still holds it open (a sharing violation on Windows)
        if p.name == "state.md" and p.parent == req:
            raise PermissionError(13, "in use")
        return real(p)

    monkeypatch.setattr(Path, "read_bytes", held)
    env.d.tick()
    assert req.is_dir() and not (env.control / "receipts" / "requests" / f"{req.name}.md").exists()
    monkeypatch.setattr(Path, "read_bytes", real)
    env.d.tick()
    assert not req.exists() and env.events("request")[0].detail["request"] == req.name


def test_waiting_request_is_not_overtaken(env):
    # An earlier request waiting on a missing target holds back a later one with an overlapping
    # target, so the later (newer) content is the one left committed.
    early, late = env.control / "requests" / "20261015-1500", env.control / "requests" / "20261015-1501"
    for d in (early, late):
        d.mkdir(parents=True)
    (early / "request.md").write_text("---\ntargets: [state.md]\n---\nEarly\n", encoding="utf-8")
    (late / "state.md").write_text("# state\n\nnewer\n", encoding="utf-8")
    (late / "request.md").write_text("---\ntargets: [state.md]\n---\nLate\n", encoding="utf-8")
    env.d.tick()
    assert early.is_dir() and late.is_dir() and not env.events("request")
    (early / "state.md").write_text("# state\n\nolder\n", encoding="utf-8")
    env.d.tick()
    assert not early.exists() and not late.exists()
    assert [e.detail["request"] for e in env.events("request")] == [early.name, late.name]
    assert (env.control / "state.md").read_text(encoding="utf-8") == "# state\n\nnewer\n"


def test_requests_apply_in_write_order_not_stamp_order(env):
    # A stamp ahead of the clock (aicomplete's 1035-reponame, written 10:27) must not jump a
    # request written after it: request.md's mtime orders them.
    ahead, now_ = env.control / "requests" / "20261028-1035-ahead", env.control / "requests" / "20261028-1031-later"
    for d, text, age in ((ahead, "# state\n\nfirst\n", 20), (now_, "# state\n\nsecond\n", 10)):
        d.mkdir(parents=True)
        (d / "state.md").write_text(text, encoding="utf-8")
        (d / "request.md").write_text("---\ntargets: [state.md]\n---\nx\n", encoding="utf-8")
        old = time.time() - age
        for p in (d / "state.md", d / "request.md"):
            os.utime(p, (old, old))
    env.d.tick()
    assert [e.detail["request"] for e in env.events("request")] == [ahead.name, now_.name]
    assert (env.control / "state.md").read_text(encoding="utf-8") == "# state\n\nsecond\n"


def test_a_request_identical_to_head_waits_for_a_changed_copy(env, monkeypatch):
    # The bridge can land a target's content after its call returned: a copy matching HEAD is
    # given SAME_WAIT_SECONDS to change before it is applied as identical.
    from conductor import request
    monkeypatch.setattr(request, "SAME_WAIT_SECONDS", 30.0)
    req = env.control / "requests" / "20261030-0811-p026"
    req.mkdir(parents=True)
    head = (env.control / "state.md").read_bytes()
    (req / "state.md").write_bytes(head)
    (req / "request.md").write_text("---\ntargets: [state.md]\n---\np026\n", encoding="utf-8")
    old = time.time() - 5
    os.utime(req / "request.md", (old, old))
    env.d.tick()
    assert req.is_dir() and not env.events("request")
    receipt = (env.control / "receipts" / "requests" / f"{req.name}.md").read_text(encoding="utf-8")
    assert "outcome: \"pending\"" in receipt and "identical to HEAD so far (state.md)" in receipt
    (req / "state.md").write_text("# state\n\nlanded\n", encoding="utf-8")
    env.d.tick()
    [ev] = env.events("request")
    assert not ev.detail["no_change"] and "identical" not in ev.detail
    assert (env.control / "state.md").read_text(encoding="utf-8") == "# state\n\nlanded\n"


def test_malformed_request_fails_at_once(env):
    req = env.control / "requests" / "broken"
    req.mkdir(parents=True)
    (req / "request.md").write_text("---\nmessage: no targets\n---\n", encoding="utf-8")
    env.d.tick()
    assert "targets" in env.events("request_failed")[0].detail["error"]
    assert (env.control / "requests" / "broken.failed").is_dir()


def test_a_request_folder_with_no_request_md_moves_to_stale_after_an_hour(env):
    from conductor import inbox, state
    reqs = env.control / "requests"
    for name in ("old", "young"):
        (reqs / name).mkdir(parents=True)
        (reqs / name / "state.md").write_text("# state\n", encoding="utf-8")
    (reqs / "stale" / "old").mkdir(parents=True)                       # an earlier one by that name
    hour_ago = time.time() - 3601
    for p in (reqs / "old" / "state.md", reqs / "old"):
        os.utime(p, (hour_ago, hour_ago))
    env.d.tick()
    [ev] = env.events("request_stale")
    assert ev.paths == ["requests/old", "requests/stale/old-2"] and ev.detail["files"] == ["state.md"]
    assert (reqs / "stale" / "old-2" / "state.md").is_file() and not (reqs / "old").exists()
    assert (reqs / "young" / "state.md").is_file()
    env.d.tick()
    assert len(env.events("request_stale")) == 1
    text = inbox.render(env.d.cfg, env.d.log, state.scan(env.d.cfg, env.d.log))
    assert "request folder `requests/old` had no `request.md` for an hour: moved to `requests/stale/old-2` (state.md)" in text


def test_extra_file_fails_once_targets_present(env):
    req = env.control / "requests" / "extra"
    req.mkdir(parents=True)
    (req / "state.md").write_text("# state\n", encoding="utf-8")
    (req / "stray.md").write_text("x\n", encoding="utf-8")
    (req / "request.md").write_text("---\ntargets: [state.md]\n---\nExtra\n", encoding="utf-8")
    env.d.tick()
    assert "stray.md" in env.events("request_failed")[0].detail["error"]


@pytest.mark.slow
def test_needs_planner_holds_until_read(env):
    env.queue("001_plan", "FAKE report done", needs_planner="true")
    env.queue("002_after", "FAKE report done")
    env.tick_until(lambda: env.events("hold"))
    fin = env.finished("code/001_plan")
    env.signal("read.txt", f"read: {fin.id}")
    env.tick_until(lambda: env.finished("code/002_after"))


def test_max_hours_kill(env):
    env.queue("001_slow", "FAKE sleep 60", max_hours=0.0003)
    env.tick_until(lambda: env.finished("code/001_slow"))
    assert env.events("kill")
    assert env.finished("code/001_slow").detail["status"] == "red"


@pytest.mark.slow
def test_default_max_hours_kill(env):
    # No max_hours of its own: the record carries the config default, not the item's None.
    env.queue("001_slow", "FAKE sleep 60")
    env.tick_until(lambda: "code/001_slow" in env.d.runs and env.d.runs["code/001_slow"].rec.get("pid"))
    run = env.d.runs["code/001_slow"]
    assert run.rec["max_hours"] == 1
    run.rec["deadline"] = stamp(now() - timedelta(minutes=1))
    env.tick_until(lambda: env.finished("code/001_slow"))
    assert env.events("kill")[0].detail["reason"] == "max_hours 1 reached"
    fin = env.finished("code/001_slow").detail
    assert fin["status"] == "red" and "killed at max_hours 1" in fin["reasons"]


def test_readonly_violation_is_red(env):
    env.queue("001_ro", "FAKE commit x.txt x", readonly="true")
    env.tick_until(lambda: env.finished("code/001_ro"))
    assert "readonly item changed the repo" in env.finished("code/001_ro").detail["reasons"]


def test_unknown_lock_and_invalid_hold(env):
    env.queue("001_lock", "FAKE report done", locks="[nope]")
    env.d.tick()
    assert "unknown lock" in env.events("hold")[0].detail["reason"]
    (env.control / "queue" / "code" / "001_lock.md").unlink()
    (env.control / "queue" / "code" / "002_broken.md").write_text("no front matter\n", encoding="utf-8")
    env.d.tick()
    assert env.events("invalid")


@pytest.mark.slow
def test_restart_adopts_live_executor(env):
    env.queue("001_long", f"{env.hold()}\nFAKE report done")
    env.tick_until(lambda: env.events("start"))
    pid = env.events("start")[0].detail["pid"]
    env.d.runs.clear()          # the daemon "dies"; its executor keeps running
    env.d.log.close()
    env.d = env.new_daemon()
    assert env.events("adopt") and "code/001_long" in env.d.runs
    env.release()
    env.tick_until(lambda: env.finished("code/001_long"))
    assert env.finished("code/001_long").detail["status"] == "done"
    assert len(env.events("start")) == 1
    env.tick_until(lambda: not procs.alive(pid, None))      # finished is seen a moment before it is gone


@pytest.mark.slow
def test_restart_with_dead_executor_is_red_never_rerun(env):
    env.queue("001_long", "FAKE sleep 30")
    env.queue("002_next", "FAKE report done")
    env.tick_until(lambda: env.events("start"))
    procs.kill_tree(env.events("start")[0].detail["pid"])
    env.d.runs.clear()
    env.d.log.close()
    env.d = env.new_daemon()
    fin = env.finished("code/001_long")
    assert fin.detail["status"] == "red" and "interrupted" in fin.detail["reasons"][0]
    env.tick_until(lambda: env.events("hold"))
    assert len(env.events("start")) == 1


@pytest.mark.slow
def test_restart_harvests_finished_executor(env):
    env.queue("001_quick", "FAKE report done")
    env.tick_until(lambda: env.events("start"))
    run = env.d.runs["code/001_quick"]
    run.popen.wait()
    env.d.runs.clear()
    env.d.log.close()
    env.d = env.new_daemon()
    assert env.events("recover")
    env.tick_until(lambda: env.finished("code/001_quick"))      # harvested, then merged
    assert env.finished("code/001_quick").detail["status"] == "done"


@pytest.mark.slow
def test_restart_requeues_never_launched(env):
    item = env.queue("001_x", "FAKE report done")
    env.d.log.close()
    rdir = env.control / "running" / "code"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "001_x.run.json").write_text(
        '{"item": "code/001_x", "repo": "code", "name": "001_x", "session_id": "nope-0000", "pid": null}',
        encoding="utf-8")
    item.replace(rdir / "001_x.md")
    env.d = env.new_daemon()
    assert env.events("requeue")
    env.tick_until(lambda: env.finished("code/001_x"))


def test_second_daemon_refuses(env):
    import json
    import sys
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        lock = env.d.cfg.state_dir / "daemon.json"
        lock.write_text(json.dumps({"pid": holder.pid, "create_time": procs.create_time(holder.pid)}),
                        encoding="utf-8")
        try:
            Daemon(env.d.cfg, echo=None).acquire()
        except Exception as exc:
            assert "already running" in str(exc)
        else:
            raise AssertionError("second daemon acquired the lock")
    finally:
        holder.kill()


def test_header_parsing(tmp_path):
    r = tmp_path / "r.md"
    r.write_text("---\nstatus: partial\nblocks: none\ncarry: add X\n---\nbody\n", encoding="utf-8")
    header, problem = executor.read_header(r)
    assert problem is None and header["status"] == "partial" and header["carry"] == "add X"
    r.write_text("status: great\nblocks: none\ncarry: none\n", encoding="utf-8")
    assert executor.read_header(r)[1]


def test_header_slow_line(tmp_path):
    r = tmp_path / "r.md"
    slow = "tests ~20m, full lane every change, mark the browser tests slow"
    for text in (f"status: done\nblocks: none\ncarry: none\nslow: {slow}\npick: picks/m.json\n\nbody\n",
                 f"---\nstatus: done\nblocks: none\ncarry: none\npick: picks/m.json\nslow: {slow}\n---\nbody\n"):
        r.write_text(text, encoding="utf-8")
        header, problem = executor.read_header(r)
        assert problem is None and header["slow"] == slow and header["pick"] == "picks/m.json"
    # Absent (an older report) is no problem; `none` is kept as said.
    r.write_text("status: done\nblocks: none\ncarry: none\n\nbody\n", encoding="utf-8")
    header, problem = executor.read_header(r)
    assert problem is None and "slow" not in header
    r.write_text("status: done\nblocks: none\ncarry: none\nslow: none\n\nbody\n", encoding="utf-8")
    assert executor.read_header(r)[0]["slow"] == "none"
    # Only straight after the three: a body line, or a fifth after a non-header fourth, is body.
    r.write_text("status: done\nblocks: none\ncarry: none\nThe build was fine.\nslow: the build\n", encoding="utf-8")
    header, problem = executor.read_header(r)
    assert problem is None and "slow" not in header
    r.write_text("status: done\nblocks: none\ncarry: none\nNotes.\npick: picks/m.json\n", encoding="utf-8")
    assert "pick" not in executor.read_header(r)[0]
    # `friction:` (59) takes any of the three optional places; `none` is kept as said.
    r.write_text(f"status: done\nblocks: none\ncarry: none\nslow: {slow}\nfriction: the README's test command is "
                 "stale; retried twice\npick: picks/m.json\n\nbody\n", encoding="utf-8")
    header, problem = executor.read_header(r)
    assert problem is None and header["friction"].startswith("the README's") and header["pick"] == "picks/m.json"
    r.write_text("status: done\nblocks: none\ncarry: none\nNotes.\nfriction: x\n", encoding="utf-8")
    assert "friction" not in executor.read_header(r)[0]
