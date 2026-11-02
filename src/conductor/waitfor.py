"""What the `wait_for` tool waits on (`ask_server.py`): a path appearing, a pid exiting, or a
line turning up in a log, which a failure line also ends, and either of those with the pid
doing the work (`Worker`). Each target answers two things: `check()`, the text of what
happened or None, and `state()`, how it stands when the wait ends without that."""

from __future__ import annotations

import re
import time
from pathlib import Path

from . import procs

READ_BYTES = 1 << 20        # a log is read in steps of this, from where the last check stopped
TAIL_CHARS = 200            # the last line a timed-out wait quotes
FAIL_DEFAULT = "panicked|error"
FAIL_MARK = "has a failure line"
ADVICE = (" If that line is harmless, call again with a narrower `fail` (\"\" for none): lines already in the log "
          "count, so the same call stops on it again.")


def advice(hit: str) -> str:
    return ADVICE if FAIL_MARK in hit else ""


class PathTarget:
    def __init__(self, path: Path):
        self.path = path

    def check(self) -> str | None:
        return f"{self.path} exists" if self.path.exists() else None

    def state(self) -> str:
        return f"{self.path} does not exist yet"


class PidTarget:
    def __init__(self, pid: int):
        self.pid = pid
        self.created = procs.create_time(pid)   # so a reused pid is not mistaken for it

    def check(self) -> str | None:
        return None if procs.alive(self.pid, self.created) else f"pid {self.pid} has exited"

    def state(self) -> str:
        return f"pid {self.pid} is still running"


class LogTarget:
    def __init__(self, path: Path, pattern: re.Pattern, fail: re.Pattern | None = None):
        self.path, self.pattern, self.fail = path, pattern, fail
        self.offset = 0                 # bytes read so far; earlier lines count, so a line
        self.partial = b""              # written before the call is still found
        self.last = ""

    def check(self) -> str | None:
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self.offset)
                while chunk := fh.read(READ_BYTES):
                    self.offset += len(chunk)
                    *lines, self.partial = (self.partial + chunk).split(b"\n")
                    for raw in lines:
                        if hit := self._match(raw):
                            return hit
        except OSError:
            return None
        return self._match(self.partial)        # a last line with no newline yet

    def _match(self, raw: bytes) -> str | None:
        # NULs and a BOM's bad bytes: a UTF-16 log, as a shell redirect can write on Windows.
        line = raw.decode("utf-8", errors="ignore").replace("\x00", "").rstrip()
        self.last = line or self.last
        if self.pattern.search(line):
            return f"{self.path} has the line: {line[:TAIL_CHARS * 2]}"
        if self.fail and self.fail.search(line):
            return f"{self.path} {FAIL_MARK}: {line[:TAIL_CHARS * 2]}"
        return None

    def state(self) -> str:
        if not self.offset:
            return f"{self.path} has nothing yet, and no line matches"
        try:
            age = f", last written {round(time.time() - self.path.stat().st_mtime)}s ago"
        except OSError:
            age = ""
        return f"no line matches in {self.path} yet ({self.offset:,} bytes{age}; last line: {self.last[-TAIL_CHARS:]})"


class Worker:
    """A path or log target with the process that should produce it: its exit ends the wait."""

    def __init__(self, target, pid: PidTarget):
        self.target, self.pid = target, pid

    def check(self) -> str | None:
        if hit := self.target.check():
            return hit
        if gone := self.pid.check():
            # It may have written the line between the two looks.
            return self.target.check() or f"{gone}, and {self.target.state()}"
        return None

    def state(self) -> str:
        return f"{self.target.state()}; {self.pid.state()}"


def _pid(value) -> tuple[PidTarget | None, str | None]:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None, "`pid` must be a positive whole number"
    return PidTarget(value), None


def target(args: dict, cwd: Path):
    """The target `wait_for`'s arguments name, and None; or None and what is wrong with them."""
    named = [k for k in ("path", "log") if args.get(k) not in (None, "")]
    worker = args.get("pid") not in (None, "")
    if len(named) > 1 or not (named or worker):
        return None, "give one of `path` or `log`, or `pid` alone (a `pid` beside either is the process doing the work)"
    pid, why = _pid(args["pid"]) if worker else (None, None)
    if why or not named:
        return pid, why
    value = args[named[0]]
    if not isinstance(value, str):
        return None, f"`{named[0]}` must be a string"
    path = cwd / value                  # an absolute `value` wins
    if named == ["path"]:
        return (Worker(PathTarget(path), pid) if pid else PathTarget(path)), None
    line, fail = args.get("line"), args.get("fail")
    if not isinstance(line, str) or not line:
        return None, "`log` needs `line`, the regular expression to wait for"
    if fail is not None and not isinstance(fail, str):
        return None, "`fail` must be a string"
    try:
        found = LogTarget(path, re.compile(line), re.compile(FAIL_DEFAULT, re.IGNORECASE) if fail is None
                          else re.compile(fail) if fail else None)
    except re.error as exc:
        return None, f"`line` or `fail` is not a regular expression: {exc}"
    return (Worker(found, pid) if pid else found), None
