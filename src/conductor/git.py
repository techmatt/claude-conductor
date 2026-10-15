"""Thin wrappers over the git CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def run(repo: Path, *args: str, check: bool = True, input: bytes | None = None) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], input=input, capture_output=True,
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout.decode("utf-8", "replace")


def is_repo(path: Path) -> bool:
    return path.is_dir() and subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--git-dir"], capture_output=True,
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


def commits_between(repo: Path, before: str | None, after: str | None) -> list[tuple[str, str]]:
    if not after or before == after:
        return []
    spec = [f"{before}..{after}"] if before else [after]
    out = run(repo, "log", "--format=%H %s", "-n", "50", *spec, check=False)
    return [tuple(line.split(" ", 1)) if " " in line else (line, "") for line in out.splitlines() if line]
