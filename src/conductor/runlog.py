"""Run logs on disk: where a finished item's log is (plain or gzipped), its result line,
and shrinking it once the item is done (compacting, then gzip). Moved out of `executor.py`
(conductor 114, its size budget); `executor` re-exports the names its callers use."""

from __future__ import annotations

import gzip
import json
import os
import shutil
from pathlib import Path


def gz_path(log: Path) -> Path:
    return log.with_name(log.name + ".gz")


def stored_log(log: Path) -> Path | None:
    """Where a run log is now: `log` itself, or `log.gz` once its item finished
    (`gzip_log`), or None."""
    for p in (log, gz_path(log)):
        if p.is_file():
            return p
    return None


def open_log(log: Path):
    """A run log opened for reading in binary, gzipped or not; OSError when it has neither form."""
    p = stored_log(log)
    if p is None:
        raise FileNotFoundError(str(log))
    return gzip.open(p, "rb") if p.suffix == ".gz" else open(p, "rb")


def read_result(log: Path) -> dict | None:
    """The final `{"type": "result", ...}` line of a stream-json log, or None."""
    if stored_log(log) is None:
        return None
    result = None
    with open_log(log) as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw.startswith(b"{"):
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("type") == "result":
                result = obj
    return result


COMPACT_MIN_BYTES = 1 << 20     # a log smaller than this is left as it is
DROPPED = "dropped_by_conductor"


def _compact(obj, top: bool = True) -> int:
    """Strip the bulk out of one stream-json line in place: base64 payloads (an image
    the executor read, twice over: the tool result and `tool_use_result.file`) and
    `tool_use_result.originalFile` (a whole file before each edit). Returns the
    characters dropped. Both are copies of files that stay in the repo."""
    dropped = 0
    if isinstance(obj, dict):
        if top and isinstance(tur := obj.get("tool_use_result"), dict) \
                and isinstance(tur.get("originalFile"), str) and len(tur["originalFile"]) > 1024:
            dropped += len(tur["originalFile"])
            tur["originalFile"], tur[DROPPED] = "", len(tur["originalFile"])
        for key in ("data", "base64"):
            value = obj.get(key)
            if isinstance(value, str) and len(value) > 1024 and (key == "base64" or obj.get("type") == "base64"):
                dropped += len(value)
                obj[key], obj[DROPPED] = "", len(value)
        for value in obj.values():
            dropped += _compact(value, False)
    elif isinstance(obj, list):
        for value in obj:
            dropped += _compact(value, False)
    return dropped


def compact_log(log: Path) -> tuple[int, int] | None:
    """Shrink a finished item's stream-json log in place (see `_compact`); everything
    conductor and the app read from it (the result line, what was said, each tool call)
    stays. Returns (bytes before, bytes after), or None when it was left alone."""
    try:
        before = log.stat().st_size
    except OSError:
        return None
    if before < COMPACT_MIN_BYTES:
        return None
    out, dropped = [], 0
    with open(log, "rb") as fh:
        for raw in fh:
            try:
                obj = json.loads(raw)
            except ValueError:
                out.append(raw)
                continue
            n = _compact(obj)
            dropped += n
            out.append(json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n" if n else raw)
    if not dropped:
        return None
    tmp = log.with_name(log.name + ".compact")
    tmp.write_bytes(b"".join(out))
    os.replace(tmp, log)
    return before, log.stat().st_size


GZIP_MIN_BYTES = 256 << 10      # a finished log smaller than this stays plain text


def gzip_log(log: Path) -> tuple[int, int] | None:
    """Replace a finished item's run log with `log.gz` (after `compact_log`): stream-json
    shrinks ~10×, and a bug hunt's log is 8-23 MB even compacted. Every reader goes
    through `stored_log` / `open_log`. Returns (bytes before, bytes after), or None."""
    try:
        before = log.stat().st_size
    except OSError:
        return None
    if before < GZIP_MIN_BYTES:
        return None
    gz = gz_path(log)
    tmp = gz.with_name(gz.name + ".tmp")
    with open(log, "rb") as src, gzip.open(tmp, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    os.replace(tmp, gz)
    log.unlink()
    return before, gz.stat().st_size
