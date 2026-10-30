"""Per-item git worktrees: create, link shared folders, run setup, merge, remove.

Each item runs on `conductor/<NNN_name>` in `<worktree_root>/<repo>/<NNN_name>`, cut
from the repo's main branch. A finished item lands as one commit: the branch is squashed
onto its fork point, that commit is rebased onto current main inside the worktree, then
the main checkout is fast-forwarded. Every step is safe to repeat, so a merge
interrupted by a daemon restart is simply run again.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import git, procs, setup_run
from .config import Config

BRANCH_PREFIX = "conductor/"
SQUASH_LISTED = 100              # branch commit subjects listed in a squash commit's body
FF_TRIES = 4                     # fast-forward attempts when git cannot write the index or a lock
FF_BACKOFF = 0.5                 # seconds before the second attempt, doubling after
# A fast-forward failure worth trying again at once: Windows holding the index or a lock.
FF_TRANSIENT = re.compile(r"index file|\.lock\b|permission denied|unable to unlink|resource busy", re.IGNORECASE)


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
    notes: list[str] = field(default_factory=list)       # for the `worktree` event, not warnings


def create(cfg: Config, repo: str, name: str, setup_log: Path, setup: bool = True) -> Created:
    """A fresh worktree for an item, with shared links and setup done (`setup=False`: the
    caller runs it, as the daemon does off its loop). Any leftover of an earlier launch
    of the same item that never ran (a crash mid-launch) is replaced."""
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
    made.linked, made.warnings, made.notes = link_shared(cfg, repo, path)
    error = run_setup(cfg, repo, path, setup_log) if setup else None
    if error:
        discard(cfg, repo, path, branch)
        raise WorktreeError(error)
    return made


def link_shared(cfg: Config, repo: str, path: Path) -> tuple[list[str], list[str], list[str]]:
    """(linked, warnings, notes)."""
    repo_path = cfg.repos[repo]
    linked, warnings, notes = [], [], []
    for rel in cfg.opts(repo).shared:
        target, link = repo_path / rel, path / rel
        if target.exists() and not target.is_dir():
            warnings.append(f"shared {rel!r}: a file, not a folder, in the main checkout; not linked")
            continue
        if not target.is_dir():
            # Not made yet (nothing has written it): make it, so every worktree shares one.
            target.mkdir(parents=True)
            notes.append(f"shared {rel!r} created in the main checkout")
        if link.exists() or procs.is_link_dir(link):
            warnings.append(f"shared {rel!r}: already present in the worktree (tracked?); not linked")
            continue
        if subprocess.run(["git", "-C", str(repo_path), "check-ignore", "-q", rel], **git.NO_WINDOW).returncode:
            warnings.append(f"shared {rel!r} is not git-ignored in {repo}; an executor could commit it")
        procs.link_dir(target, link)
        linked.append(rel)
    return linked, warnings, notes


def unlink_shared(path: Path) -> None:
    """Remove every directory link in the tree (never its target), whatever the config
    says now: `git worktree remove` follows a junction and empties its target."""
    procs.unlink_all_links(path)


def run_setup(cfg: Config, repo: str, path: Path, log: Path) -> str | None:
    """Run the repo's setup commands in the worktree, blocking; the first failure is the
    error. The daemon runs them off its loop instead (`setup_run`)."""
    return setup_run.run_commands(cfg.opts(repo).setup, path, log, cfg.setup_minutes)


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
    outcome: str                 # merged | hold | conflict | resolve (ready: `_main_ready` only)
    sha: str | None = None       # main after the merge
    reason: str = ""
    files: list[str] = field(default_factory=list)
    squashed: int = 0            # the branch's commits folded into the one that lands (0: done earlier)
    restored: list[str] = field(default_factory=list)   # main checkout paths put back to its HEAD
    dirty: list[str] = field(default_factory=list)      # left dirty by a failed fast-forward, not the branch's


@dataclass
class Restore:
    restored: list[str] = field(default_factory=list)
    foreign: list[str] = field(default_factory=list)
    error: str = ""


def _names(out: str) -> list[str]:
    return [p for p in out.split("\0") if p]


def restore_main(repo_path: Path, branch: str) -> Restore:
    """Undo a fast-forward of the main checkout that stopped part-way (git could not write
    its index, or the daemon died mid-merge). Only when every dirty path is the branch's
    own, holding exactly the branch's version (or gone where the branch has none), is the
    checkout reset to its HEAD and the branch's files left untracked removed; otherwise
    nothing is touched and `foreign` names what is not the branch's."""
    tracked = sorted(set(_names(git.run(repo_path, "diff", "HEAD", "--name-only", "--no-renames", "-z")))
                     | set(_names(git.run(repo_path, "diff", "--cached", "HEAD", "--name-only", "--no-renames", "-z"))))
    loose = _names(git.run(repo_path, "ls-files", "--others", "--exclude-standard", "-z"))
    if not tracked and not loose:
        return Restore()
    want: dict[str, str] = {}
    for entry in _names(git.run(repo_path, "ls-tree", "-z", "--full-tree", branch, "--", *tracked, *loose)):
        meta, _, path = entry.partition("\t")
        if meta.split()[1] == "blob":
            want[path] = meta.split()[2]
    present = [p for p in [*tracked, *loose] if (repo_path / p).is_file()]
    have = dict(zip(present, git.run(repo_path, "hash-object", "--", *present).split())) if present else {}
    foreign = [p for p in tracked if have.get(p) != want.get(p)]
    own_loose = [p for p in loose if p in want and have.get(p) == want[p]]
    if foreign:
        return Restore(foreign=foreign)
    try:
        if tracked:
            git.run(repo_path, "reset", "-q", "--hard", "HEAD")
        for p in own_loose:
            (repo_path / p).unlink()
    except (git.GitError, OSError) as exc:
        return Restore(foreign=tracked or own_loose, error=str(exc).strip()[:300])
    return Restore(restored=sorted(tracked + own_loose))


