"""The daemon restarting itself when its code changes (`daemon_restart`)."""

import json
import os
import subprocess
import sys
import time

import pytest

from conductor import cli, config, daemon, daemon_restart, procs, targets
from conductor.daemon import Daemon
from conductor.daemon_restart import checkout_blockers, code_version
from conductor.inbox import describe

from conftest import git, init_repo


def stand_in(d: Daemon, versions, spawned: list, import_error=None, spawn_error=None,
             heads=("abc1234 the new code",), ready=True, checkout=()) -> None:
    """Give `d` a code version and HEAD from `versions` and `heads` (each probe takes the
    next, the last repeats), an import check (`import_error`: one, or a list taken in
    turn), a checkout probe and a spawn that record instead of running. `ready`: the
    spawned daemon writes restart.ready as `run --after` would."""
    def take(values):
        return values.pop(0) if len(values) > 1 else values[0]

    def spawn(control, log, after=None):
        if spawn_error:
            raise OSError(spawn_error)
        spawned.append((control, log, after))
        if ready:
            (control / ".conductor" / daemon_restart.READY_FILE).write_text("4242\n", encoding="utf-8")
        return procs.Detached(4242, None)
    heads = list(heads)
    errors = list(import_error) if isinstance(import_error, list) else [import_error]
    d.code_probe = lambda: take(versions)
    d.head_probe = lambda: take(heads)
    d.import_check = lambda cwd: take(errors)
    d.checkout_probe = lambda: list(checkout)
    d.restart_spawner = spawn


@pytest.fixture
def started(env, monkeypatch):
    """The env's daemon as if it had started on code version v1."""
    monkeypatch.setattr(daemon_restart, "CODE_CHECK_SECONDS", 0)
    env.d.code_version, env.d.code_head = "v1", "0000000 the old code"
    return env


@pytest.mark.slow
def test_restart_on_code_change_keeps_running_and_staged_items(env, monkeypatch):
    monkeypatch.setattr(daemon_restart, "CODE_CHECK_SECONDS", 0)
    env.queue("001_long", env.hold() + "\nFAKE commit a.txt a")
    env.queue("002_next", "FAKE commit b.txt b")
    env.tick_until(lambda: env.events("staged"))
    pid = env.events("start")[0].detail["pid"]
    env.d.runs.clear()
    env.d.log.close()
    spawned = []
    d = Daemon(config.load(env.control), echo=env.lines.append, notifier=env.notifier)
    # Startup, one unchanged check, then changed.
    stand_in(d, ["v1", "v1", "v2"], spawned, heads=["0000000 the old code", "abc1234 the new code"])
    d.run_forever()                               # returns on its own, after the spawn
    assert spawned == [(env.control, env.control / ".conductor" / "daemon.out.log", os.getpid())]
    assert not (env.control / ".conductor" / "daemon.json").exists()
    env.d = env.new_daemon()
    [ev] = env.events("daemon_restart")
    assert ev.detail["reason"] == "code changed: 0000000 → abc1234 the new code"
    assert ev.detail["from"] == "v1" and ev.detail["to"] == "v2" and ev.detail["new_pid"] == 4242
    assert ev.detail["running"] == ["code/001_long"] and ev.detail["staging"] == []
    assert not env.events("daemon_stop") and "daemon restarting" in describe(ev)
    assert procs.alive(pid, None)
    assert [e.item for e in env.events("adopt")][-1:] == ["code/001_long"]
    env.release()
    env.tick_until(lambda: env.finished("code/002_next"))
    assert env.finished("code/001_long").detail["status"] == "done"
    assert env.d.log.last("start", item="code/002_next").detail["resumed"]     # the stage was kept
    assert not env.events("requeue") and not any("stage discarded" in e.detail["note"] for e in env.events("warning"))


def test_no_restart_while_the_code_is_unchanged_or_unknown(started):
    spawned = []
    stand_in(started.d, ["v1"], spawned)
    assert not started.d.restart_due()
    started.d.code_version = None                 # not a git checkout: the feature is off
    stand_in(started.d, ["v2"], spawned)
    assert not started.d.restart_due() and not spawned


