"""Small helpers shared by every module: clock, atomic writes, front-matter."""

from __future__ import annotations

import hashlib
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import yaml


def now() -> datetime:
    return datetime.now().astimezone()


def stamp(dt: datetime | None = None) -> str:
    """Local ISO timestamp with offset, second precision (what events.db stores)."""
    return (dt or now()).isoformat(timespec="seconds")


def parse_stamp(text: str) -> datetime:
    return datetime.fromisoformat(text)


def clock(value: str | datetime, ref: datetime | None = None) -> str:
    """Local 12-hour clock time, `5:25pm`; prefixed with the date when not today."""
    dt = parse_stamp(value) if isinstance(value, str) else value
    dt = dt.astimezone()
    ref = ref or now()
    hour = dt.hour % 12 or 12
    text = f"{hour}:{dt.minute:02d}{'am' if dt.hour < 12 else 'pm'}"
    if dt.date() != ref.date():
        text = f"{dt:%b} {dt.day} {text}"
    return text


def duration(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def parse_since(text: str) -> tuple[int | None, datetime | None]:
    """`#12`/`12` → event id; `30m`/`2h`/`1d` → relative; `5:25pm` → today; else ISO."""
    text = text.strip()
    if m := re.fullmatch(r"#?(\d+)", text):
        return int(m.group(1)), None
    if m := re.fullmatch(r"(\d+(?:\.\d+)?)\s*([mhd])", text):
        unit = {"m": "minutes", "h": "hours", "d": "days"}[m.group(2)]
        return None, now() - timedelta(**{unit: float(m.group(1))})
    if m := re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", text, re.IGNORECASE):
        hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
        return None, now().replace(hour=hour, minute=int(m.group(2) or 0), second=0, microsecond=0)
    dt = datetime.fromisoformat(text)
    return None, dt if dt.tzinfo else dt.astimezone()


RETRY_SECONDS = 3.0


def retry_io(fn, *args, seconds: float = RETRY_SECONDS):
    """`fn(*args)`, retried while it raises PermissionError, for up to `seconds`.

    On Windows a rename, replace or unlink fails while another process has the file
    open without FILE_SHARE_DELETE, which is how Python (and so the app, the CLI, an
    editor or a virus scanner) opens files. Those reads are short; waiting them out
    beats failing the operation.
    """
    end = time.monotonic() + seconds
    delay = 0.02
    while True:
        try:
            return fn(*args)
        except PermissionError:
            if time.monotonic() >= end:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.25)


def file_hash(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def write_text(path: Path, text: str) -> None:
    """Atomic UTF-8 / LF write: temp file in the same folder, then replace. The temp name
    carries the pid, so two processes writing one file (an old daemon's last inbox and a
    new one's first) never share, and splice, one temp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    retry_io(os.replace, tmp, path)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")


class FrontMatterError(ValueError):
    pass


def split_front_matter(text: str) -> tuple[dict, str]:
    """`---\\n<yaml>\\n---\\n<body>` → (mapping, body). No front-matter → ({}, text)."""
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        raise FrontMatterError("front-matter opened with --- but never closed")
    raw = text[4:end]
    rest = text[end + 4:]
    body = rest[1:] if rest.startswith("\n") else rest
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise FrontMatterError(f"front-matter is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise FrontMatterError("front-matter must be a mapping")
    return data, body
