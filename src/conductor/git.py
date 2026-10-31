"""Thin wrappers over the git CLI."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# git is a console program: started from a process with no console (the app, or a
# daemon the app started under pythonw), each call would open a console window.
NO_WINDOW: dict = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


class GitError(RuntimeError):
    pass


def run(repo: Path, *args: str, check: bool = True, input: bytes | None = None) -> str:
    # GIT_EDITOR: a rebase or merge must never wait on an editor nobody sees.
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], input=input, capture_output=True,
        env={**os.environ, "GIT_EDITOR": "true", "GIT_TERMINAL_PROMPT": "0"}, **NO_WINDOW,
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout.decode("utf-8", "replace")


def is_repo(path: Path) -> bool:
    return path.is_dir() and subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--git-dir"], capture_output=True, **NO_WINDOW,
    ).returncode == 0


def head(repo: Path) -> str | None:
    out = run(repo, "rev-parse", "--verify", "-q", "HEAD", check=False).strip()
    return out or None


def symbolic_head(repo: Path) -> str | None:
    out = run(repo, "symbolic-ref", "-q", "HEAD", check=False).strip()
    return out or None


def refs(repo: Path) -> dict[str, str]:
    out = run(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    return dict(line.split(" ", 1) for line in out.splitlines() if line)


def ls_files(repo: Path) -> list[str]:
    return [p for p in run(repo, "ls-files", "-z").split("\0") if p]


def write_tree(repo: Path) -> str | None:
    out = run(repo, "write-tree", check=False).strip()
    return out or None


def tracked_status(repo: Path) -> str:
    """Porcelain status of tracked files only (untracked files are not a change)."""
    return run(repo, "status", "--porcelain=v1", "--untracked-files=no")


def rev(repo: Path, ref: str) -> str | None:
    out = run(repo, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}", check=False).strip()
    return out or None


def is_ancestor(repo: Path, a: str, b: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", a, b],
                          capture_output=True, **NO_WINDOW).returncode == 0


def changed_files(repo: Path, before: str, after: str) -> list[str]:
    """Files that differ between two commits (renames as delete + add)."""
    out = run(repo, "diff", "--name-only", "--no-renames", "-z", before, after)
    return [p for p in out.split("\0") if p]


def rebase_in_progress(worktree: Path) -> bool:
    gitdir = Path(run(worktree, "rev-parse", "--absolute-git-dir").strip())
    return (gitdir / "rebase-merge").exists() or (gitdir / "rebase-apply").exists()


def commits_between(repo: Path, before: str | None, after: str | None) -> list[tuple[str, str]]:
    return [(sha, subject) for sha, _, subject in commits_dated(repo, before, after)]


def commits_dated(repo: Path, before: str | None, after: str | None) -> list[tuple[str, str, str]]:
    """(sha, committer time in ISO form, subject), newest first, at most 50."""
    if not after or before == after:
        return []
    spec = [f"{before}..{after}"] if before else [after]
    out = run(repo, "log", "--format=%H %cI %s", "-n", "50", *spec, check=False)
    return [(*line.split(" ", 2), "")[:3] for line in out.splitlines() if line]
