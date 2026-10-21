"""Process handling. Everything specific to Windows lives here.

Executors are launched detached from the daemon's console (new process group, no
window on Windows; new session on POSIX), with stdout/stderr going straight to log
files. So Ctrl+C or a killed daemon does not take the executor down, and its output
is not lost: a restarted daemon finds the process again and adopts it.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from dataclasses import dataclass
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


def allow_foreground() -> None:
    """Let another process (the running app) take the foreground. Windows only grants
    that to the process the user just started, so a second launch passes it on."""
    if WINDOWS:
        import ctypes
        ASFW_ANY = -1
        ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)


APP_ID = "conductor.app"


def set_app_id(app_id: str = APP_ID) -> None:
    """Windows: the app's own AppUserModelID, so the taskbar groups its window under its
    own icon, not Python's. Set before any window shows; a no-op elsewhere."""
    if WINDOWS:
        import ctypes
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except (AttributeError, OSError):
            pass


def free_port(avoid: set[int] = frozenset()) -> int:
    """A local TCP port nothing listens on now, from the OS's ephemeral range, never one
    in `avoid` (run targets' ports, other items')."""
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port not in avoid:
            return port
    raise OSError("no free port found")


def spawn(cmd: list[str], cwd: Path, stdin: Path, stdout: Path, stderr: Path,
          env: dict[str, str] | None = None) -> subprocess.Popen:
    """`env`: variables added to the child's environment."""
    kwargs: dict = {}
    if WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    with open(stdin, "rb") as fin, open(stdout, "ab") as fout, open(stderr, "ab") as ferr:
        return subprocess.Popen(cmd, cwd=str(cwd), stdin=fin, stdout=fout, stderr=ferr,
                                env={**child_env(), **(env or {})}, **kwargs)


def console_python() -> str:
    """The console interpreter: `python.exe` beside `pythonw.exe` when this process is
    the GUI one (the `conductor-app` gui script). A pythonw daemon has no console, so
    every console child it starts (git) would open a window of its own."""
    exe = Path(sys.executable)
    if WINDOWS and exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").is_file():
        return str(exe.parent / "python.exe")
    return sys.executable


def spawn_daemon(control: Path, log: Path, after: int | None = None) -> Detached:
    """Start `python -m conductor run <control>` detached from the caller (the app):
    no console window, its own process group, out of any job object that allows it,
    output appended to `log`, unbuffered (`-u`: a file is block-buffered, so the log
    would otherwise stay empty for hours). Quitting the app never takes the daemon down,
    and neither does killing the app's process tree (`spawn_detached`). `after`: the
    pid of a daemon restarting itself, which the new one waits for (`--after`)."""
    cmd = [console_python(), "-u", "-m", "conductor", "run", str(control)]
    return spawn_detached(cmd + (["--after", str(after)] if after else []), control, log)


@dataclass
class Detached:
    """A process `spawn_detached` started. It is not the caller's child, so its exit
    code is never known: `poll()` is None while it runs, else -1."""
    pid: int
    create_time: float | None

    def poll(self) -> int | None:
        return None if alive(self.pid, self.create_time) else -1


# Run by a short-lived launcher: it starts the real process and exits at once, so the
# real one's parent is gone and no tree kill of the caller (`taskkill /T`, a Claude Code
# session reaping its background commands) reaches it. Found live, 2026-10-20: the
# app ran as a Claude Code background command, and when that session reaped it at
# 2:16am the daemon and its executor died with it, logging nothing.
_LAUNCHER = """\
import json, subprocess, sys
a = json.loads(sys.argv[1])
with open(a["log"], "ab") as out:
    for i, flags in enumerate(a["flags"]):
        try:
            p = subprocess.Popen(a["cmd"], cwd=a["cwd"], shell=a["shell"], stdin=subprocess.DEVNULL,
                                 stdout=out, stderr=subprocess.STDOUT, creationflags=flags,
                                 start_new_session=a["posix"])
            break
        except OSError:
            if i == len(a["flags"]) - 1:
                raise
print(p.pid, flush=True)
"""