def _main_ready(repo_path: Path, main: str, branch: str) -> Merge:
    """`ready`, or a hold while the main checkout cannot be fast-forwarded. A checkout left
    dirty by a part-way fast-forward of `branch` is first put back (`restored`)."""
    if git.symbolic_head(repo_path) != f"refs/heads/{main}":
        return Merge("hold", reason=f"the main checkout is on {git.symbolic_head(repo_path) or 'a detached HEAD'}, "
                                    f"not {main}")
    restored = []
    if git.tracked_status(repo_path):
        restored = restore_main(repo_path, branch).restored
        if git.tracked_status(repo_path):
            return Merge("hold", reason="the main checkout has uncommitted changes")
    return Merge("ready", restored=restored)


def merge(cfg: Config, repo: str, path: Path, branch: str, subject: str) -> Merge:
    """Squash `branch` into one commit (`squash`), rebase that onto main in its worktree,
    then fast-forward the main checkout. A rebase that stops on conflicts in prose files
    only is left stopped (`resolve`). A worktree removed meanwhile is made again."""
    repo_path, main = cfg.repos[repo], cfg.opts(repo).main
    tip = git.rev(repo_path, f"refs/heads/{branch}")
    if tip is None:
        return Merge("merged", git.rev(repo_path, main), "branch already gone (merged before a restart)")
    if git.is_ancestor(repo_path, tip, main):
        return Merge("merged", git.rev(repo_path, main))
    if (ready := _main_ready(repo_path, main, branch)).outcome == "hold":
        return ready
    m = _merge_ready(cfg, repo, path, branch, subject)
    m.restored = ready.restored + m.restored
    return m