def test_code_check_is_throttled(started, monkeypatch):
    monkeypatch.setattr(daemon_restart, "CODE_CHECK_SECONDS", 60)
    spawned = []
    stand_in(started.d, ["v1", "v2"], spawned)
    assert not started.d.restart_due()            # the first check sees v1
    assert not started.d.restart_due() and not spawned      # the next is a minute away


@pytest.mark.parametrize("blocker", ["resolve", "run_setup", "stage_setup", "reorder", "picker"])
def test_unsafe_restart_waits_and_warns_once_past_patience(started, monkeypatch, blocker):
    env, d = started, started.d
    rdir = env.control / "running" / "code"
    rdir.mkdir(parents=True, exist_ok=True)
    made = {
        "resolve": (rdir / "001_a.run.json", {"item": "code/001_a", "phase": "merge", "resolve": {"state": "running"}}),
        "run_setup": (rdir / "001_a.run.json", {"item": "code/001_a", "phase": "setup"}),
        "stage_setup": (rdir / "001_a.stage.json", {"item": "code/001_a", "phase": "staging",
                                                     "setup": {"state": "running"}}),
        "reorder": (d._reorder_journal, {"where": "code"}),
    }
    if blocker == "picker":
        d.picker.httpd = object()                 # a page served, opened, never saved
        d.picker.entries["colours"] = (None, None)
        d.log.append("pick_open", name="colours", manifest="code/picks/colours.json")
    else:
        path, rec = made[blocker]
        path.write_text(json.dumps(rec), encoding="utf-8")
    spawned = []
    stand_in(d, ["v2"], spawned)
    assert not d.restart_due() and not d.restart_due() and not spawned
    assert not env.events("warning")
    monkeypatch.setattr(daemon_restart, "RESTART_PATIENCE_SECONDS", 0)
    assert not d.restart_due() and not d.restart_due()
    [warn] = env.events("warning")
    assert "keeps waiting" in warn.detail["note"]
    assert {"resolve": "resolving a prose conflict", "run_setup": "code/001_a: its setup is running",
            "stage_setup": "code/001_a: its setup is running", "reorder": "a reorder of code",
            "picker": "picker colours"}[blocker] in warn.detail["note"]
    if blocker == "picker":
        d.log.append("pick_saved", name="colours")
        d.picker.httpd = None
    else:
        made[blocker][0].unlink()
    assert d.restart_due() and spawned and env.events("daemon_restart")


def test_code_that_does_not_import_is_not_restarted_into(started):
    spawned = []
    stand_in(started.d, ["v2"], spawned, import_error="SyntaxError: invalid syntax")
    assert not started.d.restart_due() and not started.d.restart_due()
    [warn] = started.events("warning")
    assert "does not import: SyntaxError" in warn.detail["note"] and not spawned
    stand_in(started.d, ["v3"], spawned)          # the next commit fixes it
    assert started.d.restart_due() and started.events("daemon_restart")[0].detail["to"] == "v3"


def test_failed_spawn_keeps_running_and_is_retried_after_a_backoff(started, monkeypatch):
    spawned = []
    stand_in(started.d, ["v2"], spawned, spawn_error="no launcher")
    assert not started.d.restart_due() and not started.d.restart_due()
    [warn] = started.events("warning")
    assert "could not start: no launcher" in warn.detail["note"] and not started.events("daemon_restart")
    assert "tries again every 5m" in warn.detail["note"]
    monkeypatch.setattr(daemon_restart, "RESTART_RETRY_SECONDS", 0)
    assert not started.d.restart_due()            # retried, fails again: no second warning
    assert len(started.events("warning")) == 1
    stand_in(started.d, ["v2"], spawned)          # the same version, now spawnable
    assert started.d.restart_due() and started.events("daemon_restart")[0].detail["to"] == "v2"


