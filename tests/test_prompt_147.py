"""Prompt 147: disk retention (a reviewed kept worktree's build output, farm jobs, bundles and
trees), the broker's job order, native-only and shard-size jobs, the job record from the
start, heredoc scripts into `out/`, and the time limit up front."""

import json
import os
import time
from datetime import datetime
from pathlib import Path

from conductor import disk, executor, rules
from conductor.items import Item
from conductor.remote import client, retention, settings
from conductor.remote.board import Board

from conftest import git
from test_preamble import SHORT, render

GB = 1e9


# ------------------------------------------------------------------ kept worktrees
def _kept(env, name="012_red") -> Path:
    """A red item's kept worktree with an ignored target/ and web/node_modules/, and a tracked `target` doc."""
    wt = env.worktree(name)
    wt.parent.mkdir(parents=True, exist_ok=True)
    git(env.repo, "worktree", "add", "-q", "-b", f"conductor/{name}", str(wt))
    (wt / ".gitignore").write_text("target/\nnode_modules/\nout/\n", encoding="utf-8")
    for rel in ("target/release/sim.exe", "web/node_modules/x/index.js", "out/target/keep.txt", "src/main.rs"):
        (wt / rel).parent.mkdir(parents=True, exist_ok=True)
        (wt / rel).write_bytes(b"x" * 1000)
    env.d.log.append("finish", item=f"code/{name}", repo="code", status="red")
    return wt


def test_a_reviewed_kept_worktree_loses_its_build_output_once_and_logs_it(env):
    wt = _kept(env)
    fin = env.d.log.last("finish", item="code/012_red")
    assert disk.due(env.cfg, env.d.log, set()) == []                 # not reviewed: still for inspection
    env.d.log.append("reviewed", item="code/012_red", finish=fin.id, by="planner")
    assert disk.due(env.cfg, env.d.log, {"code/012_red"}) == []       # running again
    rows = disk.sweep(env.cfg, env.d.log, set())
    assert rows[0]["dirs"] == {"target": 1000, "web/node_modules": 1000}
    assert not (wt / "target").exists() and not (wt / "web" / "node_modules").exists()
    assert (wt / "src" / "main.rs").exists() and (wt / "out" / "target" / "keep.txt").exists()
    ev = env.d.log.last("pruned", item="code/012_red")
    assert ev.detail["bytes"] == 2000 and ev.detail["what"] == "build output"
    assert disk.due(env.cfg, env.d.log, set()) == []                 # once
    assert disk.lines([rows[0]]) == ["code/012_red: removed target 0.0 MB, web/node_modules 0.0 MB"]


def test_a_cleared_hold_frees_it_too_and_rebuildable_is_per_repo(env):
    _kept(env)
    env.d.log.append("clear", repo="code", by="planner")
    assert [i for _, i, _ in disk.due(env.cfg, env.d.log, set())] == ["code/012_red"]
    env.configure('[repos.code]\npath = "../code"\nrebuildable = []\n')
    assert disk.due(env.d.cfg, env.d.log, set()) == []


def test_a_tracked_folder_of_that_name_stays(env):
    wt = _kept(env)
    (wt / "docs" / "node_modules").mkdir(parents=True)
    (wt / "docs" / "node_modules" / "a.md").write_text("tracked\n", encoding="utf-8")
    git(wt, "add", "-f", "docs/node_modules/a.md")
    assert "docs/node_modules" not in disk.rebuildable(wt, ["node_modules"])


# ------------------------------------------------------------------ .conductor/remote
def _remote(tmp_path: Path, **keep) -> settings.Remote:
    r = settings.Remote(control=tmp_path, native=["sim", "{out}"], fingerprint=["sim"], **keep)
    r.state.mkdir(parents=True)
    return r


