"""Conductor 130: a farm runs from a farm tree pinned at its start (`tree.py`): an edit to the
working tree mid-farm never reaches its shards, its build output stays out of the item's
tree, a different key gets its own tree, and a worker's *Now* line is its latest gate."""

import sys
import threading
import time

from conductor.remote import tree

from .conftest import git
from .test_remote_board import board, job

SIM = ("import sys, time, pathlib\n"
       "first, count, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]\n"
       "time.sleep(float(sys.argv[4]) if len(sys.argv) > 4 else 0)\n"
       "conf = pathlib.Path('conf.txt').read_text().strip()\n"
       "open(out, 'w', newline='\\n').write(''.join(f'{s} {conf}\\n' for s in range(first, first + count)))\n")


def pinned_project(farm, tmp_path, **over):
    repo = tmp_path / "code"
    (repo / "sim.py").write_text(SIM, encoding="utf-8")
    (repo / "conf.txt").write_text("old\n", encoding="utf-8")
    (repo / ".gitignore").write_text("built.txt\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "sim")
    py = sys.executable.replace("\\", "/")
    return farm.setup(native=f'["{py}", "sim.py", "{{first}}", "{{count}}", "{{out}}", "{{args}}"]',
                      native_build=f'["{py}", "-c", "open(\'built.txt\', \'a\').write(\'x\')"]',
                      fingerprint=f'["{py}", "-c", "print(1)"]', **over)


def test_an_edit_mid_farm_never_reaches_its_shards(farm, tmp_path, capsys):
    pinned_project(farm, tmp_path)
    got = {}
    t = threading.Thread(target=lambda: got.update(r=farm.run(0, 3, "1")))
    t.start()
    end = time.time() + 30
    while not list((farm.r.state / tree.TREES).glob("*/conf.txt")) and time.time() < end:
        time.sleep(0.02)
    (tmp_path / "code" / "conf.txt").write_text("new\n", encoding="utf-8")   # 263: a parameter added mid-farm
    t.join(60)
    code, out = got["r"]
    assert code == 0 and out == b"0 old\n1 old\n2 old\n"
    assert "runs from farm tree" in capsys.readouterr().err
    assert not (tmp_path / "code" / "built.txt").exists()                  # 167, 260: the build is the tree's
    assert (farm.r.state / tree.TREES / "0" / "built.txt").exists()
    git(tmp_path / "code", "checkout", "conf.txt")


def test_a_tree_is_shared_by_its_key_synced_when_free_and_another_key_gets_its_own(farm, tmp_path):
    pinned_project(farm, tmp_path)
    repo, state = tmp_path / "code", farm.r.state
    a, copied = tree.take(state, repo, "k1")
    assert a.name == "0" and copied == 4 and (a / "sim.py").exists()
    again, copied = tree.take(state, repo, "k1", pid=1)                  # same key: shared, nothing copied
    assert again == a and copied == 0
    (repo / "conf.txt").write_text("new\n", encoding="utf-8")
    b, copied = tree.take(state, repo, "k2")                             # k1 still held: a second tree
    assert b.name == "1" and copied == 4 and (a / "conf.txt").read_text() == "old\n"
    for t in (a, b):
        tree.release(t)
    (repo / "extra.txt").write_text("x", encoding="utf-8")
    c, copied = tree.take(state, repo, "k3")                             # free again: synced in place
    assert c.name == "0" and copied == 2 and (c / "conf.txt").read_text() == "new\n"
    (repo / "extra.txt").unlink()
    tree.release(c)
    c, copied = tree.take(state, repo, "k4")
    assert copied == 1 and not (c / "extra.txt").exists()                # a file gone from the source goes
    tree.release(c)
    git(repo, "checkout", "conf.txt")


def test_an_argument_naming_an_ignored_file_is_copied_into_the_tree(farm, tmp_path):
    pinned_project(farm, tmp_path)
    (tmp_path / "code" / "built.txt").write_text("spec", encoding="utf-8")   # ignored: not in the tree's copy
    a, _ = tree.take(farm.r.state, tmp_path / "code", "k1")
    assert not (a / "built.txt").exists()
    tree.add_args(tmp_path / "code", a, ["--spec", "built.txt", "../escape.txt"])
    assert (a / "built.txt").read_text() == "spec"
    tree.release(a)
    (tmp_path / "code" / "built.txt").unlink()


def test_pin_false_runs_in_the_working_tree(farm, tmp_path, capsys):
    pinned_project(farm, tmp_path, pin="false")
    assert farm.run(0, 2)[0] == 0
    assert (tmp_path / "code" / "built.txt").exists() and not (farm.r.state / tree.TREES).exists()
    assert "farm tree" not in capsys.readouterr().err
    (tmp_path / "code" / "built.txt").unlink()


def test_now_shows_a_workers_latest_gate_only_and_blames_a_bundle_node_failed_too(tmp_path):
    b, clock = board(tmp_path, fresh=600)
    job(b, key="k1", fp="fp1")
    b.hello("lr", "local-remote")
    assert b.poll("lr")["task"] == "gate"
    assert not b.gate("lr", "k1", "load failed: Error: configs/baseline.json: not a parameter")
    assert "1 failed the fingerprint gate (lr: load failed: Error" in b.now_line()
    b.keys["k1"].note = "browsers gated against the native reference: node exited 1"
    assert "failed in Node too: its build, not this worker" in b.now_line()
    clock.t += 5
    job(b, key="k2", fp="fp2")
    assert b.poll("lr")["task"] == "gate"
    assert b.gate("lr", "k2", "fp2")
    assert "failed the fingerprint gate" not in b.now_line()               # 6:09pm's failure, five passes on
