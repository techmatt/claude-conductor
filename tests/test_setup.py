"""A repo's `setup` commands run off the daemon loop (`setup_run`), polled each tick."""

import json
import sys
import time

import pytest

from conductor import procs, state, worktrees

from conftest import git

WAIT = """\
import pathlib, sys, time
go, end = pathlib.Path(sys.argv[1]), time.time() + 60
while not go.exists() and time.time() < end:
    time.sleep(0.05)
pathlib.Path("setup.txt").write_text("ran\\n")
"""


STRAY = """\
import pathlib, subprocess, sys
flags = 0x8 | 0x200 if sys.platform == "win32" else 0      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], creationflags=flags,
                     start_new_session=sys.platform != "win32")
pathlib.Path(sys.argv[1]).write_text(str(p.pid))
"""


def slow_setup(env, stray: bool = False) -> None:
    """Setup that waits until `setup_go()`, then writes setup.txt in the worktree; with
    `stray`, it first leaves a detached sleeper in the worktree (its pid in `stray.pid`)."""
    script = env.root / "setup_wait.py"
    script.write_text(WAIT, encoding="utf-8")
    commands = [f'"{sys.executable}" "{script}" "{env.root / "setup-go"}"']
    if stray:
        (env.root / "setup_stray.py").write_text(STRAY, encoding="utf-8")
        commands.insert(0, f'"{sys.executable}" "{env.root / "setup_stray.py"}" "{env.root / "stray.pid"}"')
    listed = ", ".join(f"'{c}'".replace("\\", "/") for c in commands)
    env.configure(f"[repos.code]\npath = \"../code\"\nsetup = [{listed}]\n")


def setup_go(env) -> None:
    (env.root / "setup-go").touch()


def send_request(env, name: str, text: str) -> None:
    req = env.control / "requests" / name
    req.mkdir(parents=True)
    (req / "state.md").write_text(text, encoding="utf-8")
    (req / "request.md").write_text("---\ntargets: [state.md]\n---\nUpdate state\n", encoding="utf-8")


def restart(env) -> None:
    env.d.runs.clear()
    env.d.stages.clear()
    env.d.log.close()
    env.d = env.new_daemon()


@pytest.mark.slow
def test_signals_and_requests_move_while_setup_runs(env):
    slow_setup(env)
    env.queue("001_a", "FAKE check setup.txt\nFAKE commit a.txt a")
    env.tick_until(lambda: env.events("setup"))
    [ev] = env.events("setup")
    assert ev.item == "code/001_a" and procs.alive(ev.detail["pid"], None) and not ev.detail["stage"]
    rec = env.d.runs["code/001_a"].rec
    assert rec["phase"] == "setup" and rec["setup"]["state"] == "running" and rec["pid"] is None
    # While setup runs: a signal and a request are applied, an amendment waits for the prompt.
    env.signal("cp", "checkpoint: mid setup")
    send_request(env, "r1", "# state\n\nduring setup\n")
    p = env.control / "queue" / "code" / "001_a.amend.md"
    p.write_text("---\nby: planner\n---\n\nAlso this.\n", encoding="utf-8")
    env.tick_until(lambda: env.events("checkpoint") and env.events("request") and env.events("amended"))
    assert not env.d.log.last("start", item="code/001_a") and env.d.runs["code/001_a"].rec["phase"] == "setup"
    assert env.events("amended")[0].detail["state"] == "queued"
    assert git(env.control, "log", "-1", "--format=%s").strip() == "Update state"
    assert "001_a (setting up)" in (env.control / "inbox.md").read_text(encoding="utf-8")
    view = state.scan(env.d.cfg, env.d.log)
    assert view.running["code/001_a"]["phase"] == "setup"
    setup_go(env)
    env.tick_until(lambda: env.finished("code/001_a"))
    assert env.finished("code/001_a").detail["status"] == "done"
    assert (env.repo / "a.txt").is_file() and not env.worktree("001_a").exists()
    start = env.d.log.last("start", item="code/001_a")
    assert start.detail["pid"] and "setup_seconds" in start.detail and not start.detail.get("error")
    assert "Also this." in (env.control / "reports" / "code__001_a.prompt.md").read_text(encoding="utf-8")
    assert "$ " in (env.control / "reports" / "code__001_a.setup.log").read_text(encoding="utf-8")
    assert not list((env.control / ".conductor" / "setup").iterdir())       # spec and result removed