def spawn_detached(cmd: list[str] | str, cwd: Path, log: Path) -> Detached:
    """Start a long-running process detached from the caller: through a launcher that
    exits at once (so the process has no live parent), no console window, its own
    process group, out of the caller's job object when that is allowed, stdin closed,
    stdout and stderr appended to `log`. A string runs through the platform shell."""
    log.parent.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        base = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        flags = [base | subprocess.CREATE_BREAKAWAY_FROM_JOB, base]   # a job may refuse breakaway
    else:
        flags = [0]
    spec = json.dumps({"cmd": cmd, "cwd": str(cwd), "log": str(log), "shell": isinstance(cmd, str),
                       "flags": flags, "posix": not WINDOWS})
    out = subprocess.run([sys.executable, "-c", _LAUNCHER, spec], stdin=subprocess.DEVNULL,
                         capture_output=True, text=True, env=child_env(),
                         **({"creationflags": subprocess.CREATE_NO_WINDOW} if WINDOWS else {}))
    if out.returncode != 0 or not out.stdout.strip().isdigit():
        raise OSError(f"could not start {cmd!r}: {out.stderr.strip().splitlines()[-1:] or out.returncode}")
    pid = int(out.stdout.strip())
    return Detached(pid, create_time(pid))


def open_console(argv: list[str], cwd: Path) -> subprocess.Popen:
    """Run `argv` in a new, visible console window (an interactive `claude --resume`).
    Windows only: elsewhere there is no one terminal to ask for."""
    if not WINDOWS:
        raise OSError(f"opening a terminal is implemented for Windows only; run it yourself: "
                      f"cd {cwd} && {subprocess.list2cmdline(argv)}")
    return subprocess.Popen(argv, cwd=str(cwd), env=child_env(),
                            creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP)


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
    from .util import retry_io

    def onerror(func, path, exc):     # git marks object files read-only; a killed
        os.chmod(path, 0o700)         # process's files can stay locked for a moment
        retry_io(func, path)
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
        procs = root.children(recursive=True) + [root]
    except psutil.Error:            # gone already, or gone between the two calls
        return
    for p in procs:
        try:
            p.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(procs, timeout=10)


def family(pid: int | None) -> set[int]:
    """A live process and every descendant, as pids (empty when it is gone)."""
    if not pid:
        return set()
    try:
        root = psutil.Process(pid)
        return {pid} | {c.pid for c in root.children(recursive=True)}
    except psutil.Error:
        return set()


def ancestors(pid: int | None = None) -> list[psutil.Process]:
    """The live parents of `pid` (default: this process), nearest first."""
    out = []
    try:
        p = psutil.Process(pid or os.getpid()).parent()
        while p is not None and len(out) < 64:
            out.append(p)
            p = p.parent()
    except psutil.Error:
        pass
    return out


def claude_ancestor() -> psutil.Process | None:
    """A Claude Code process this one runs under, if any: when that session ends or
    reaps its background commands, it kills its whole tree, this process included."""
    for p in ancestors():
        try:
            if Path(p.name()).stem.lower() == "claude":
                return p
        except psutil.Error:
            continue
    return None


# ---------------------------------------------------------------- memory
@dataclass
class MemoryStatus:
    """The box's memory, in bytes. `commit` against `limit` is what runs out on Windows
    (RAM plus page file); `available` is free physical memory."""
    commit: int
    limit: int
    available: int
    total: int


def memory_status() -> MemoryStatus:
    if WINDOWS:
        import ctypes
        from ctypes import wintypes

        class PerformanceInformation(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("CommitTotal", ctypes.c_size_t),
                        ("CommitLimit", ctypes.c_size_t), ("CommitPeak", ctypes.c_size_t),
                        ("PhysicalTotal", ctypes.c_size_t), ("PhysicalAvailable", ctypes.c_size_t),
                        ("SystemCache", ctypes.c_size_t), ("KernelTotal", ctypes.c_size_t),
                        ("KernelPaged", ctypes.c_size_t), ("KernelNonpaged", ctypes.c_size_t),
                        ("PageSize", ctypes.c_size_t), ("HandleCount", wintypes.DWORD),
                        ("ProcessCount", wintypes.DWORD), ("ThreadCount", wintypes.DWORD)]

        info = PerformanceInformation()
        info.cb = ctypes.sizeof(info)
        if not ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(info), info.cb):
            raise OSError(ctypes.get_last_error(), "GetPerformanceInfo failed")
        page = info.PageSize
        return MemoryStatus(info.CommitTotal * page, info.CommitLimit * page,
                            info.PhysicalAvailable * page, info.PhysicalTotal * page)
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    return MemoryStatus(vm.total - vm.available + sw.used, vm.total + sw.total, vm.available, vm.total)


