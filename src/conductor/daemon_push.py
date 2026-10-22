"""Pushing `main` to a repo's remote after each merge, off the loop (`docs/push.md`).

A repo with `push = true` is pushed after every merge into its main branch, at daemon
start when its main is ahead of the remote's, on a `push: <repo>` signal, and (with the
control folder, when that has a remote) on a planner's `closeout:` request. The control
folder, when it has a remote, is pushed after its own commits, batched (`CONTROL_QUIET_SECONDS`
after the last, or `CONTROL_MAX_WAIT_SECONDS` after the first unpushed), at daemon start when
ahead, and on `push: control`. One `git
push` per target at a time, a plain fast-forward push of the main branch only: never a
`conductor/*` branch, never `--force`. A push is a process the tick polls, so a slow
network never holds a tick or a merge. A failure is one `push_failed` event; the same
failure again from an automatic retry (the next merge, a restart) logs nothing more.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import git, procs
from .util import read_text

PUSH_SECONDS = 300          # a push still running after this is killed and reported
CONTROL = ".control"        # the control folder's target name (`push: control` asks for it)
CONTROL_QUIET_SECONDS = 30.0      # a burst of control commits is one push, this long after the last
CONTROL_MAX_WAIT_SECONDS = 300.0  # ... or this long after the first, however busy the burst
TAIL_LINES = 6


@dataclass
class Push:
    target: str
    path: Path
    remote: str
    branch: str
    whys: set[str]
    popen: subprocess.Popen
    log: Path
    started: float = field(default_factory=time.monotonic)


def remote_for(path: Path, branch: str) -> str | None:
    """The branch's upstream remote, else `origin`, else the only remote; None without one."""
    if r := git.run(path, "config", f"branch.{branch}.remote", check=False).strip():
        return r
    remotes = git.run(path, "remote", check=False).split()
    return "origin" if "origin" in remotes else (remotes[0] if len(remotes) == 1 else None)


def rejected(output: str) -> bool:
    """The remote moved on (someone pushed from elsewhere): not a network failure."""
    return any(s in output for s in ("[rejected]", "non-fast-forward", "fetch first"))


