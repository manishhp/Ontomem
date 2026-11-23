"""Append-only event log (SQLite). The memory graph is a projection of this log.

Why event sourcing:
* time travel - "what did the memory believe on 1 June?" is a replay up to that date;
* reproducibility - the same log always projects to the same graph (invariant I3);
* auditability - nothing is overwritten; corrections are new events;
* the right to be forgotten is one well-defined operation (``erase_user``) that leaves
  a content-free tombstone.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Event:
    seq: int
    user_id: str
    ts: datetime
    type: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "user_id": self.user_id, "ts": self.ts.isoformat(), "type": self.type, "payload": self.payload}


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    ts          TEXT NOT NULL,
    type        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_user_ts ON events(user_id, ts, seq);
"""


class EventStore:
    """Thread-safe SQLite event log. Use ``":memory:"`` for tests and benchmarks."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()

    def append(self, user_id: str, ts: datetime, type_: str, payload: dict[str, Any]) -> Event:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events (user_id, ts, type, payload, recorded_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, ts.isoformat(), type_, json.dumps(payload, default=str), datetime.now(UTC).isoformat()),
            )
            self._conn.commit()
            return Event(int(cur.lastrowid or 0), user_id, ts, type_, payload)

    def events(self, user_id: str, until: datetime | None = None) -> Iterator[Event]:
        """Events in replay order: by timestamp, ties broken by sequence number (total order)."""
        q = "SELECT seq, user_id, ts, type, payload FROM events WHERE user_id = ?"
        args: list[Any] = [user_id]
        if until is not None:
            q += " AND ts <= ?"
            args.append(until.isoformat())
        q += " ORDER BY ts, seq"
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        for seq, uid, ts, typ, payload in rows:
            yield Event(seq, uid, datetime.fromisoformat(ts), typ, json.loads(payload))

    def count(self, user_id: str | None = None) -> int:
        with self._lock:
            if user_id is None:
                return int(self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            return int(self._conn.execute("SELECT COUNT(*) FROM events WHERE user_id = ?", (user_id,)).fetchone()[0])

    def users(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute("SELECT DISTINCT user_id FROM events ORDER BY user_id").fetchall()
        return [r[0] for r in rows]

    def erase_user(self, user_id: str) -> int:
        """Right to be forgotten: delete every event, keep one content-free tombstone."""
        with self._lock:
            n = self._conn.execute("DELETE FROM events WHERE user_id = ?", (user_id,)).rowcount
            self._conn.execute(
                "INSERT INTO events (user_id, ts, type, payload, recorded_at) VALUES (?, ?, 'erased', ?, ?)",
                (user_id, datetime.now(UTC).replace(tzinfo=None).isoformat(), json.dumps({"events_deleted": n}),
                 datetime.now(UTC).isoformat()),
            )
            self._conn.commit()
        return n

    def close(self) -> None:
        self._conn.close()
