"""Memory: sampling, the pressure guard, leftover reaping, and a dead daemon made visible."""

import json
import os
import subprocess
import sys
import time

import psutil
import pytest

from conductor import cli, config, daemon, memory, procs, state
from conductor.util import stamp

GIB = 2 ** 30


def status(percent: float, free_gb: float) -> procs.MemoryStatus:
    return procs.MemoryStatus(commit=int(percent * GIB), limit=100 * GIB, available=int(free_gb * GIB), total=32 * GIB)


def gone(pid: int, timeout: float = 5.0) -> bool:
    """A killed process is gone (or a zombie). Windows can keep a killed pid visible for a
    moment, and it can vanish between `pid_exists` and `status`, so poll and allow both."""
    end = time.time() + timeout
    while True:
        try:
            if not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        if time.time() > end:
            return False
        time.sleep(0.1)


def guarded(cfg: config.Config) -> config.Config:
    cfg.memory_pressure_percent, cfg.memory_pressure_free_gb = 85, 3
    cfg.memory_critical_percent, cfg.memory_critical_free_gb = 93, 1.5
    return cfg


def test_levels_hold_until_clear_by_the_slack(env):
    cfg = guarded(env.d.cfg)
    assert memory.level(status(80, 10), cfg) == "ok"
    assert memory.level(status(86, 10), cfg) == "pressure"
    assert memory.level(status(50, 2.5), cfg) == "pressure"           # free RAM alone
    assert memory.level(status(94, 10), cfg) == "critical"
    assert memory.level(status(50, 1), cfg) == "critical"
    # Leaving a level takes a margin: 83% stays pressure, 81% is ok.
    assert memory.level(status(83, 10), cfg, "pressure") == "pressure"
    assert memory.level(status(81, 10), cfg, "pressure") == "ok"
    assert memory.level(status(91, 10), cfg, "critical") == "critical"
    assert memory.level(status(89, 10), cfg, "critical") == "pressure"
    cfg.memory_pressure_percent = cfg.memory_pressure_free_gb = 0     # 0 turns a threshold off
    assert memory.level(status(90, 2), cfg) == "ok"


def test_memory_status_reads_the_box():
    s = procs.memory_status()
    assert 0 < s.commit <= s.limit and 0 < s.available <= s.total


def test_kinds():
    assert procs.kind("claude.exe") == "claude"
    assert procs.kind("chrome.exe") == "chromium"
    assert procs.kind("node.exe", ["node", "C:/x/node_modules/vite/bin/vite.js", "preview"]) == "vite"
    assert procs.kind("node.exe", ["node", "playwright", "test"]) == "playwright"
    assert procs.kind("node") == "node"
    assert procs.kind("tail.exe") == "shell"
    assert procs.kind("Code.exe") == "other"


def test_samples_record_each_items_tree_and_the_heartbeat(env):
    """The sampler sees an executor's child holding memory, by kind; the heartbeat lands
    in daemon.json; the child, left running when the executor exits, is reaped."""
    env.d.cfg.memory_sample_seconds = 0.1
    env.d.acquire()
    env.queue("001_hog", f"FAKE hog 200 60\n{env.hold()}")
    item = "code/001_hog"

    def hog_seen():
        rec = memory.latest(env.cfg.state_dir)
        mb = (rec or {}).get("items", {}).get(item, {}).get("kinds", {}).get("python", [0, 0])[1]
        return mb >= 200

    env.tick_until(hog_seen, timeout=60)
    lock = json.loads((env.cfg.state_dir / "daemon.json").read_text(encoding="utf-8"))
    assert lock["beat"] and lock["pid"] == os.getpid()
    info = state.daemon_info(env.d.cfg)
    assert info.alive and not info.stale and info.beat == lock["beat"]
    env.release()
    env.tick_until(lambda: env.finished(item))
    report = (env.control / "reports" / "code__001_hog.md").read_text(encoding="utf-8")
    hog = int(report.split("HOG=")[1].split()[0])
    reaped = env.events("reaped")
    assert reaped and hog in {p["pid"] for p in reaped[0].detail["processes"]}
    assert reaped[0].detail["private_mb"] >= 200
    assert gone(hog)
    env.d.release()
    assert not state.daemon_info(env.d.cfg).died      # a clean stop leaves no lock


