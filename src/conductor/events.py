"""`events.db`: the append-only SQLite event log. Every other view derives from it."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from .util import stamp

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT NOT NULL,
    kind   TEXT NOT NULL,
    item   TEXT,
    repo   TEXT,
    paths  TEXT NOT NULL DEFAULT '[]',
    detail TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_item ON events(item);
CREATE INDEX IF NOT EXISTS events_kind ON events(kind);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
    BEGIN SELECT RAISE(ABORT, 'events.db is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
    BEGIN SELECT RAISE(ABORT, 'events.db is append-only'); END;
"""

# Kinds that put an event in the inbox's red section.
RED_KINDS = {"red", "tamper", "kill", "request_failed", "invalid"}
# One question's trail; each is logged at most once per question (detail `question`).
QUESTION_KINDS = ("ask", "answer", "default", "late", "question_closed")


def item_matches(item_id: str, ref: str) -> bool:
    """`sandbox/001_x` matches `sandbox/001_x`, `001_x`, `001`, `sandbox/001`."""
    repo, _, name = item_id.partition("/")
    if "/" in ref:
        ref_repo, _, ref = ref.partition("/")
        if ref_repo != repo:
            return False
    return name == ref or name.split("_", 1)[0] == ref


@dataclass
class Event:
    id: int
    ts: str
    kind: str
    item: str | None
    repo: str | None
    paths: list[str]
    detail: dict

    @classmethod
    def from_row(cls, row: tuple) -> "Event":
        return cls(row[0], row[1], row[2], row[3], row[4], json.loads(row[5]), json.loads(row[6]))


class EventLog:
    def __init__(self, path: Path, on_append: Callable[[Event], None] | None = None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), isolation_level=None, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.on_append = on_append

    def close(self) -> None:
        self.conn.close()

    def append(self, kind: str, item: str | None = None, repo: str | None = None,
               paths: Iterable[str] = (), **detail) -> Event:
        if repo is None and item and "/" in item:
            repo = item.split("/", 1)[0]
        ts = stamp()
        paths = list(paths)
        cur = self.conn.execute(
            "INSERT INTO events (ts, kind, item, repo, paths, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (ts, kind, item, repo, json.dumps(paths), json.dumps(detail, default=str)),
        )
        event = Event(cur.lastrowid, ts, kind, item, repo, paths, detail)
        if self.on_append:
            self.on_append(event)
        return event

    def query(self, since_id: int | None = None, since_ts: datetime | None = None,
              item: str | None = None, kinds: Iterable[str] | None = None,
              repo: str | None = None) -> list[Event]:
        sql, args = "SELECT * FROM events WHERE 1=1", []
        if since_id is not None:
            sql += " AND id > ?"
            args.append(since_id)
        if repo:
            sql += " AND repo = ?"
            args.append(repo)
        if kinds:
            kinds = list(kinds)
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += kinds
        rows = [Event.from_row(r) for r in self.conn.execute(sql + " ORDER BY id", args)]
        if since_ts is not None:
            rows = [e for e in rows if datetime.fromisoformat(e.ts) >= since_ts]
        if item:
            rows = [e for e in rows if e.item and item_matches(e.item, item)]
        return rows

    def last(self, kind: str, item: str | None = None, repo: str | None = None) -> Event | None:
        sql, args = "SELECT * FROM events WHERE kind = ?", [kind]
        if item is not None:
            sql += " AND item = ?"
            args.append(item)
        if repo is not None:
            sql += " AND repo = ?"
            args.append(repo)
        row = self.conn.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()
        return Event.from_row(row) if row else None

    def max_id(self) -> int:
        return self.conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]

    def read_mark(self) -> int:
        ev = self.last("read")
        return int(ev.detail.get("through", 0)) if ev else 0