class PushMixin:
    def _init_push(self) -> None:
        self.pushes: dict[str, Push] = {}               # target → its running push
        self.push_wanted: dict[str, set[str]] = {}       # target → why it is wanted next
        self.closeout_push: dict | None = None           # a closeout's pushes still out, and their results
        self.control_dirty: tuple[float, float] | None = None   # (first, last) unpushed control commit, monotonic
        # target → the failure it is in (its signature), from the log: a restart repeats no warning.
        self.push_failing: dict[str, str] = {}
        for e in self.log.query(kinds=["push", "push_failed"]):
            t = e.detail.get("target")
            if e.kind == "push":
                self.push_failing.pop(t, None)
            else:
                self.push_failing[t] = e.detail.get("signature", "")

    def _push_where(self, target: str) -> tuple[Path, str]:
        if target == CONTROL:
            return self.cfg.control, (git.symbolic_head(self.cfg.control) or "refs/heads/main").removeprefix("refs/heads/")
        return self.cfg.repos[target], self.cfg.opts(target).main

    def control_committed(self) -> None:
        """Conductor committed to the control folder: pushed once the burst goes quiet."""
        now = time.monotonic()
        self.control_dirty = (self.control_dirty[0] if self.control_dirty else now, now)

    def _control_remote(self) -> str | None:
        return remote_for(self.cfg.control, self._push_where(CONTROL)[1])

    def want_push(self, target: str, why: str) -> None:
        """Push `target` when its current push (if any) is done."""
        if target == CONTROL or self.cfg.opts(target).push or why in ("signal", "closeout"):
            self.push_wanted.setdefault(target, set()).add(why)

    def push_ahead(self) -> None:
        """Daemon start: each `push = true` repo whose main is ahead of its remote-tracking
        ref (a merge's push lost to a restart, or never made) is pushed."""
        for repo, path in self.cfg.repos.items():
            if not self.cfg.opts(repo).push:
                continue
            branch = self.cfg.opts(repo).main
            remote = remote_for(path, branch)
            local, tracked = git.rev(path, branch), remote and git.rev(path, f"refs/remotes/{remote}/{branch}")
            if local and local != tracked:
                self.want_push(repo, "start")
        # The control folder likewise, when it has a remote; without one, quietly nothing.
        branch = self._push_where(CONTROL)[1]
        if remote := self._control_remote():
            local, tracked = git.rev(self.cfg.control, branch), git.rev(self.cfg.control, f"refs/remotes/{remote}/{branch}")
            if local and local != tracked:
                self.want_push(CONTROL, "start")

    def closeout_pushes(self, request: str) -> None:
        """A `closeout:` request: the control folder (when it has a remote) and every
        `push = true` repo; `closeout_push` reports them once all are done."""
        targets = [r for r in self.cfg.repos if self.cfg.opts(r).push]
        branch = self._push_where(CONTROL)[1]
        if remote_for(self.cfg.control, branch):
            targets.insert(0, CONTROL)
            self.control_dirty = None           # this push carries the batch
        if not targets:
            return
        self.closeout_push = {"request": request, "left": set(targets), "pushed": [], "failed": []}
        for t in targets:
            self.want_push(t, "closeout")

    def process_pushes(self) -> None:
        if (c := self.control_dirty) and (time.monotonic() - c[1] >= CONTROL_QUIET_SECONDS
                                          or time.monotonic() - c[0] >= CONTROL_MAX_WAIT_SECONDS):
            self.control_dirty = None
            if self._control_remote():           # a control folder with no remote: skipped quietly
                self.want_push(CONTROL, "commit")
        for target, p in list(self.pushes.items()):
            code = p.popen.poll()
            if code is None and time.monotonic() - p.started > PUSH_SECONDS:
                procs.kill_tree(p.popen.pid)
                code = p.popen.wait()
                self._push_done(p, code, f"timed out after {PUSH_SECONDS}s")
            elif code is not None:
                self._push_done(p, code)
        for target in [t for t in self.push_wanted if t not in self.pushes]:
            self._start_push(target, self.push_wanted.pop(target))

    def _start_push(self, target: str, whys: set[str]) -> None:
        path, branch = self._push_where(target)
        remote = remote_for(path, branch)
        log = self.cfg.state_dir / "push" / f"{target}.log"
        if remote is None:
            self._push_failed(target, branch, None, whys, "no remote: add one (`git remote add origin <url>`) "
                              f"in {path}", "no remote")
            return
        log.parent.mkdir(parents=True, exist_ok=True)
        ref = f"refs/heads/{branch}"
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never", "GIT_EDITOR": "true"}
        with open(log, "wb") as out:
            try:
                popen = subprocess.Popen(["git", "-C", str(path), "push", remote, f"{ref}:{ref}"],
                                         stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, env=env,
                                         **git.NO_WINDOW)
            except OSError as exc:
                self._push_failed(target, branch, remote, whys, f"git did not start: {exc}", "no git")
                return
        self.pushes[target] = Push(target, path, remote, branch, whys, popen, log)

    def _push_done(self, p: Push, code: int, error: str | None = None) -> None:
        del self.pushes[p.target]
        try:
            lines = [ln for ln in read_text(p.log).splitlines() if ln.strip() and not ln.startswith("hint:")]
        except OSError:
            lines = []
        tail = "\n".join(lines[-TAIL_LINES:])
        if code == 0 and error is None:
            was = self.push_failing.pop(p.target, None)
            self.log.append("push", repo=None if p.target == CONTROL else p.target, target=p.target,
                            remote=p.remote, branch=p.branch, sha=git.rev(p.path, p.branch), why=sorted(p.whys),
                            up_to_date="Everything up-to-date" in tail, **({"recovered": True} if was else {}))
            self._closeout_result(p.target, p.whys, None)
            return
        if rejected("\n".join(lines)):
            name = "control" if p.target == CONTROL else p.target
            error = (f"rejected: {p.remote}/{p.branch} has commits {p.branch} lacks (pushed from elsewhere). "
                     f"Conductor never force-pushes: pull them into {p.path} ({p.branch}) while nothing merges "
                     f"there, then signal `push: {name}`")
            signature = "rejected"
        else:
            error = error or f"git push exited {code}"
            signature = error if "timed out" in error else (lines[-1] if lines else error)
        self._push_failed(p.target, p.branch, p.remote, p.whys, error, signature, tail)

    def _push_failed(self, target: str, branch: str, remote: str | None, whys: set[str], error: str,
                     signature: str, tail: str = "") -> None:
        # The same failure again from an automatic retry stays quiet; asked for, it is told.
        quiet = self.push_failing.get(target) == signature and not whys & {"signal", "closeout"}
        self.push_failing[target] = signature
        if not quiet:
            self.log.append("push_failed", repo=None if target == CONTROL else target, target=target, remote=remote,
                            branch=branch, why=sorted(whys), error=error, signature=signature, output=tail,
                            rejected=signature == "rejected")
        self._closeout_result(target, whys, error)

    def _closeout_result(self, target: str, whys: set[str], error: str | None) -> None:
        c = self.closeout_push
        if c is None or "closeout" not in whys or target not in c["left"]:
            return
        c["left"].discard(target)
        (c["failed"] if error else c["pushed"]).append(target)
        if not c["left"]:
            self.closeout_push = None
            self.log.append("closeout_push", request=c["request"], pushed=c["pushed"], failed=c["failed"])