def _job(r, name, age_days, mb=1, owner=None):
    p = r.state / "jobs" / name / "shards"
    p.mkdir(parents=True)
    (p / "000000000000.out").write_bytes(b"x" * int(mb * 1e6))
    if owner:
        (p.parent / "owner.json").write_text(json.dumps({"pid": owner}), encoding="utf-8")
    t = time.time() - age_days * 86400
    for q in (p / "000000000000.out", p, p.parent):
        os.utime(q, (t, t))


def test_jobs_go_past_their_age_or_the_size_cap_unless_something_uses_them(tmp_path):
    r = _remote(tmp_path, keep_jobs_days=2, keep_jobs_gb=0.0025)
    _job(r, "j100-1", 0.1)
    _job(r, "j100-2", 0.2)
    _job(r, "j100-3", 0.3)                                   # past 2.5 MB in all
    _job(r, "j100-4", 3)                                     # past its age
    _job(r, "j100-5", 3, owner=os.getpid())                  # its farm-seeds is alive
    _job(r, "j100-6", 3)                                     # a queued item names it
    (r.state / "board.json").write_text(json.dumps({"jobs": [{"id": "j100-7", "key": "k"}]}), encoding="utf-8")
    _job(r, "j100-7", 3)                                     # the broker still has it open
    p = retention.prune_jobs(r, "read the shards of j100-6", dry=False)
    assert sorted(p.names) == ["j100-3", "j100-4"] and p.bytes == 2_000_000
    assert sorted(x.name for x in (r.state / "jobs").iterdir()) == ["j100-1", "j100-2", "j100-5", "j100-6", "j100-7"]


def test_bundles_keep_the_newest_and_any_in_use(tmp_path):
    r = _remote(tmp_path, keep_bundles=2, keep_bundles_days=3)
    old = time.time() - 5 * 86400
    for i, key in enumerate(["aaaaaaaaaaa1", "aaaaaaaaaaa2", "aaaaaaaaaaa3", "aaaaaaaaaaa4", "bbbbbbbbbbb5"]):
        (r.state / "bundles" / key).mkdir(parents=True)
        (r.state / "bundles" / key / ".ok").write_text("ok\n", encoding="utf-8")
        (r.state / "bundles" / f"{key}.build.log").write_text("log\n", encoding="utf-8")
        t = time.time() - i * 60 if i < 3 else old
        for q in (r.state / "bundles" / key, r.state / "bundles" / key / ".ok", r.state / "bundles" / f"{key}.build.log"):
            os.utime(q, (t, t))
    (r.state / "bundles" / "bbbbbbbbbbb5.lock").write_text("", encoding="utf-8")   # being built
    p = retention.prune_bundles(r, "farm-seeds --at aaaaaaaaaaa4", dry=False)
    assert p.names == ["aaaaaaaaaaa2", "aaaaaaaaaaa3"]                   # the build counts among the newest 2
    assert not (r.state / "bundles" / "aaaaaaaaaaa3.build.log").exists()
    assert (r.state / "bundles" / "aaaaaaaaaaa4").is_dir() and (r.state / "bundles" / "bbbbbbbbbbb5").is_dir()


def test_free_farm_trees_past_the_bound_go_highest_first(tmp_path):
    r = _remote(tmp_path, keep_trees=2)
    trees = r.state / "trees"
    for n in range(4):
        (trees / str(n)).mkdir(parents=True)
        holds = {str(os.getpid()): None} if n == 3 else {}
        (trees / f"{n}.json").write_text(json.dumps({"key": f"k{n}", "files": {}, "holds": holds}), encoding="utf-8")
    p = retention.prune_trees(r)
    assert p.names == ["2", "1"]                               # 3 is held; 0 is kept
    assert sorted(x.name for x in trees.iterdir()) == ["0", "0.json", "3", "3.json"]


def test_a_pruned_bundle_is_built_again(tmp_path):
    b = Board(tmp_path, native_argv=["sim"])
    (tmp_path / "bundles").mkdir()
    b.add_job("k1", "fp", [], [(0, 1)], ".")
    b.set_bundle("k1", "ready")
    b.add_job("k1", "fp", [], [(1, 1)], ".")
    assert b.keys["k1"].bundle == "none"


