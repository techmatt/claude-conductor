"""Process handling. Everything specific to Windows lives here.

Executors are launched detached from the daemon's console (new process group, no
window on Windows; new session on POSIX), with stdout/stderr going straight to log
files. So Ctrl+C or a killed daemon does not take the executor down, and its output
is not lost: a restarted daemon finds the process again and adopts it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import psutil

WINDOWS = sys.platform == "win32"

# Variables that tie a process to an enclosing Claude Code session. Conductor may be
# started from inside one; an executor must not inherit them.
_PARENT_SESSION_VARS = {
    "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID",
    "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_EXECPATH", "CLAUDE_EFFORT", "AI_AGENT",
}


def child_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _PARENT_SESSION_VARS}


def spawn(cmd: list[str], cwd: Path, stdin: Path, stdout: Path, stderr: Path) -> subprocess.Popen:
    kwargs: dict = {}
    if WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    with open(stdin, "rb") as fin, open(stdout, "ab") as fout, open(stderr, "ab") as ferr:
        return subprocess.Popen(cmd, cwd=str(cwd), stdin=fin, stdout=fout, stderr=ferr,
                                env=child_env(), **kwargs)


def link_dir(target: Path, link: Path) -> None:
    """A directory link at `link` → `target`: a junction on Windows (no privilege
    needed), a symlink elsewhere."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def is_link_dir(path: Path) -> bool:
    return os.path.isjunction(path) or path.is_symlink()


def unlink_dir(link: Path) -> None:
    """Remove the link only, never what it points at."""
    if os.path.isjunction(link):
        os.rmdir(link)
    elif link.is_symlink():
        try:
            link.unlink()
        except OSError:          # a directory symlink on Windows
            os.rmdir(link)


def unlink_all_links(root: Path) -> list[str]:
    """Remove every directory link (junction or symlink) under `root`, never following
    one. `os.walk` would descend into a junction, so this walks by hand. Returns the
    links removed, relative to `root`."""
    removed = []
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            p = Path(e.path)
            if os.path.isjunction(p) or (e.is_symlink() and e.is_dir()):
                unlink_dir(p)
                removed.append(p.relative_to(root).as_posix())
            elif e.is_dir(follow_symlinks=False):
                stack.append(p)
    return removed


def remove_tree(root: Path) -> None:
    """Delete a folder that may hold links into other folders: links first, then the rest."""
    if not root.exists():
        return
    unlink_all_links(root)
    import shutil
    def onerror(func, path, exc):     # git marks object files read-only
        os.chmod(path, 0o700)
        func(path)
    shutil.rmtree(root, onexc=onerror)


def run_shell(command: str, cwd: Path, log: Path, timeout: float) -> int | None:
    """Run a setup command through the platform shell; output appended to `log`.
    Returns the exit code, or None on timeout (the process tree is killed)."""
    kwargs: dict = {"creationflags": subprocess.CREATE_NO_WINDOW} if WINDOWS else {}
    with open(log, "ab") as out:
        out.write(f"$ {command}\n".encode("utf-8"))
        out.flush()
        p = subprocess.Popen(command, shell=True, cwd=str(cwd), stdin=subprocess.DEVNULL,
                             stdout=out, stderr=subprocess.STDOUT, env=child_env(), **kwargs)
        try:
            return p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(p.pid)
            return None


def resolve(command: str) -> str:
    """`claude` → full path (Windows needs it for .cmd/.exe shims under Popen)."""
    import shutil
    return shutil.which(command) or command


def create_time(pid: int) -> float | None:
    try:
        return psutil.Process(pid).create_time()
    except psutil.Error:
        return None


def alive(pid: int | None, created: float | None) -> bool:
    """Is this exact process (pid + creation time, so a reused pid is not mistaken) alive?"""
    if not pid:
        return False
    try:
        p = psutil.Process(pid)
        if created is not None and abs(p.create_time() - created) > 1.0:
            return False
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def find_by_marker(marker: str) -> psutil.Process | None:
    """A live process whose command line contains `marker` (the executor's session id)."""
    me = os.getpid()
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            if p.pid != me and marker in " ".join(p.info["cmdline"] or []):
                # Prefer the outermost match: skip a child whose parent also matches.
                parent = p.parent()
                if parent and marker in " ".join(parent.cmdline() or []):
                    continue
                return p
        except psutil.Error:
            continue
    return None


def kill_tree(pid: int) -> None:
    """Hard-kill a process and every descendant."""
    try:
        root = psutil.Process(pid)
    except psutil.Error:
        return
    procs = root.children(recursive=True) + [root]
    for p in procs:
        try:
            p.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(procs, timeout=10)
