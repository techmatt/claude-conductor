"""The daemon restarts itself when its own code changes.

The code version is git's id of the committed package and its deps (`HEAD:./src`,
`HEAD:./pyproject.toml` in conductor's checkout), so only a commit that changes either
counts: uncommitted, mid-edit work never does. Every `CODE_CHECK_SECONDS`, between
ticks, a changed version that imports is a restart wanted; it is taken at a safe point,
which includes a checkout whose src/ and pyproject match HEAD with no rebase, merge or
cherry-pick under way (the new daemon runs the working tree). Then pending signals are
applied (a `stop` written meanwhile is this daemon's), the import is checked again, and
the new daemon is spawned detached with `--after <this pid>`: once its config loads and
its code imports it writes `.conductor/restart.ready`, then waits for this one to exit
before taking the lock. Only then is `daemon_restart` logged and this one leaves
`run_forever`. A version that fails to import, to spawn or to answer is retried after
`RESTART_RETRY_SECONDS`, with one warning. Running executors and staged items survive,
as for any restart: the new daemon adopts them (`recover`). Not a git checkout: off.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import timedelta
from pathlib import Path

from . import git, procs
from .session import CONDUCTOR_ROOT
from .util import duration, now

CODE_CHECK_SECONDS = 10
RESTART_PATIENCE_SECONDS = 300      # unsafe this long: one warning naming what blocks it
PICKER_WAIT_HOURS = 1               # a picker opened this recently still waits for its save
RESTART_READY_SECONDS = 20          # the new daemon has this long to write READY_FILE
RESTART_RETRY_SECONDS = 300         # a refused version (import, spawn, no handshake) is tried again after this
IMPORT_CHECK = "import conductor.cli, conductor.daemon"
READY_FILE = "restart.ready"        # in .conductor/: `run --after` is up (its pid), removed once it holds the lock
IN_PROGRESS = {"rebase-merge": "a rebase", "rebase-apply": "a rebase", "MERGE_HEAD": "a merge",
               "CHERRY_PICK_HEAD": "a cherry-pick"}


def _git(root: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30,
                             **git.NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def code_version(root: Path = CONDUCTOR_ROOT) -> str | None:
    """The committed package and deps as `<src tree> <pyproject blob>`; None when `root`
    is not a git checkout holding them (an installed wheel, no git)."""
    out = _git(root, "rev-parse", "HEAD:./src", "HEAD:./pyproject.toml")
    return " ".join(out.split()) if out and len(out.split()) == 2 else None


def code_head(root: Path = CONDUCTOR_ROOT) -> str:
    """`<short sha> <subject>` of the checkout's HEAD, for the reason a restart gives."""
    return _git(root, "log", "-1", "--format=%h %s") or "?"


def checkout_blockers(root: Path = CONDUCTOR_ROOT) -> list[str]:
    """Why the checkout's working tree is not the committed code a restart would run:
    uncommitted changes under src/ or in pyproject.toml, or a rebase, merge or
    cherry-pick in progress (`--git-path`, so a worktree checkout is read right)."""
    status = _git(root, "status", "--porcelain", "--", "src", "pyproject.toml")
    if status is None:
        return ["conductor's checkout: git status failed"]
    out = [f"conductor's checkout has uncommitted changes in src/ or pyproject.toml "
           f"({len(status.splitlines())} paths)"] if status else []
    names = list(IN_PROGRESS)
    paths = (_git(root, "rev-parse", *(a for n in names for a in ("--git-path", n))) or "").splitlines()
    out += sorted({f"conductor's checkout: {IN_PROGRESS[n]} is in progress"
                   for n, p in zip(names, paths) if (root / p).exists()})
    return out


