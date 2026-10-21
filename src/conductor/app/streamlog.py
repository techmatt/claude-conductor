"""An executor's stream-json log as readable text, for the trail's Log tabs. No Qt here."""

from __future__ import annotations

import json
from pathlib import Path

TEXT_LIMIT = 400_000          # characters of a log, report or diff shown at once

_RENDERED: dict[tuple, str] = {}


def render_stream_json(path: Path, limit: int = TEXT_LIMIT) -> str:
    """An executor's stream-json log as readable text: what it said, each tool call
    and a short form of each result, then the final result line. Cached per file
    version: moving through a trail never re-parses a log that has not changed."""
    try:
        st = path.stat()
    except OSError:
        return ""
    key = (str(path), st.st_mtime_ns, st.st_size, limit)
    if key in _RENDERED:
        return _RENDERED[key]
    text = _render_stream_json(path, limit)
    for k in [k for k in _RENDERED if k[0] == key[0]]:
        del _RENDERED[k]
    if len(_RENDERED) >= 32:
        _RENDERED.pop(next(iter(_RENDERED)))
    _RENDERED[key] = text
    return text


def _render_stream_json(path: Path, limit: int) -> str:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    out: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            out.append(line)
            continue
        out += _render_obj(obj)
    text = "\n".join(out) + "\n"
    return text if len(text) <= limit else text[:limit] + "\n… (log truncated)\n"


def _short(value, n: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = text.strip()
    return text if len(text) <= n else text[:n] + f" … (+{len(text) - n})"


def _tool_input(name: str, data: dict) -> str:
    for key in ("command", "file_path", "pattern", "path", "url", "query", "description"):
        if isinstance(data, dict) and data.get(key):
            return _short(data[key], 200)
    return _short(data, 200)


def _render_obj(obj: dict) -> list[str]:
    t = obj.get("type")
    if t == "system":
        if obj.get("subtype") == "init":
            bits = [f"{k} {obj[k]}" for k in ("session_id", "model", "cwd") if obj.get(k)]
            return ["── " + " · ".join(bits).replace("session_id", "session", 1)]
        return []
    if t == "assistant":
        out = []
        for c in (obj.get("message") or {}).get("content") or []:
            if c.get("type") == "text" and c.get("text", "").strip():
                out.append("")
                out += [f"  {ln}" for ln in c["text"].strip().splitlines()]
            elif c.get("type") == "tool_use":
                out.append(f"→ {c.get('name')}: {_tool_input(c.get('name', ''), c.get('input') or {})}")
        return out
    if t == "user":
        out = []
        content = (obj.get("message") or {}).get("content")
        for c in content if isinstance(content, list) else []:
            if c.get("type") == "tool_result":
                body = c.get("content")
                if isinstance(body, list):
                    body = " ".join(x.get("text", "") for x in body if isinstance(x, dict))
                mark = "✗" if c.get("is_error") else "←"
                out.append(f"    {mark} {_short(body or '', 240)}".replace("\n", " ⏎ "))
        return out
    if t == "result":
        bits = [f"status {obj.get('subtype')}", f"is_error {obj.get('is_error')}"]
        if obj.get("num_turns") is not None:
            bits.append(f"{obj['num_turns']} turns")
        if obj.get("duration_ms") is not None:
            bits.append(f"{obj['duration_ms'] / 1000:.0f}s")
        if obj.get("total_cost_usd") is not None:
            bits.append(f"${obj['total_cost_usd']:.2f}")
        out = ["", f"══ result: {', '.join(bits)}"]
        if obj.get("result"):
            out += [f"  {ln}" for ln in str(obj["result"]).strip().splitlines()]
        return out
    return []