def test_an_import_failure_from_the_working_tree_is_retried(started, monkeypatch):
    spawned = []
    stand_in(started.d, ["v2"], spawned, import_error=["SyntaxError: half-written", None])
    assert not started.d.restart_due() and not started.d.restart_due() and not spawned
    assert "tries again" in started.events("warning")[0].detail["note"]
    monkeypatch.setattr(daemon_restart, "RESTART_RETRY_SECONDS", 0)
    assert started.d.restart_due() and spawned


def test_imports_are_checked_again_just_before_the_spawn(started):
    env, d = started, started.d
    spawned, blocked = [], ["conductor's checkout has uncommitted changes in src/ or pyproject.toml (1 paths)"]
    stand_in(d, ["v2"], spawned, import_error=[None, "ImportError: cannot import name 'x'"], checkout=blocked)
    assert not d.restart_due()                    # imports at detection, blocked by the dirty tree
    blocked.clear()
    assert not d.restart_due() and not spawned    # the tree changed meanwhile: checked again, refused
    [warn] = env.events("warning")
    assert "does not import: ImportError" in warn.detail["note"] and not env.events("daemon_restart")


def test_no_handshake_refuses_the_version_and_keeps_running(started, monkeypatch):
    env, d = started, started.d
    monkeypatch.setattr(daemon_restart, "RESTART_READY_SECONDS", 0.6)
    stale = env.control / ".conductor" / daemon_restart.READY_FILE
    stale.write_text("1\n", encoding="utf-8")     # a copy from an earlier restart never counts
    hung = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        d.code_probe, d.head_probe, d.checkout_probe = (lambda: "v2"), (lambda: "abc1234 new"), (lambda: [])
        d.import_check = lambda cwd: None
        d.restart_spawner = lambda control, log, after=None: procs.Detached(hung.pid, procs.create_time(hung.pid))
        assert not d.restart_due()
        assert hung.wait(timeout=10) is not None  # killed: it never takes the lock later
    finally:
        hung.kill()
    [warn] = env.events("warning")
    assert "did not report ready within 0s" in warn.detail["note"] and ".conductor/daemon.out.log" in warn.detail["note"]
    assert not env.events("daemon_restart") and not stale.exists()


def test_a_dead_new_daemon_is_noticed_at_once(started):
    spawned = []
    stand_in(started.d, ["v2"], spawned, ready=False)     # pid 4242 is not alive: no wait for the timeout
    t0 = time.monotonic()
    assert not started.d.restart_due() and spawned and time.monotonic() - t0 < 5
    assert "did not report ready" in started.events("warning")[0].detail["note"]
    assert not started.events("daemon_restart")


def test_a_stop_signal_written_during_the_restart_is_this_daemons(started):
    env, d = started, started.d
    spawned = []
    stand_in(d, ["v2"], spawned)
    env.signal("stop.signal", "stop: daemon")     # after the tick's signals, before the spawn
    # Settled: on Windows a fresh file's mtime can read a hair past time.time(), unsettled even at 0s.
    old = time.time() - 5
    os.utime(env.control / "signals" / "stop.signal", (old, old))
    assert not d.restart_due() and not spawned and d.stop_requested is not None
    assert not d.restart_due() and not spawned and not env.events("daemon_restart")


@pytest.mark.parametrize("blocker", ["checkout", "run_target"])
def test_a_dirty_checkout_or_a_starting_run_target_blocks_the_restart(started, blocker):
    d = started.d
    spawned, dirty = [], ["conductor's checkout: a rebase is in progress"]
    stand_in(d, ["v2"], spawned, checkout=dirty if blocker == "checkout" else ())
    if blocker == "run_target":
        d.runner.pending["web"] = targets.Pending(time.monotonic() + 60, time.monotonic(), None)
    assert not d.restart_due() and not spawned
    assert d._restart_blockers() == {"checkout": dirty, "run_target": ["run target web: starting, not ready yet"]}[blocker]
    dirty.clear()
    d.runner.pending.clear()
    assert d.restart_due() and spawned


