import subprocess

from conductor import executor, procs
from conductor.daemon import Daemon

from conftest import git


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


def test_missing_report_is_red(env):
    env.queue("001_quiet", "FAKE report none")
    env.tick_until(lambda: env.finished("code/001_quiet"))
    assert "no report written" in env.finished("code/001_quiet").detail["reasons"]


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


def test_restart_adopts_live_executor(env):
    env.queue("001_long", "FAKE sleep 3\nFAKE report done")
    env.tick_until(lambda: env.events("start"))
    pid = env.events("start")[0].detail["pid"]
    env.d.runs.clear()          # the daemon "dies"; its executor keeps running
    env.d.log.close()
    env.d = env.new_daemon()
    assert env.events("adopt") and "code/001_long" in env.d.runs
    env.tick_until(lambda: env.finished("code/001_long"))
    assert env.finished("code/001_long").detail["status"] == "done"
    assert len(env.events("start")) == 1
    assert not procs.alive(pid, None)


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


def test_restart_harvests_finished_executor(env):
    env.queue("001_quick", "FAKE report done")
    env.tick_until(lambda: env.events("start"))
    run = env.d.runs["code/001_quick"]
    run.popen.wait()
    env.d.runs.clear()
    env.d.log.close()
    env.d = env.new_daemon()
    assert env.events("recover")
    assert env.finished("code/001_quick").detail["status"] == "done"


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
