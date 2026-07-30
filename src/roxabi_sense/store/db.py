"""SQLite event store (WAL)."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
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

# Kinds surfaces care about for day/status (owned by store, not CLI).
STATUS_KINDS: tuple[str, ...] = (
    "agent_sessions_snapshot",
    "process_snapshot",
    "idle",
    "media_snapshot",
    "tmux_snapshot",
    "desktop_snapshot",
    "focus",
)

TIMELINE_KINDS: tuple[str, ...] = (
    "agent_sessions_snapshot",
    "agent_session",
    "process_snapshot",
    "idle",
    "media",
    "media_snapshot",
    "tmux_snapshot",
    "focus",
    "desktop_snapshot",
)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _to_z(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Event:
    id: int
    ts: str
    kind: str
    payload: dict[str, Any]


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        self._conn = sqlite3.connect(self.path, check_same_thread=True)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._harden_files()
        self._batch_depth = 0

    def _harden_files(self) -> None:
        for p in (
            self.path,
            Path(str(self.path) + "-wal"),
            Path(str(self.path) + "-shm"),
        ):
            if p.is_file():
                try:
                    os.chmod(p, 0o600)
                except OSError:
                    pass

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Group multiple append/set_meta into one commit."""
        self._batch_depth += 1
        if self._batch_depth == 1:
            self._conn.execute("BEGIN")
        try:
            yield
            if self._batch_depth == 1:
                self._conn.commit()
                self._harden_files()
        except Exception:
            if self._batch_depth == 1:
                self._conn.rollback()
            raise
        finally:
            self._batch_depth -= 1

    def _maybe_commit(self) -> None:
        if self._batch_depth == 0:
            self._conn.commit()
            self._harden_files()

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
        self._maybe_commit()
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
        self._maybe_commit()

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

    def latest_by_kinds(self, kinds: tuple[str, ...] = STATUS_KINDS) -> dict[str, Event]:
        out: dict[str, Event] = {}
        for kind in kinds:
            ev = self.last_by_kind(kind)
            if ev is not None:
                out[kind] = ev
        return out

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

    def events_for_day(
        self,
        day: str | None = None,
        *,
        kinds: tuple[str, ...] = TIMELINE_KINDS,
        limit: int = 200,
    ) -> list[Event]:
        start, end = self.day_bounds(day)
        if not kinds:
            return self.events_between(start, end, limit=limit)
        placeholders = ",".join("?" * len(kinds))
        rows = self._conn.execute(
            f"""
            SELECT id, ts, kind, payload FROM events
            WHERE ts >= ? AND ts < ? AND kind IN ({placeholders})
            ORDER BY ts ASC, id ASC
            LIMIT ?
            """,
            (start, end, *kinds, limit),
        ).fetchall()
        return [self._row(r) for r in rows]

    def day_bounds(self, day: str | None = None) -> tuple[str, str]:
        """Return [start, end) ISO-Z bounds for a local calendar day (handles DST)."""
        local_tz = datetime.now().astimezone().tzinfo
        if day:
            try:
                d = date.fromisoformat(day)
            except ValueError as exc:
                raise ValueError(f"invalid day {day!r}; expected YYYY-MM-DD") from exc
        else:
            d = datetime.now().astimezone().date()
        start_local = datetime.combine(d, time.min, tzinfo=local_tz)
        end_local = datetime.combine(d + timedelta(days=1), time.min, tzinfo=local_tz)
        return _to_z(start_local), _to_z(end_local)

    @staticmethod
    def _row(row: sqlite3.Row) -> Event:
        payload = json.loads(row["payload"] or "{}")
        if not isinstance(payload, dict):
            payload = {"value": payload}
        return Event(id=int(row["id"]), ts=str(row["ts"]), kind=str(row["kind"]), payload=payload)