# ------------------------------------------------------------------ the broker
class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def test_main_slots_go_to_jobs_in_the_order_they_were_made(tmp_path):
    clock = Clock()
    b = Board(tmp_path, fresh_seconds=30, native_argv=["sim"], clock=clock, main_slots=2)
    j1 = b.add_job("k1", "fp", [], [(i, 1) for i in range(4)], ".")
    clock.t += 1
    j2 = b.add_job("k1", "fp", [], [(i, 1) for i in range(4)], ".")
    assert b.claim(j2) == {"wait": True}                        # j1 still has free shards
    assert b.claim(j1)["shard"] == 0 and b.claim(j1)["shard"] == 1
    assert b.claim(j2) == {"wait": True}                        # both slots busy
    assert b.result("main", j1, 0, True, b"x", ms=10)
    assert b.claim(j2) == {"wait": True}                        # the freed slot is j1's
    assert b.claim(j1)["shard"] == 2


def test_a_native_only_job_never_goes_to_a_browser(tmp_path):
    b = Board(tmp_path, fresh_seconds=30, native_argv=["sim"])
    b.add_job("k1", "fp", [], [(0, 2)], ".", native_only=True)
    b.set_bundle("k1", "ready")
    b.hello("tab", "browser", 4, hw=8)
    assert b.poll("tab")["task"] == "idle"
    b.hello("lr", "local-remote", 2)
    assert b.poll("lr")["task"] == "gate"


def test_farm_seeds_options():
    assert client.options(["--native-only", "--shard-seeds", "1", "--at=abc", "0", "4", "o"]) == (
        {"native_only": True, "shard_seeds": 1, "at": "abc"}, ["0", "4", "o"])
    assert client.options(["0", "4", "o", "--native-only"])[0] == {}       # a project arg, not an option
    for bad in (["--shard-seeds", "0", "0", "1", "o"], ["--at"]):
        try:
            client.options(bad)
            raise AssertionError(bad)
        except ValueError:
            pass


# ------------------------------------------------------------------ hooks and preambles
def test_a_heredoc_script_into_out_goes_through():
    assert rules.shell_edit("cat > out/147_x/edit.py <<'PY'\nopen('a','w').write('x')\nPY") is None
    assert rules.shell_edit("cat <<'PY' | tee ./out/x/e.py\nx\nPY") is None
    for cmd in ("cat > src/edit.py <<'PY'\nx\nPY", "cat > out/../src/e.py <<'PY'\nx\nPY",
                "cat > out/x/e.py > src/y <<'PY'\nx\nPY", "python - <<'PY'\nopen('out/a','w').write('x')\nPY"):
        assert rules.shell_edit(cmd), cmd


def test_the_time_limit_comes_first_in_launch_stage_and_resume(env):
    full = render(env, SHORT * 3)
    assert full.index("Time limit: 1h; killed at") < full.index("Files.")
    assert full.count("Time limit:") == 1
    light = render(env, SHORT)
    assert light.index("Time limit: 1h") < light.index("Asking.")
    item = Item("code", "001_a", env.control / "queue" / "code" / "001_a.md", num=1, body=SHORT, max_hours=4)
    run = {"worktree": str(env.root / "wt"), "branch": "conductor/001_a", "base": "0123456789"}
    stage = executor.build_stage_prompt(env.cfg, item, run)
    assert "Once launched, the item has 4h" in " ".join(stage.split())
    paths = executor.RunPaths.for_item(env.cfg, item)
    resumed = executor.build_prompt(env.cfg, item, paths, 4, datetime(2026, 11, 22, 3, 24), run,
                                    resume={"old": "a" * 10, "new": "b" * 10, "landed": 0})
    assert resumed.startswith("You have the lock now. Your time limit starts now: 4h, killed at ")
