"""Prompt 72's rules: `out/` git-ignored at launch, the snapshot's renames retried, and the
preamble's new lines (scripts in files, `nice`, design reversals, an outline row per done-when item)."""

import os

from conductor import executor, git, guard
from conftest import git as run_git
from test_preamble import SHORT, render


def ignored(repo, path: str) -> bool:
    return git.run(repo, "check-ignore", path, check=False).strip() == path


def test_ignore_locally_adds_once_and_reaches_worktrees(env):
    assert git.ignore_locally(env.repo, "out") is True
    assert git.ignore_locally(env.repo, "out") is False
    assert (env.repo / ".git" / "info" / "exclude").read_text(encoding="utf-8").count("/out/") == 1
    run_git(env.repo, "worktree", "add", "-q", "-b", "wt", str(env.root / "wt"))
    assert ignored(env.root / "wt", "out/x.json")
    assert git.ignore_locally(env.root / "wt", "out") is False


def test_a_repo_that_already_ignores_it_is_left_alone(env):
    (env.repo / ".gitignore").write_text("out\n", encoding="utf-8")
    assert git.ignore_locally(env.repo, "out") is False
    assert not (env.repo / ".git" / "info" / "exclude").exists()


def test_a_launch_ignores_scratch(env):
    env.queue("001_a", "hello")
    env.tick_until(lambda: env.finished("code/001_a"))
    assert ignored(env.repo, "out/x.json")
    assert not env.events("warning")


def test_the_snapshot_rename_waits_out_a_held_handle(env, monkeypatch):
    real, fails = os.replace, [3]

    def flaky(src, dst):
        if fails[0]:
            fails[0] -= 1
            raise PermissionError(5, "Access is denied")
        return real(src, dst)

    monkeypatch.setattr(guard.os, "replace", flaky)
    snap = guard.take(env.control, env.d.cfg.state_dir, "now")
    assert fails[0] == 0 and guard.load(env.d.cfg.state_dir).head == snap.head


def test_a_parked_write_gets_a_receipt(env):
    receipt = env.control / "receipts" / "parked" / "code.md"
    (env.control / "parked" / "code.md").write_text("# parked\n- rename x\n- [rules] cut y\n", encoding="utf-8")
    env.tick_until(receipt.is_file)
    text = receipt.read_text(encoding="utf-8")
    assert 'outcome: "accepted"' in text and "2 parked edit(s) waiting for code (1 `[rules]`)" in text
    (env.control / "parked" / "nope.md").write_text("- x\n", encoding="utf-8")
    env.tick_until((env.control / "receipts" / "parked" / "nope.md").is_file)
    assert "is not a configured repo" in (env.control / "receipts" / "parked" / "nope.md").read_text(encoding="utf-8")


def test_the_preambles_carry_the_new_lines(env):
    full = " ".join(render(env, SHORT * 3).split())
    assert "never a heredoc or `-c` string" in full and "`nice -n 10 npm test`" in full
    assert "conductor git-ignores it" in full
    assert "reverses a direction the brief or an amendment states, however clear the data" in full
    assert "one row per done-when item" in full
    light = " ".join(render(env, SHORT).split())
    assert "never a heredoc" in light and "`nice -n 10 <cmd>`" in light
    assert executor.SCRATCH == "out"
