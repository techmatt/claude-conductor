"""Pushing main to a repo's remote after each merge (`daemon_push`), against a local bare repo."""

import pytest

from conductor import inbox, state

from conftest import git


def remote(env, path=None, name: str = "remote.git"):
    """A bare repo as `origin` of `path` (the code repo), holding its main."""
    path = path or env.repo
    bare = env.root / name
    git(env.root, "init", "-q", "--bare", "-b", "main", str(bare))
    git(path, "remote", "add", "origin", str(bare))
    git(path, "push", "-q", "-u", "origin", "main")
    return bare


def pushing(env) -> None:
    env.configure('[repos.code]\npath = "../code"\npush = true\n')


def settled(env):
    return lambda: not env.d.pushes and not env.d.push_wanted


@pytest.mark.slow
def test_a_merge_pushes_main_and_only_main(env):
    bare = remote(env)
    pushing(env)
    env.queue("001_a", "FAKE commit a.txt a")
    env.tick_until(lambda: env.events("push"))
    [push] = env.events("push")
    assert push.detail["target"] == "code" and push.detail["why"] == ["merge"]
    assert git(bare, "rev-parse", "main").strip() == git(env.repo, "rev-parse", "main").strip() == push.detail["sha"]
    assert git(bare, "branch", "--format=%(refname:short)").split() == ["main"]     # never conductor/*
    assert "pushed code main to origin" in inbox.describe(push)


def test_push_is_off_by_default(env):
    remote(env)
    env.queue("001_a", "FAKE commit a.txt a")
    env.tick_until(lambda: env.finished("code/001_a"))
    env.d.tick()
    assert not env.d.pushes and not env.events("push") and not env.events("push_failed")


@pytest.mark.slow
def test_a_failing_remote_warns_once_and_never_blocks_a_merge(env):
    remote(env)
    pushing(env)
    git(env.repo, "remote", "set-url", "origin", str(env.root / "missing.git"))
    env.queue("001_a", "FAKE commit a.txt a")
    env.tick_until(lambda: env.events("push_failed"))
    env.queue("002_b", "FAKE commit b.txt b")
    env.tick_until(lambda: env.finished("code/002_b") and settled(env)())
    assert env.finished("code/002_b").detail["status"] == "done"
    [failed] = env.events("push_failed")                    # the second merge's retry failed the same way: quiet
    assert failed.detail["why"] == ["merge"] and not failed.detail["rejected"] and failed.detail["output"]
    # A restart's retry of the same failure is quiet too.
    env.d.log.close()
    env.d = env.new_daemon()
    assert env.d.push_wanted == {"code": {"start"}}
    env.tick_until(settled(env))
    assert len(env.events("push_failed")) == 1
    # Asked for, a failure is always told; once the remote works, `push:` recovers.
    env.signal("p1.signal", "push: code\n")
    env.tick_until(lambda: len(env.events("push_failed")) == 2)
    git(env.repo, "remote", "set-url", "origin", str(env.root / "remote.git"))
    env.signal("p2.signal", "push: code\n")
    env.tick_until(lambda: env.events("push"))
    assert env.events("push")[0].detail["recovered"] is True


@pytest.mark.slow
def test_a_rejected_push_is_reported_never_forced(env):
    bare = remote(env)
    pushing(env)
    other = env.root / "elsewhere"
    git(env.root, "clone", "-q", str(bare), str(other))
    git(other, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "--allow-empty", "-m", "from elsewhere")
    git(other, "push", "-q", "origin", "main")
    theirs = git(bare, "rev-parse", "main").strip()
    env.queue("001_a", "FAKE commit a.txt a")
    env.tick_until(lambda: env.events("push_failed"))
    [failed] = env.events("push_failed")
    assert failed.detail["rejected"] and "never force-pushes" in failed.detail["error"]
    assert "push: code" in failed.detail["error"]
    assert git(bare, "rev-parse", "main").strip() == theirs           # the remote kept its commit


def test_closeout_pushes_the_control_folder_and_every_push_repo(env):
    bare = remote(env)
    pushing(env)
    control_bare = remote(env, env.control, "control.git")
    git(env.repo, "commit", "-q", "--allow-empty", "-m", "local only")
    req = env.control / "requests" / "20261021-1000"
    req.mkdir(parents=True)
    (req / "state.md").write_text("# state\n\nclosed\n", encoding="utf-8")
    (req / "request.md").write_text('---\ntargets: [state.md]\nmessage: "closeout: s1 ends"\n---\n', encoding="utf-8")
    env.tick_until(lambda: env.events("closeout_push"))
    [c] = env.events("closeout_push")
    assert sorted(c.detail["pushed"]) == [".control", "code"] and c.detail["failed"] == []
    assert git(control_bare, "rev-parse", "main").strip() == git(env.control, "rev-parse", "main").strip()
    assert git(bare, "rev-parse", "main").strip() == git(env.repo, "rev-parse", "main").strip()
    text = inbox.render(env.d.cfg, env.d.log, state.scan(env.d.cfg, env.d.log))
    assert "closeout push: " in text and "FAILED" not in text