def imports_ok(cwd: Path) -> str | None:
    """Does the code on disk import in a fresh interpreter? The error, if not."""
    try:
        out = subprocess.run([procs.console_python(), "-c", IMPORT_CHECK], cwd=cwd, capture_output=True, text=True,
                             timeout=120, env=procs.child_env(), **git.NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return repr(exc)
    if out.returncode == 0:
        return None
    lines = out.stderr.strip().splitlines()
    return lines[-1] if lines else f"exit code {out.returncode}"


class RestartMixin:
    def _init_restart(self) -> None:
        self.code_probe = code_version          # tests stand in a version
        self.head_probe = code_head
        self.import_check = imports_ok
        self.checkout_probe = checkout_blockers
        self.restart_spawner = procs.spawn_daemon
        self.code_version: str | None = None    # what this process runs: read at startup
        self.code_head = ""
        self._code_at: float | None = None      # monotonic time of the last check
        self._code_refused: dict[str, float] = {}   # version -> monotonic time it failed (import, spawn, handshake)
        self._refusal_warned: set[str] = set()  # one warning per refused version, however often it is retried
        self._restart_to: str | None = None     # a restart wanted, to this version
        self._restart_since: float | None = None
        self._restart_warned = False

    @property
    def _ready_file(self) -> Path:
        return self.cfg.state_dir / READY_FILE

    def _read_code(self) -> None:
        # At startup, under the lock: a restart that handed over to this process is done.
        self._ready_file.unlink(missing_ok=True)
        self.code_version = self.code_probe()
        self.code_head = self.head_probe() if self.code_version else ""

    def _refuse(self, version: str, why: str) -> None:
        self._code_refused[version] = time.monotonic()
        if version not in self._refusal_warned:
            self._refusal_warned.add(version)
            self.log.append("warning", note=f"conductor's code changed ({self.head_probe()}) but {why}; this daemon "
                                            f"keeps running the code it started with and tries again every "
                                            f"{duration(RESTART_RETRY_SECONDS)}")

    def restart_due(self) -> bool:
        """Between ticks: True once a new daemon is started on changed code and this one
        should leave `run_forever` (without `daemon_stop`)."""
        if self.code_version is None:
            return False
        checked = False                 # imports checked in this call: not again before the spawn
        if self._restart_to is None:
            if self._code_at is not None and time.monotonic() - self._code_at < CODE_CHECK_SECONDS:
                return False
            self._code_at = time.monotonic()
            version = self.code_probe()
            if version is None or version == self.code_version:
                return False
            refused = self._code_refused.get(version)
            if refused is not None and time.monotonic() - refused < RESTART_RETRY_SECONDS:
                return False
            if error := self.import_check(self.cfg.control):
                self._refuse(version, f"does not import: {error}")
                return False
            self._restart_to, self._restart_since, self._restart_warned = version, time.monotonic(), False
            checked = True
        if blockers := self._restart_blockers():
            waited = time.monotonic() - self._restart_since
            if waited >= RESTART_PATIENCE_SECONDS and not self._restart_warned:
                self._restart_warned = True
                self.log.append("warning", note=f"conductor's code changed; the restart has waited {duration(waited)} "
                                                f"for a safe point: {'; '.join(blockers)}. It keeps waiting, never forced")
            return False
        return self._restart(imports_checked=checked)

    def _restart(self, imports_checked: bool = False) -> bool:
        # A `stop: daemon` written since the tick is this daemon's: applied here, never by the new one.
        self.process_signals()
        if self.stop_requested is not None:
            self._restart_to = None
            return False                # run_forever stops (`daemon_stop`) at its next check
        if self._restart_blockers():
            return False                # a signal just applied made it unsafe: still wanted, retried
        to, self._restart_to = self._restart_to, None
        # The working tree may have changed since the check at detection (a blocked wait).
        if not imports_checked and (error := self.import_check(self.cfg.control)):
            self._refuse(to, f"does not import: {error}")
            return False
        head = self.head_probe()
        log = self.cfg.state_dir / "daemon.out.log"
        self._ready_file.unlink(missing_ok=True)
        try:
            new = self.restart_spawner(self.cfg.control, log, after=os.getpid())
        except OSError as exc:
            self._refuse(to, f"the new daemon could not start: {exc}")
            return False
        if not self._handshake(new):
            if new.poll() is None:
                procs.kill_tree(new.pid)        # never left to take the lock later
            self._refuse(to, f"the new daemon (pid {new.pid}) did not report ready within "
                             f"{duration(RESTART_READY_SECONDS)}; see {self.cfg.rel(log)}")
            return False
        self.log.append("daemon_restart", reason=f"code changed: {self.code_head.split(' ', 1)[0]} → {head}",
                        **{"from": self.code_version, "to": to}, pid=os.getpid(), new_pid=new.pid,
                        running=sorted(self.runs), staging=sorted(self.stages),
                        note="executors keep running; the new daemon adopts them")
        return True

    def _handshake(self, new) -> bool:
        """Wait for the new daemon's READY_FILE: its config loaded and its code imported.
        False if it exits first or the wait runs out."""
        end = time.monotonic() + RESTART_READY_SECONDS
        while not self._ready_file.is_file():
            if new.poll() is not None or time.monotonic() >= end:
                return False
            time.sleep(0.2)
        return True

    def _restart_blockers(self) -> list[str]:
        """What a restart now would break: a merge executor resolving a prose conflict, a
        setup running, a reorder not finished, a picker page still waiting for its save
        (its port and token die with this process), a run target still starting (its wait
        lives in memory); or what it would run that is not committed (`checkout_blockers`)."""
        out = list(self.checkout_probe())
        for path in sorted(self.cfg.path("running").glob("*/*.json")):
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(rec, dict):
                continue
            item = rec.get("item") or path.name
            if (rec.get("resolve") or {}).get("state") == "running":
                out.append(f"{item}: a merge executor is resolving a prose conflict")
            if rec.get("phase") == "setup" or (rec.get("setup") or {}).get("state") == "running":
                out.append(f"{item}: its setup is running")
        if where := self._reorder_pending():
            out.append(f"a reorder of {where} is not finished")
        out += [f"picker {name}: open, not saved yet" for name in self._pickers_waiting()]
        out += [f"run target {name}: starting, not ready yet" for name in sorted(self.runner.pending)]
        return out

    def _pickers_waiting(self) -> list[str]:
        if self.picker.httpd is None:
            return []
        self.picker.drain()             # a save that arrived since the tick
        opened: dict[str, bool] = {}
        for ev in self.log.query(since_ts=now() - timedelta(hours=PICKER_WAIT_HOURS), kinds=["pick_open", "pick_saved"]):
            opened[ev.detail.get("name")] = ev.kind == "pick_open"
        return sorted(n for n, waiting in opened.items() if waiting and n in self.picker.entries)