def test_samples_count_what_an_item_detached_in_its_worktree(env):
    """Found live in the sandbox proof: a hog started with `( cmd & )` has no live
    parent, so a tree walk misses it; the item's orphans are counted too."""
    env.d.cfg.memory_sample_seconds = 0.1
    env.queue("001_detach", f"FAKE detach-hog 150 60\n{env.hold()}")
    item = "code/001_detach"

    def hog_seen():
        rec = memory.latest(env.cfg.state_dir)
        return (rec or {}).get("items", {}).get(item, {}).get("kinds", {}).get("python", [0, 0])[1] >= 150

    env.tick_until(hog_seen, timeout=60)
    env.release()
    env.tick_until(lambda: env.finished(item))
    assert env.events("reaped")


@pytest.mark.slow
def test_pressure_pauses_starts_and_tells_running_items(env):
    guarded(env.d.cfg)
    env.d.cfg.memory_sample_seconds = 0.05
    env.d.cfg.memory_calm_seconds = 0
    box = {"s": status(50, 10)}
    env.d.memory_probe = lambda: box["s"]
    env.queue("001_busy", "FAKE until-message 30")
    env.tick_until(lambda: "code/001_busy" in env.d.runs and env.d.runs["code/001_busy"].pid())
    box["s"] = status(88, 10)
    env.queue("002_look", "FAKE report done", readonly="true")
    env.tick_until(lambda: env.events("memory"))
    ev = env.events("memory")[-1]
    assert ev.detail["level"] == "pressure" and ev.detail["was"] == "ok" and ev.detail["percent"] == 88
    tells = [e for e in env.events("tell") if e.detail.get("by") == "conductor"]
    assert len(tells) == 1 and tells[0].item == "code/001_busy" and "cut parallelism" in tells[0].detail["text"]
    assert any("memory pressure" in t[0] for t in env.toasts)
    # The busy item gets the message and keeps running; the readonly one never starts.
    env.tick_until(lambda: env.events("delivered"))
    for _ in range(10):
        env.d.tick()
        time.sleep(0.05)
    assert not env.d.log.last("start", item="code/002_look")
    env.d.write_inbox(force=True)
    assert "PRESSURE: launches and stages paused" in (env.control / "inbox.md").read_text(encoding="utf-8")
    box["s"] = status(95, 10)
    env.tick_until(lambda: env.events("memory_critical"))
    assert any("CRITICAL" in t[0] for t in env.toasts)
    box["s"] = status(40, 10)
    env.tick_until(lambda: env.d.log.last("start", item="code/002_look"))
    assert [e.detail["level"] for e in env.events("memory")] == ["pressure", "ok"]
    assert not env.events("kill")


def test_a_level_rises_at_once_and_falls_only_once_calm(env):
    """Found live: a leaking service swung this box's free RAM by gigabytes every few
    minutes, so a fall waits for `memory_calm_seconds` of clear readings."""
    cfg = guarded(env.d.cfg)
    cfg.memory_sample_seconds, cfg.memory_calm_seconds = 0.01, 0.5
    box = {"s": status(50, 2)}
    env.d.memory_probe = lambda: box["s"]
    env.d.sample_memory(force=True)
    assert env.d.mem_level == "pressure"
    box["s"] = status(50, 10)
    env.d.sample_memory(force=True)
    assert env.d.mem_level == "pressure"            # clear, not yet calm
    box["s"] = status(50, 2)
    env.d.sample_memory(force=True)                 # a dip again restarts the clock
    box["s"] = status(50, 10)
    env.d.sample_memory(force=True)
    time.sleep(0.3)
    env.d.sample_memory(force=True)
    assert env.d.mem_level == "pressure"
    time.sleep(0.3)
    env.d.sample_memory(force=True)
    assert env.d.mem_level == "ok"
    box["s"] = status(50, 1)
    env.d.sample_memory(force=True)
    assert env.d.mem_level == "critical"            # straight up, no wait
    assert [e.detail["level"] for e in env.events("memory")] == ["pressure", "ok"]
    assert len(env.events("memory_critical")) == 1