def kind(name: str, cmdline: list[str] | None = None) -> str:
    """What a process is, for memory accounting: claude (an executor and its in-process
    subagents), chromium, vite, playwright, node, python, shell or other."""
    stem = Path(name).stem.lower()
    if stem == "claude":
        return "claude"
    if stem in ("chrome", "chromium", "headless_shell", "msedge", "chrome-headless-shell", "firefox"):
        return "chromium"
    if stem == "node":
        line = " ".join(cmdline or []).lower()
        return "vite" if "vite" in line else "playwright" if "playwright" in line else "node"
    if stem.startswith(("python", "pythonw", "uv")):
        return "python"
    if stem in ("bash", "sh", "cmd", "powershell", "pwsh", "conhost", "tail", "grep", "sleep", "timeout", "git"):
        return "shell"
    return "other"


# Kinds an item's leftover may be when only its command line names the worktree (its
# working folder is elsewhere): never an editor or a terminal Matt opened on it.
TOOL_KINDS = {"chromium", "vite", "playwright", "node", "python", "shell"}


def usage(p: psutil.Process) -> tuple[int, int]:
    """(private bytes, working set) of one process; private is RSS off Windows."""
    mi = p.memory_info()
    return getattr(mi, "private", mi.rss), mi.rss


def _ppids() -> dict[int, int]:
    """pid → ppid for every process at once: psutil's own map (what `children()` uses,
    ~10ms). A first `ppid()` per process costs far more on Windows (3.3s for 400,
    measured), so asking each is the fallback only."""
    try:
        return dict(psutil._ppid_map())
    except Exception:           # noqa: BLE001 - a private helper; fall back to the public call
        out = {}
        for p in psutil.process_iter():
            try:
                out[p.pid] = p.ppid()
            except psutil.Error:
                continue
        return out


def _orphan(pid: int, created: float, ppids: dict[int, int], by_pid: dict[int, float | None]) -> bool:
    """Is the parent of `pid` gone? (Its ppid is free, or was reused by a later process.)"""
    ppid = ppids.get(pid)
    if not ppid or ppid not in by_pid:
        return True
    parent_created = by_pid[ppid]
    return parent_created is not None and parent_created > created


def strays(root: Path, since: float, until: float, spare: set[int]) -> list[psutil.Process]:
    """What an item left running in its worktree: live processes started between
    `since` and `until` (epoch seconds) whose parent is gone (an item's leftover always
    outlived the executor or shell that started it) and whose working folder is `root`
    or under it, or a tool (`TOOL_KINDS`) whose command line names it. Each is the root
    of what it started: kill its `family`. Never one in `spare`, this process or its
    parents. Creation time and parent are checked first: reading a working folder is
    slow (~10ms), and a terminal Matt opened on the worktree has a live parent."""
    base = os.path.normcase(str(root)).rstrip("\\/")
    spare = spare | {os.getpid()} | {p.pid for p in ancestors()}
    listed = list(psutil.process_iter(["create_time"]))
    by_pid = {p.pid: p.info.get("create_time") for p in listed}
    ppids = _ppids()
    out = []
    for p in listed:
        created = by_pid[p.pid]
        if (p.pid in spare or created is None or not (since <= created <= until)
                or not _orphan(p.pid, created, ppids, by_pid)):
            continue
        try:
            cwd = os.path.normcase(p.cwd() or "").rstrip("\\/")
            under = cwd == base or cwd.startswith(base + os.sep)
            if not under:
                cmd = p.cmdline()
                under = base in os.path.normcase(" ".join(cmd)) and kind(p.name(), cmd) in TOOL_KINDS
        except psutil.Error:
            continue
        if under:
            out.append(p)
    return out
