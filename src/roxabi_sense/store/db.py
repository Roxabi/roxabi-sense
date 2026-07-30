"""SQLite event store (WAL)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_kind_ts ON events(kind, ts);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Event:
    id: int
    ts: str
    kind: str
    payload: dict[str, Any]


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def append(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        ts: str | None = None,
    ) -> int:
        row_ts = ts or _utc_now()
        body = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
        cur = self._conn.execute(
            "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)",
            (row_ts, kind, body),
        )
        self._conn.commit()
        row_id = cur.lastrowid
        if row_id is None:
            raise RuntimeError("insert returned no row id")
        return int(row_id)

    def set_meta(self, key: str, value: str) -> None:
        sql = (
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )
        self._conn.execute(sql, (key, value))
        self._conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()
        return int(row["n"])

    def last_event(self) -> Event | None:
        row = self._conn.execute(
            "SELECT id, ts, kind, payload FROM events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return None if row is None else self._row(row)

    def last_by_kind(self, kind: str) -> Event | None:
        row = self._conn.execute(
            "SELECT id, ts, kind, payload FROM events WHERE kind = ? ORDER BY id DESC LIMIT 1",
            (kind,),
        ).fetchone()
        return None if row is None else self._row(row)

    def events_between(self, start: str, end: str, *, limit: int = 5000) -> list[Event]:
        rows = self._conn.execute(
            """
            SELECT id, ts, kind, payload FROM events
            WHERE ts >= ? AND ts < ?
            ORDER BY ts ASC, id ASC
            LIMIT ?
            """,
            (start, end, limit),
        ).fetchall()
        return [self._row(r) for r in rows]

    def day_bounds(self, day: str | None = None) -> tuple[str, str]:
        """Return [start, end) ISO bounds for a local calendar day (YYYY-MM-DD)."""
        if day:
            d = datetime.strptime(day, "%Y-%m-%d").date()
        else:
            d = datetime.now().astimezone().date()
        start_local = datetime(d.year, d.month, d.day).astimezone().replace(microsecond=0)
        end_local = start_local + timedelta(days=1)
        return (
            start_local.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            end_local.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        )

    @staticmethod
    def _row(row: sqlite3.Row) -> Event:
        payload = json.loads(row["payload"] or "{}")
        if not isinstance(payload, dict):
            payload = {"value": payload}
        return Event(id=int(row["id"]), ts=str(row["ts"]), kind=str(row["kind"]), payload=payload)