def test_inbox_and_status_carry_the_memory_line(env, capsys):
    env.d.write_inbox(force=True)
    now_section = (env.control / "inbox.md").read_text(encoding="utf-8").split("## Now")[1]
    assert now_section.splitlines()[1].startswith("- memory ") and "GiB committed" in now_section
    assert cli.main(["status", str(env.control)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[1].startswith("memory ") and out[1].endswith("· ok")


def test_strays_are_orphans_found_by_folder_and_time(tmp_path):
    folder = tmp_path / "wt"
    folder.mkdir()
    since = time.time() - 1
    sleep = [sys.executable, "-c", "import time; time.sleep(60)"]
    orphan = procs.spawn_detached(sleep, folder, tmp_path / "out.log")       # its launcher is gone
    child = subprocess.Popen(sleep, cwd=folder)                               # its parent (this test) is not
    try:
        end = time.time() + 1
        found = {s.pid for s in procs.strays(folder, since, end + 5, set())}
        assert orphan.pid in found and child.pid not in found
        assert orphan.pid not in {s.pid for s in procs.strays(folder, since, end + 5, {orphan.pid})}
        assert orphan.pid not in {s.pid for s in procs.strays(folder, end + 30, end + 60, set())}   # outside the window
        assert orphan.pid not in {s.pid for s in procs.strays(tmp_path / "other", since, end + 5, set())}
    finally:
        procs.kill_tree(orphan.pid)
        child.kill()
        child.wait()


def test_leftovers_are_reaped_when_the_item_finishes(env):
    env.queue("001_leaves", "FAKE leave 60")
    env.tick_until(lambda: env.finished("code/001_leaves"))
    report = (env.control / "reports" / "code__001_leaves.md").read_text(encoding="utf-8")
    left = int(report.split("LEFT=")[1].split()[0])
    ev = env.events("reaped")[0]
    # A venv's python.exe is a launcher with the real interpreter as its child: both are left.
    assert ev.item == "code/001_leaves" and left in [p["pid"] for p in ev.detail["processes"]]
    assert gone(left)
    # The worktree, no longer held, is removed with the merge.
    assert not env.worktree("001_leaves").exists()


def test_a_dead_daemon_is_named_at_the_next_start(env):
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    created = procs.create_time(gone.pid)
    gone.wait()
    env.cfg.state_dir.mkdir(parents=True, exist_ok=True)
    (env.cfg.state_dir / "daemon.json").write_text(json.dumps({
        "pid": gone.pid, "create_time": created, "started": "2026-10-19T23:00:00-07:00",
        "beat": "2026-10-20T02:16:30-07:00"}), encoding="utf-8")
    memory.store(env.cfg.state_dir, {"at": "2026-10-20T02:16:30-07:00", "level": "pressure", "commit": 34.0,
                                     "limit": 36.6, "free": 1.9, "items": {}}, 24)
    info = state.daemon_info(env.cfg)
    assert info.died and not info.alive and info.beat == "2026-10-20T02:16:30-07:00"
    d = daemon.Daemon(config.load(env.control), echo=None, notifier=env.notifier)
    d.acquire()
    d.startup()
    try:
        ev = d.log.last("daemon_died")
        assert ev.detail["pid"] == gone.pid and ev.detail["last_beat"] == "2026-10-20T02:16:30-07:00"
        assert ev.detail["memory"]["free"] == 1.9 and ev.detail["gap"]
        assert any("had died" in t[0] for t in env.toasts)
        assert state.daemon_info(env.cfg).alive
    finally:
        d.release()


def test_a_daemon_under_claude_code_warns(env, monkeypatch):
    monkeypatch.setattr(procs, "claude_ancestor", lambda: psutil.Process(os.getpid()))
    env.d.log.close()
    env.d = env.new_daemon()
    assert any("runs under a Claude Code session" in e.detail["note"] for e in env.events("warning"))


def test_spawn_detached_leaves_no_parent(tmp_path):
    """A tree kill of the caller (Claude Code reaping the app) must not reach it."""
    p = procs.spawn_detached([sys.executable, "-c", "import time; time.sleep(60)"], tmp_path, tmp_path / "out.log")
    try:
        assert p.poll() is None
        assert p.pid not in procs.family(os.getpid())
        assert os.getpid() not in {a.pid for a in procs.ancestors(p.pid)}
    finally:
        procs.kill_tree(p.pid)
    assert p.poll() == -1


@pytest.mark.parametrize("beat_age, stale", [(10, False), (600, True)])
def test_a_stale_heartbeat_reads_as_not_answering(env, beat_age, stale):
    from datetime import timedelta
    from conductor.util import now
    env.cfg.state_dir.mkdir(parents=True, exist_ok=True)
    (env.cfg.state_dir / "daemon.json").write_text(json.dumps({
        "pid": os.getpid(), "create_time": procs.create_time(os.getpid()), "started": stamp(),
        "beat": stamp(now() - timedelta(seconds=beat_age))}), encoding="utf-8")
    assert state.daemon_info(env.cfg).stale is stale