def _merge_ready(cfg: Config, repo: str, path: Path, branch: str, subject: str) -> Merge:
    repo_path, main = cfg.repos[repo], cfg.opts(repo).main
    if not path.is_dir():
        git.run(repo_path, "worktree", "prune", check=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        git.run(repo_path, "worktree", "add", "-q", str(path), branch)
    if git.rebase_in_progress(path):
        git.run(path, "rebase", "--abort", check=False)
    if git.symbolic_head(path) != f"refs/heads/{branch}":
        return Merge("conflict", reason=f"the worktree is not on {branch} (HEAD {git.symbolic_head(path) or 'detached'})")
    if changed(path):
        return Merge("conflict", reason="uncommitted changes in the worktree: commit or discard them first")
    if (n := squash(repo_path, path, branch, main, subject)) is None:
        return Merge("merged", git.rev(repo_path, main), "nothing to land: the branch's changes net to none")
    m = _rebase(cfg, repo, path, branch, ["rebase", "-q", main, branch])
    m.squashed = n
    return m


def squash(repo_path: Path, path: Path, branch: str, main: str, subject: str) -> int | None:
    """Make `branch` one commit on its fork point from main, holding its whole tree: so a
    rebase replays the item's net change once, never each commit (or a merged branch's
    commits) in turn. The message is `subject`, then the branch's commit subjects, oldest
    first. One atomic ref move (`commit-tree`, then `reset --soft` to it: same tree, so
    the worktree stays clean), and skipped when the branch already is that one commit.
    Returns how many commits it held (0: already squashed), or None when its tree is the
    fork point's (nothing to land; the branch is moved there)."""
    base = git.run(repo_path, "merge-base", main, branch).strip()
    log = git.run(repo_path, "log", "--reverse", "--format=%H %s", f"{base}..{branch}")
    commits = [ln.split(" ", 1)[1] if " " in ln else "" for ln in log.splitlines() if ln]
    if commits == [subject]:
        return 0
    trees = git.run(repo_path, "rev-parse", f"{branch}^{{tree}}", f"{base}^{{tree}}").split()
    if trees[0] == trees[1]:
        git.run(path, "reset", "-q", "--soft", base)
        return None
    listed = [f"- {c}" for c in commits[:SQUASH_LISTED]]
    if len(commits) > SQUASH_LISTED:
        listed.append(f"- … and {len(commits) - SQUASH_LISTED} more")
    message = f"{subject}\n\nSquashed by conductor from {branch} ({len(commits)} commit(s)):\n" + "\n".join(listed)
    sha = git.run(repo_path, "commit-tree", f"{branch}^{{tree}}", "-p", base, "-F", "-",
                  input=message.encode("utf-8")).strip()
    git.run(path, "reset", "-q", "--soft", sha)
    return len(commits)


def continue_rebase(cfg: Config, repo: str, path: Path, branch: str, files: list[str]) -> Merge:
    """Stage the resolved prose `files` and carry the stopped rebase on (it may stop
    again: `resolve` once more, or `conflict`)."""
    repo_path, main = cfg.repos[repo], cfg.opts(repo).main
    if (ready := _main_ready(repo_path, main, branch)).outcome == "hold":
        return ready
    if not git.rebase_in_progress(path):
        # Conductor's machinery, not the item: the landing starts over next tick.
        return Merge("hold", reason="the stopped rebase is gone; landing again from the branch",
                     restored=ready.restored)
    git.run(path, "add", "--", *files)
    staged = git.run(path, "diff", "--cached", "--name-only", check=False).strip()
    # A resolution identical to main leaves the replayed commit empty: skip it.
    m = _rebase(cfg, repo, path, branch, ["rebase", "--continue"] if staged else ["rebase", "--skip"])
    m.restored = ready.restored + m.restored
    return m


def abort_rebase(path: Path) -> None:
    """Back to the item's own branch tip: both versions kept, main untouched."""
    if path.is_dir() and git.rebase_in_progress(path):
        git.run(path, "rebase", "--abort", check=False)


def conflicted(path: Path) -> list[str]:
    return [f for f in git.run(path, "diff", "--name-only", "--diff-filter=U", check=False).splitlines() if f]


def _rebase(cfg: Config, repo: str, path: Path, branch: str, args: list[str]) -> Merge:
    repo_path, opts = cfg.repos[repo], cfg.opts(repo)
    try:
        git.run(path, *args)
    except git.GitError as exc:
        files = conflicted(path)
        reason = str(exc).strip()[:300]
        if files and all(opts.is_prose(f) for f in files):
            return Merge("resolve", reason=reason.splitlines()[0], files=files)
        git.run(path, "rebase", "--abort", check=False)
        if not files:
            # Not a content conflict (a stale lock, an untracked file in the way, a file
            # held open): try again next tick.
            return Merge("hold", reason=f"rebase failed: {reason}")
        return Merge("conflict", reason=reason.splitlines()[0], files=files)
    return _fast_forward(repo_path, opts.main, branch)


def _fast_forward(repo_path: Path, main: str, branch: str) -> Merge:
    """Fast-forward the main checkout to `branch`. A failure may leave it part-updated, so
    each is followed by `restore_main`; one on the index or a lock (Windows holding a
    file) is tried again at once, with a backoff. Anything else (main moved since the
    rebase, an untracked file in the way) holds for the next tick; so does a checkout
    still dirty after the restore, with `dirty` naming it."""
    restored: list[str] = []
    for attempt in range(FF_TRIES):
        if attempt:
            time.sleep(FF_BACKOFF * 2 ** (attempt - 1))
        try:
            git.run(repo_path, "merge", "-q", "--ff-only", branch)
            return Merge("merged", git.rev(repo_path, main), restored=restored)
        except git.GitError as exc:
            error = str(exc).strip()[:300]
        back = restore_main(repo_path, branch)
        restored = sorted(set(restored) | set(back.restored))
        if back.foreign:
            why = f"; restoring it failed: {back.error}" if back.error else ""
            return Merge("hold", reason=f"fast-forward failed: {error}{why}", restored=restored, dirty=back.foreign)
        if not FF_TRANSIENT.search(error):
            break
    tries = f" ({attempt + 1} tries)" if attempt else ""
    return Merge("hold", reason=f"fast-forward failed{tries}: {error}", restored=restored)
