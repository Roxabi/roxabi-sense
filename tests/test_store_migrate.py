"""Schema version + migrations (ADR-003)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from roxabi_sense.store import SCHEMA_VERSION, SchemaVersionError, Store
from roxabi_sense.store.migrate import META_KEY, migrate, read_schema_version


def test_new_store_writes_schema_version(tmp_path: Path) -> None:
    db = tmp_path / "sense.db"
    with Store(db) as store:
        assert store.get_meta(META_KEY) == str(SCHEMA_VERSION)
        assert store.count() == 0
        store.append("idle", {"idle": False})
    with Store(db) as store2:
        assert store2.get_meta(META_KEY) == str(SCHEMA_VERSION)
        assert store2.count() == 1


def test_pre_versioned_db_migrates_without_data_loss(tmp_path: Path) -> None:
    """Simulate a DB created before schema_version existed."""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            kind TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)",
        ("2026-08-01T00:00:00Z", "idle", '{"idle":true}'),
    )
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)",
        ("last_tick", "2026-08-01T00:00:00Z"),
    )
    conn.commit()
    conn.close()

    with Store(db) as store:
        assert store.get_meta(META_KEY) == str(SCHEMA_VERSION)
        assert store.count() == 1
        assert store.get_meta("last_tick") == "2026-08-01T00:00:00Z"
        last = store.last_event()
        assert last is not None
        assert last.kind == "idle"
        assert last.payload.get("idle") is True


def test_refuse_newer_schema_version(tmp_path: Path) -> None:
    db = tmp_path / "future.db"
    with Store(db) as store:
        store.set_meta(META_KEY, str(SCHEMA_VERSION + 99))
    with pytest.raises(SchemaVersionError, match="newer than this package"):
        Store(db)


def test_corrupt_schema_version_fails_closed(tmp_path: Path) -> None:
    db = tmp_path / "bad.db"
    with Store(db) as store:
        store.set_meta(META_KEY, "not-an-int")
    with pytest.raises(SchemaVersionError, match="unreadable"):
        Store(db)


def test_migrate_idempotent_at_target(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    with Store(db) as store:
        conn = store._conn  # noqa: SLF001 — intentional unit test of migrate
        assert migrate(conn) == SCHEMA_VERSION
        assert read_schema_version(conn) == SCHEMA_VERSION