def test_checkout_blockers_read_the_working_tree_and_git_operations(tmp_path):
    root = tmp_path / "checkout"
    init_repo(root, {"README.md": "x\n", "pyproject.toml": "[project]\n"})
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("a = 1\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "src")
    assert checkout_blockers(root) == []
    (root / "README.md").write_text("y\n", encoding="utf-8")
    assert checkout_blockers(root) == []                        # outside src/ and pyproject: fine
    (root / "src" / "b.py").write_text("b = 1\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    [dirty] = checkout_blockers(root)
    assert "uncommitted changes in src/ or pyproject.toml (2 paths)" in dirty
    git(root, "add", "-A")
    git(root, "commit", "-qm", "b")
    (root / ".git" / "MERGE_HEAD").write_text(git(root, "rev-parse", "HEAD"), encoding="utf-8")
    assert checkout_blockers(root) == ["conductor's checkout: a merge is in progress"]
    (root / ".git" / "MERGE_HEAD").unlink()
    wt = tmp_path / "wt"
    git(root, "worktree", "add", "-q", str(wt))
    (root / ".git" / "worktrees" / "wt" / "rebase-merge").mkdir()   # the worktree's own git dir
    assert checkout_blockers(wt) == ["conductor's checkout: a rebase is in progress"]
    assert checkout_blockers(root) == []


def test_amend_during_setup_says_it_goes_in_when_setup_finishes(env, capsys):
    # cli amend's message (this test lives here with the rest of prompt 28's restart fixes).
    rdir = env.control / "running" / "code"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "001_a.md").write_text("FAKE report done\n", encoding="utf-8")
    rec = {"item": "code/001_a", "phase": "setup"}
    (rdir / "001_a.run.json").write_text(json.dumps(rec), encoding="utf-8")
    assert cli.main(["amend", "001", "more", "--control", str(env.control)]) == 0
    out = capsys.readouterr().out
    assert "in its prompt when its setup finishes" in out and "next tool call" not in out
    (rdir / "001_a.run.json").write_text(json.dumps({**rec, "staged": True}), encoding="utf-8")
    assert cli.main(["amend", "001", "again", "--control", str(env.control)]) == 0
    assert "in the resume message when its setup finishes" in capsys.readouterr().out


def test_code_version_counts_committed_package_and_deps_only(tmp_path):
    root = tmp_path / "checkout"
    assert code_version(tmp_path) is None                       # not a git repo
    init_repo(root, {"README.md": "x\n", "pyproject.toml": "[project]\n"})
    assert code_version(root) is None                           # no src/
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("a = 1\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "src")
    v1 = code_version(root)
    assert v1 and len(v1.split()) == 2
    (root / "src" / "a.py").write_text("a = 2\n", encoding="utf-8")
    assert code_version(root) == v1                             # uncommitted work never counts
    (root / "README.md").write_text("y\n", encoding="utf-8")
    git(root, "commit", "-qm", "docs", "--", "README.md")
    assert code_version(root) == v1                             # nor a commit outside src/ and pyproject
    git(root, "commit", "-qm", "code", "--", "src/a.py")
    v2 = code_version(root)
    assert v2 != v1
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    git(root, "commit", "-qam", "deps")
    assert code_version(root) not in (v1, v2)
    assert code_version(root / "src") is None                   # src/ and pyproject are the folder's own


def test_run_after_waits_for_the_old_daemon(env, monkeypatch):
    old = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
    seen = []
    ready = env.control / ".conductor" / daemon_restart.READY_FILE
    monkeypatch.setattr(daemon.Daemon, "run_forever", lambda self: seen.append(procs.alive(old.pid, None)))
    t0 = time.monotonic()
    assert cli.main(["run", str(env.control), "--after", str(old.pid)]) == 0
    old.wait()
    assert seen == [False] and time.monotonic() - t0 >= 1.0
    assert ready.read_text(encoding="utf-8") == f"{os.getpid()}\n"     # written before the wait: the handshake
    env.d._read_code()                            # startup, under the lock: the handover is done
    assert not ready.exists()


def test_wait_exit_gives_up(monkeypatch):
    old = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert not cli.wait_exit(old.pid, 0.3)
    finally:
        old.kill()
        old.wait()
