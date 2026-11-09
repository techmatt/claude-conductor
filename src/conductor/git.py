"""Thin wrappers over the git CLI."""

from __future__ import annotations

import os
import subprocess
import sys
import time
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


def toplevel(path: Path) -> Path | None:
    """The work tree `path` belongs to, or None outside one."""
    out = run(path, "rev-parse", "--show-toplevel", check=False).strip() if path.is_dir() else ""
    return Path(out).resolve() if out else None


def has_remote(repo: Path) -> bool | None:
    """Whether the repo has any git remote; None when it can't be asked (not a repo)."""
    proc = subprocess.run(["git", "-C", str(repo), "remote"], capture_output=True, **NO_WINDOW) \
        if repo.is_dir() else None
    return None if proc is None or proc.returncode != 0 else bool(proc.stdout.strip())


NO_REMOTE = "{repo} has no remote: create its GitHub repo"
_REMOTE_TTL = 60.0
_remote_seen: dict[Path, tuple[float, bool | None]] = {}


def remote_missing(repos: dict[str, Path], ttl: float = _REMOTE_TTL) -> list[str]:
    """Names of repos with no git remote (a repo git can't read is left out). Each answer is
    kept `ttl` seconds: the app asks on every refresh."""
    at, out = time.monotonic(), []
    for name, path in repos.items():
        seen = _remote_seen.get(path)
        if seen is None or at - seen[0] >= ttl:
            seen = _remote_seen[path] = (at, has_remote(path))
        if seen[1] is False:
            out.append(name)
    return out


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


def ignore_locally(repo: Path, folder: str) -> bool:
    """Make sure git ignores `folder/` in `repo`: unless a rule already does, add it to the
    clone's `info/exclude` (never committed, shared by every worktree). True when added."""
    probe = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", f"{folder}/x"],
                           capture_output=True, **NO_WINDOW)
    if probe.returncode == 0:
        return False
    common = repo / run(repo, "rev-parse", "--git-common-dir").strip()   # relative in a main checkout
    exclude = common / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    with exclude.open("a", encoding="utf-8", newline="\n") as f:
        f.write(("" if not text or text.endswith("\n") else "\n") + f"/{folder}/\n")
    return True


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
