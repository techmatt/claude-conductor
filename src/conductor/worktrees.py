"""Per-item git worktrees: create, link shared folders, run setup, merge, remove.

Each item runs on `conductor/<NNN_name>` in `<worktree_root>/<repo>/<NNN_name>`, cut
from the repo's main branch. A finished item is merged by rebasing that branch onto
current main inside its worktree, then fast-forwarding the main checkout. Every step is
safe to repeat, so a merge interrupted by a daemon restart is simply run again.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import git, procs
from .config import Config

BRANCH_PREFIX = "conductor/"


class WorktreeError(RuntimeError):
    pass


def branch_name(item_name: str) -> str:
    return BRANCH_PREFIX + item_name


@dataclass
class Created:
    path: Path
    branch: str
    base: str
    warnings: list[str] = field(default_factory=list)
    linked: list[str] = field(default_factory=list)


def create(cfg: Config, repo: str, name: str, setup_log: Path) -> Created:
    """A fresh worktree for an item, with shared links and setup done. Any leftover of
    an earlier launch of the same item that never ran (a crash mid-launch) is replaced."""
    repo_path, opts = cfg.repos[repo], cfg.opts(repo)
    path, branch = cfg.worktree(repo, name), branch_name(name)
    base = git.rev(repo_path, opts.main)
    if base is None:
        raise WorktreeError(f"branch {opts.main!r} does not exist in {repo_path}")
    discard(cfg, repo, path, branch)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        git.run(repo_path, "worktree", "add", "-q", "-b", branch, str(path), base)
    except git.GitError as exc:
        raise WorktreeError(str(exc)) from exc
    made = Created(path, branch, base)
    made.linked, made.warnings = link_shared(cfg, repo, path)
    error = run_setup(cfg, repo, path, setup_log)
    if error:
        discard(cfg, repo, path, branch)
        raise WorktreeError(error)
    return made


def link_shared(cfg: Config, repo: str, path: Path) -> tuple[list[str], list[str]]:
    repo_path = cfg.repos[repo]
    linked, warnings = [], []
    for rel in cfg.opts(repo).shared:
        target, link = repo_path / rel, path / rel
        if not target.is_dir():
            warnings.append(f"shared {rel!r}: no such folder in the main checkout; not linked")
            continue
        if link.exists() or procs.is_link_dir(link):
            warnings.append(f"shared {rel!r}: already present in the worktree (tracked?); not linked")
            continue
        if subprocess.run(["git", "-C", str(repo_path), "check-ignore", "-q", rel], **git.NO_WINDOW).returncode:
            warnings.append(f"shared {rel!r} is not git-ignored in {repo}; an executor could commit it")
        procs.link_dir(target, link)
        linked.append(rel)
    return linked, warnings


def unlink_shared(path: Path) -> None:
    """Remove every directory link in the tree (never its target), whatever the config
    says now: `git worktree remove` follows a junction and empties its target."""
    procs.unlink_all_links(path)


def run_setup(cfg: Config, repo: str, path: Path, log: Path) -> str | None:
    """Run the repo's setup commands in the worktree; the first failure is the error."""
    for command in cfg.opts(repo).setup:
        code = procs.run_shell(command, path, log, cfg.setup_minutes * 60)
        if code is None:
            return f"setup {command!r} took longer than {cfg.setup_minutes:g} minutes"
        if code:
            return f"setup {command!r} exited {code} (see {log.name})"
    return None


def discard(cfg: Config, repo: str, path: Path, branch: str | None) -> None:
    """Remove the worktree and its branch, whatever state they are in. Never through
    `git worktree remove`: links go first, then the files, then git forgets the tree."""
    repo_path = cfg.repos[repo]
    procs.remove_tree(path)
    git.run(repo_path, "worktree", "prune", check=False)
    if branch and git.rev(repo_path, f"refs/heads/{branch}"):
        git.run(repo_path, "branch", "-D", branch, check=False)


def keep(cfg: Config, repo: str, path: Path) -> None:
    """A red item's worktree stays for inspection, without its shared links."""
    if path.exists():
        unlink_shared(path)


def changed(path: Path) -> str:
    """Tracked changes in a worktree (porcelain), ignoring untracked files."""
    return git.tracked_status(path)


def untracked(path: Path) -> list[str]:
    """Untracked, unignored files: what a read-only stage must not add to."""
    out = git.run(path, "ls-files", "--others", "--exclude-standard", "-z")
    return sorted(p for p in out.split("\0") if p)


def move_to(cfg: Config, repo: str, path: Path) -> str:
    """Move a clean, commit-free worktree to current main; returns the new base."""
    sha = git.rev(cfg.repos[repo], cfg.opts(repo).main)
    git.run(path, "reset", "-q", "--hard", sha)
    return sha


@dataclass
class Merge:
    outcome: str                 # merged | hold | conflict
    sha: str | None = None       # main after the merge
    reason: str = ""
    files: list[str] = field(default_factory=list)


def merge(cfg: Config, repo: str, path: Path, branch: str) -> Merge:
    """Rebase `branch` onto main in its worktree, then fast-forward the main checkout."""
    repo_path, main = cfg.repos[repo], cfg.opts(repo).main
    tip = git.rev(repo_path, f"refs/heads/{branch}")
    if tip is None:
        return Merge("merged", git.rev(repo_path, main), "branch already gone (merged before a restart)")
    if git.is_ancestor(repo_path, tip, main):
        return Merge("merged", git.rev(repo_path, main))
    if git.symbolic_head(repo_path) != f"refs/heads/{main}":
        return Merge("hold", reason=f"the main checkout is on {git.symbolic_head(repo_path) or 'a detached HEAD'}, "
                                    f"not {main}")
    if git.tracked_status(repo_path):
        return Merge("hold", reason="the main checkout has uncommitted changes")
    if not path.is_dir():
        return Merge("conflict", reason=f"worktree {path} is gone; cannot rebase")
    if git.rebase_in_progress(path):
        git.run(path, "rebase", "--abort", check=False)
    if git.symbolic_head(path) != f"refs/heads/{branch}":
        return Merge("conflict", reason=f"the worktree is not on {branch} (HEAD {git.symbolic_head(path) or 'detached'})")
    try:
        git.run(path, "rebase", "-q", main, branch)
    except git.GitError as exc:
        files = [f for f in git.run(path, "diff", "--name-only", "--diff-filter=U", check=False).splitlines() if f]
        git.run(path, "rebase", "--abort", check=False)
        reason = str(exc).strip()[:300]
        if not files:
            # Not a content conflict (a stale lock, an untracked file in the way, a file
            # held open): try again next tick.
            return Merge("hold", reason=f"rebase failed: {reason}")
        return Merge("conflict", reason=reason.splitlines()[0], files=files)
    try:
        git.run(repo_path, "merge", "-q", "--ff-only", branch)
    except git.GitError as exc:
        # main moved between the rebase and the fast-forward, or an untracked file is in
        # the way: try again next tick.
        return Merge("hold", reason=f"fast-forward failed: {str(exc).strip()[:300]}")
    return Merge("merged", git.rev(repo_path, main))
