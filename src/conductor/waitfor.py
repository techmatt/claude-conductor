"""What the `wait_for` tool waits on (`ask_server.py`): a path appearing, a pid exiting, or a
line turning up in a log. Each target answers two things: `check()`, the text of what
happened or None, and `state()`, how it stands when the wait ends without that."""

from __future__ import annotations

import re
from pathlib import Path

from . import procs

READ_BYTES = 1 << 20        # a log is read in steps of this, from where the last check stopped
TAIL_CHARS = 200            # the last line a timed-out wait quotes


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
    def __init__(self, path: Path, pattern: re.Pattern):
        self.path, self.pattern = path, pattern
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
        return f"{self.path} has the line: {line[:TAIL_CHARS * 2]}" if self.pattern.search(line) else None

    def state(self) -> str:
        if not self.offset:
            return f"{self.path} has nothing yet, and no line matches"
        return f"no line matches in {self.path} yet ({self.offset:,} bytes; last line: {self.last[-TAIL_CHARS:]})"


def target(args: dict, cwd: Path):
    """The target `wait_for`'s arguments name, and None; or None and what is wrong with them."""
    named = [k for k in ("path", "pid", "log") if args.get(k) not in (None, "")]
    if len(named) != 1:
        return None, "give exactly one of `path`, `pid` or `log`"
    if named == ["pid"]:
        pid = args["pid"]
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            return None, "`pid` must be a positive whole number"
        return PidTarget(pid), None
    value = args[named[0]]
    if not isinstance(value, str):
        return None, f"`{named[0]}` must be a string"
    path = cwd / value                  # an absolute `value` wins
    if named == ["path"]:
        return PathTarget(path), None
    line = args.get("line")
    if not isinstance(line, str) or not line:
        return None, "`log` needs `line`, the regular expression to wait for"
    try:
        return LogTarget(path, re.compile(line)), None
    except re.error as exc:
        return None, f"`line` is not a regular expression: {exc}"
