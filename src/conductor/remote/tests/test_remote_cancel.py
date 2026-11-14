"""`farm-seeds` safety (conductor 114): the commit its native exe runs from, a dirty-tree
warning, and a stopped run leaving no broker job and no native shard running."""

import subprocess
import sys
import time

import psutil
import pytest

from conductor.remote import client, settings

from .conftest import WAIT, git


def test_a_clean_tree_names_its_commit_and_a_dirty_one_warns(farm, capsys, tmp_path):
    farm.setup()
    head = git(tmp_path / "code", "rev-parse", "HEAD").strip()[:12]
    assert farm.run(0, 3)[0] == 0
    err = capsys.readouterr().err
    assert f"native from commit {head}" in err and client.DIRTY not in err
    (tmp_path / "code" / "README.md").write_text("changed\n", encoding="utf-8")
    assert farm.run(0, 3)[0] == 0
    err = capsys.readouterr().err
    assert f"native from commit {head} plus uncommitted changes" in err
    assert f"farm-seeds: {client.DIRTY} (README.md)" in err and "Commit before you farm" in err


def _start(farm, tmp_path, item="code/001_x"):
    """farm-seeds as its own process, shards of 10 s each, once the board holds its job."""
    env = {**__import__("os").environ, settings.ITEM_VAR: item}
    p = subprocess.Popen([sys.executable, "-P", "-m", "conductor.remote.client", "0", "40",
                          str(tmp_path / "out.txt"), "--sleep", "1"], cwd=str(tmp_path / "code"), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    end = time.time() + WAIT
    while time.time() < end and not (farm.board.jobs and psutil.Process(p.pid).children(recursive=True)):
        assert p.poll() is None, p.stderr.read().decode()
        time.sleep(0.05)
    return p, [c for c in psutil.Process(p.pid).children(recursive=True)]


def _gone(procs, timeout=15.0) -> bool:
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    return not alive


def test_cancel_closes_the_job_and_kills_its_native_shards(farm, tmp_path, capsys, monkeypatch):
    farm.setup(main_slots="2")
    p, kids = _start(farm, tmp_path)
    assert farm.board.jobs and kids
    monkeypatch.setenv(settings.ITEM_VAR, "code/other")          # another item's cancel leaves it be
    assert client.main(["--cancel"]) == 0 and farm.board.jobs and p.poll() is None
    monkeypatch.setenv(settings.ITEM_VAR, "code/001_x")
    assert client.main(["--cancel"]) == 0
    assert "cancelled 1 run(s) of item code/001_x" in capsys.readouterr().err
    assert not farm.board.jobs
    assert _gone(kids) and p.wait(15) is not None
    assert not list((farm.r.state / "jobs").glob(f"*/{client.OWNER}"))


@pytest.mark.skipif(sys.platform != "win32", reason="the kill-on-exit job is Windows'")
def test_a_hard_killed_run_takes_its_shards_with_it_and_the_next_run_closes_its_job(farm, tmp_path):
    farm.setup(main_slots="2")
    p, kids = _start(farm, tmp_path)
    p.kill()                                     # TerminateProcess: no handler runs
    p.wait(15)
    assert _gone(kids)                           # nothing left locking the native exe
    assert farm.board.jobs                       # its heartbeat has not lapsed yet
    assert farm.run(0, 3)[0] == 0                # the next run tidies it at once
    assert not farm.board.jobs


@pytest.mark.skipif(sys.platform != "win32", reason="Ctrl-Break is Windows' console stop")
def test_a_stop_signal_closes_the_job(farm, tmp_path):
    farm.setup(main_slots="2")
    env = {**__import__("os").environ, settings.ITEM_VAR: "code/001_x"}
    p = subprocess.Popen([sys.executable, "-P", "-m", "conductor.remote.client", "0", "40",
                          str(tmp_path / "out.txt"), "--sleep", "1"], cwd=str(tmp_path / "code"), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                         creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    end = time.time() + WAIT
    while time.time() < end and not farm.board.jobs:
        time.sleep(0.05)
    kids = psutil.Process(p.pid).children(recursive=True)
    p.send_signal(__import__("signal").CTRL_BREAK_EVENT)
    assert p.wait(15) == client.CANCELLED_EXIT
    assert "cancelled: job" in p.stderr.read().decode()
    assert not farm.board.jobs and _gone(kids)