def test_setup_failure_is_a_red_launch_error(env):
    env.configure('[repos.code]\npath = "../code"\nsetup = ["exit 3"]\n')
    env.queue("001_a", "FAKE commit a.txt a")
    env.tick_until(lambda: env.finished("code/001_a"))
    fin = env.finished("code/001_a").detail
    assert fin["status"] == "red" and any("setup 'exit 3' exited 3" in r for r in fin["reasons"])
    assert "setup 'exit 3' exited 3" in env.d.log.last("start", item="code/001_a").detail["error"]
    assert not env.worktree("001_a").exists() and not (env.repo / "a.txt").exists()
    assert any(e.detail["action"] == "discarded" for e in env.events("worktree"))


@pytest.mark.slow
def test_restart_mid_setup_requeues_and_discards_a_setting_up_stage(env):
    slow_setup(env)
    env.queue("001_a", "FAKE commit a.txt a")
    env.queue("002_b", "FAKE commit b.txt b")
    env.tick_until(lambda: len(env.events("setup")) == 2)       # 001's run and 002's stage
    pids = [e.detail["pid"] for e in env.events("setup")]
    assert [e.detail["stage"] for e in env.events("setup")] == [False, True]
    assert env.d.stages["code/002_b"].setting_up and not env.d.stages["code/002_b"].exited()
    assert "001_a setting up" in state.decide(env.d.cfg, state.scan(env.d.cfg, env.d.log))["code"].reason
    restart(env)
    [req] = env.events("requeue")
    assert req.item == "code/001_a" and req.detail["reason"] == "daemon stopped during setup"
    assert any("during the stage's setup" in e.detail["note"] for e in env.events("warning"))
    assert not any(procs.alive(pid, None) for pid in pids)
    assert (env.control / "queue" / "code" / "001_a.md").is_file()
    setup_go(env)
    env.tick_until(lambda: env.finished("code/002_b"))
    assert [env.finished(i).detail["status"] for i in ("code/001_a", "code/002_b")] == ["done", "done"]
    assert len([e for e in env.events("start") if e.item == "code/001_a"]) == 1    # it never ran before


def test_a_process_still_starting_is_looked_at_again(monkeypatch):
    """Under load a brand-new executor's command line reads as empty, not None, for a moment."""
    class Starting:
        pid, info, reads = -1, {"cmdline": [], "create_time": time.time()}, [[], ["claude", "--session-id", "s1"]]

        def cmdline(self):
            return self.reads.pop(0)

        def parent(self):
            return None

    p = Starting()
    monkeypatch.setattr(procs.psutil, "process_iter", lambda attrs: [p])
    assert procs.find_by_marker("s1") is p


@pytest.mark.slow
@pytest.mark.parametrize("old_record", [False, True])
def test_a_save_lost_after_the_executor_spawned_is_adopted_not_requeued(env, monkeypatch, old_record):
    """Setup succeeds, the executor spawns, its pid save fails: the restart adopts it. With
    `old_record` the record on disk still says `phase: setup` (as before the save-as-exec
    order): `_recover_setup_run` finds the executor by its session marker all the same."""
    env.configure('[repos.code]\npath = "../code"\nsetup = ["echo ok"]\n')
    env.queue("001_a", f"{env.hold()}\nFAKE commit a.txt a")
    real, lost = env.d._save, []

    def save(run):
        if run.rec.get("pid") and not lost:
            lost.append(run.rec["pid"])
            raise PermissionError("running/code/001_a.run.json held open")
        real(run)

    monkeypatch.setattr(env.d, "_save", save)
    with pytest.raises(PermissionError):
        env.tick_until(lambda: False)
    rec_path = env.control / "running" / "code" / "001_a.run.json"
    rec = state.read_run(rec_path)
    assert rec["phase"] == "exec" and rec["pid"] is None and rec["setup"]["state"] == "ok"
    if old_record:
        rec["phase"] = "setup"
        rec_path.write_text(json.dumps(rec), encoding="utf-8")
    restart(env)
    assert not env.events("requeue")
    [adopt] = env.events("adopt")
    assert adopt.item == "code/001_a" and adopt.detail["pid"] == lost[0]
    assert env.d.runs["code/001_a"].rec["phase"] == "exec"
    env.release()
    env.tick_until(lambda: env.finished("code/001_a"))
    assert env.finished("code/001_a").detail["status"] == "done" and (env.repo / "a.txt").is_file()
    assert len(env.events("setup")) == 1 and not env.d.log.last("start", item="code/001_a")    # launched once


