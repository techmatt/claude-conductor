"""Finished work always lands (44): squash landing, reds of conductor's own making,
`land:` for a kept red branch, the `conductor:` report stamp and the held-queue toast."""

import pytest

from conductor import guard, state
from conductor.daemon_merge import stamp_report

from conftest import git
from test_push import remote


def main_head(env) -> str:
    return git(env.repo, "rev-parse", "main").strip()


def report(env, name: str) -> str:
    return (env.control / "reports" / f"code__{name}.md").read_text(encoding="utf-8")


def test_stamp_goes_under_the_header_and_replaces_an_earlier_one(tmp_path):
    p = tmp_path / "r.md"
    p.write_text("---\nstatus: done\nblocks: none\ncarry: none\nslow: none\n---\n\n# Title\nbody\n", encoding="utf-8")
    stamp_report(p, "red (x); NOT on main")
    stamp_report(p, "landed later")
    lines = p.read_text(encoding="utf-8").splitlines()
    assert lines[:7] == ["---", "status: done", "blocks: none", "carry: none", "slow: none",
                         "conductor: landed later", "---"]
    assert not stamp_report(tmp_path / "missing.md", "x")


@pytest.mark.slow
def test_several_commits_land_as_one_commit(env):
    before = main_head(env)
    env.queue("001_three", "# 001 — Three things\nFAKE commit a.txt a\nFAKE commit b.txt b\nFAKE append a.txt more")
    env.tick_until(lambda: env.finished("code/001_three"))
    assert env.finished("code/001_three").detail["status"] == "done"
    log = git(env.repo, "log", "--format=%s", f"{before}..main").splitlines()
    assert log == ["code/001_three: Three things"]
    body = git(env.repo, "log", "-1", "--format=%b", "main")
    assert "3 commit(s)" in body and "- fake: a.txt\n- fake: b.txt\n- fake: append a.txt" in body
    assert (env.repo / "a.txt").read_text(encoding="utf-8") == "a\nmore\n"
    [merge] = env.events("merge")
    assert merge.detail["squashed"] == 3 and merge.detail["commits"] == 1
    assert "conductor:" not in report(env, "001_three")          # outcome matches the report
    env.signal("l.signal", "land: 001_three\n")
    env.tick_until(lambda: env.events("invalid"))
    assert "already on main" in env.events("invalid")[0].detail["error"]


@pytest.mark.slow
def test_a_red_of_conductors_own_push_still_lands_and_holds_nothing(env, monkeypatch):
    # Before 43 the guard watched refs/remotes/*: conductor's own push of the control folder
    # was tamper, and Word Warriors 096/097 went red. Put that back to make the red happen.
    monkeypatch.setattr(guard, "_guarded", lambda refs: dict(refs))
    remote(env, env.control, "control.git")
    git(env.control, "commit", "-q", "--allow-empty", "-m", "unpushed")
    env.queue("001_a", f"# A\n{env.hold()}\nFAKE commit a.txt a")
    env.tick_until(lambda: env.events("start"))
    env.signal("pc.signal", "push: control\n")
    env.tick_until(lambda: env.events("push"))
    env.release()
    env.tick_until(lambda: env.finished("code/001_a"))
    fin = env.finished("code/001_a")
    assert fin.detail["status"] == "done" and fin.detail["merged"] == main_head(env), fin.detail
    assert "refs/remotes/origin/main" in fin.detail["conductor"][0]
    assert not env.events("red") and not env.events("tamper")
    assert any(e.detail.get("own") for e in env.events("drift"))
    assert (env.repo / "a.txt").is_file()
    assert "conductor: landed on main as" in report(env, "001_a")
    assert not state.scan(env.d.cfg, env.d.log).red_hold


@pytest.mark.slow
def test_a_kept_red_branch_with_merge_commits_lands_with_land(env):
    env.queue("001_g", "# Golden\nFAKE commit g.txt one\nFAKE commit b.txt nope", paths="['g.txt']")
    env.tick_until(lambda: env.finished("code/001_g"))
    fin = env.finished("code/001_g")
    assert fin.detail["status"] == "red" and fin.detail["report_status"] == "done"
    assert "NOT on main: branch `conductor/001_g` kept; `land: code/001_g` lands it" in report(env, "001_g")
    [toast] = [t for t in env.toasts if "is red" in t[0]]
    assert "queue `code` HELD" in toast[0]
    assert state.scan(env.d.cfg, env.d.log).red_hold["code"].item == "code/001_g"
    # The recovery 099 tried by hand: two branches that each regenerate g.txt, merged in.
    # Replayed commit by commit onto a main that moved, they conflict; squashed, they don't.
    wt = env.worktree("001_g")
    for side, text in (("side1", "two"), ("side2", "three")):
        git(wt, "checkout", "-q", "-b", side, "conductor/001_g")
        (wt / "g.txt").write_text(text + "\n", encoding="utf-8")
        git(wt, "commit", "-qam", f"regenerate g as {text}")
    git(wt, "checkout", "-q", "conductor/001_g")
    git(wt, "merge", "-q", "--no-ff", "side1", "-m", "merge side1")
    git(wt, "merge", "-q", "--no-ff", "side2", "-m", "merge side2", "-X", "theirs")
    (wt / "g.txt").write_text("four\n", encoding="utf-8")
    git(wt, "commit", "-qam", "regenerate g once more")
    git(wt, "branch", "-q", "-D", "side1", "side2")
    (env.repo / "other.txt").write_text("main moved\n", encoding="utf-8")
    git(env.repo, "add", "other.txt")
    git(env.repo, "commit", "-qm", "main moves on")
    before = main_head(env)
    env.signal("l.signal", "land: 001_g\n")
    env.tick_until(lambda: any(e.detail.get("state") == "landed" for e in env.events("land")))
    assert git(env.repo, "log", "--format=%s", f"{before}..main").splitlines() == ["code/001_g: Golden"]
    assert (env.repo / "g.txt").read_text(encoding="utf-8") == "four\n" and (env.repo / "b.txt").is_file()
    assert "conductor/001_g" not in git(env.repo, "branch") and not wt.exists()
    assert "landed later by `land:` on main as" in report(env, "001_g")
    assert not state.scan(env.d.cfg, env.d.log).red_hold         # its own hold released
    assert env.events("clear")[-1].detail["note"].startswith("code/001_g")
    assert not env.d.landings and not list((env.d.cfg.state_dir / "landing").glob("*.json"))