@pytest.mark.slow
def test_restart_mid_setup_reaps_a_stray_before_discarding(env):
    """A stray the setup left in the worktree (it outlives the runner's tree) is killed
    first: on Windows its cwd would hold the worktree and the discard would fail. There
    it dies with the runner's job (`procs.spawn`); elsewhere the reaper finds it."""
    slow_setup(env, stray=True)
    env.queue("001_a", "FAKE commit a.txt a")
    pid_file = env.root / "stray.pid"
    env.tick_until(lambda: pid_file.is_file() and pid_file.read_text())
    pid = int(pid_file.read_text())
    assert procs.alive(pid, None)
    restart(env)
    if not procs.WINDOWS:
        [reaped] = env.events("reaped")
        assert reaped.detail["context"] == "daemon stopped during setup" and pid in [r["pid"] for r in reaped.detail["processes"]]
    assert not procs.alive(pid, None) and not env.worktree("001_a").exists()
    assert env.events("requeue")[0].detail["reason"] == "daemon stopped during setup"


@pytest.mark.slow
def test_restart_mid_setup_with_a_locked_worktree_warns_and_requeues(env, monkeypatch):
    """A discard that fails (a file another process holds) is a warning and a leftover,
    never a crash in `recover` that would stop every daemon start."""
    slow_setup(env)
    env.queue("001_a", "FAKE commit a.txt a")
    env.tick_until(lambda: env.events("setup"))

    def locked(*args, **kwargs):
        raise PermissionError("[WinError 32] in use by another process")

    monkeypatch.setattr(worktrees, "discard", locked)
    restart(env)
    assert env.events("requeue")[0].detail["reason"] == "daemon stopped during setup"
    assert (env.control / "queue" / "code" / "001_a.md").is_file()
    assert any("not discarded" in e.detail["note"] for e in env.events("warning"))
    assert not (env.control / "running" / "code" / "001_a.run.json").exists()


@pytest.mark.slow
def test_a_stage_runs_its_setup_off_the_loop_then_resumes(env):
    slow_setup(env)
    env.queue("001_a", env.hold())
    env.queue("002_b", "FAKE check setup.txt\nFAKE commit b.txt b")
    env.tick_until(lambda: len(env.events("setup")) == 2)
    srec = state.scan(env.d.cfg, env.d.log).staged["code/002_b"]
    assert srec["phase"] == "staging" and srec["setup"]["state"] == "running" and srec["pid"] is None
    assert not env.events("stage")
    setup_go(env)
    env.tick_until(lambda: env.events("staged"))
    srec = state.scan(env.d.cfg, env.d.log).staged["code/002_b"]
    assert "setup.txt" in srec["untracked"] and srec["setup"]["state"] == "ok"   # recorded after setup
    assert "setup_seconds" in env.events("stage")[0].detail
    env.release()
    env.tick_until(lambda: env.finished("code/002_b"))
    assert env.d.log.last("start", item="code/002_b").detail["resumed"]
    assert len(env.events("setup")) == 3                # the resume runs setup again, after the move
    assert env.finished("code/002_b").detail["status"] == "done" and (env.repo / "b.txt").is_file()
