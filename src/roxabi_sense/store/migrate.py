"""Ordered local store migrations + schema_version gate.

See docs/architecture/adr/003-schema-version-and-sync.md.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

# Package capability — bump when adding a migration step below.
SCHEMA_VERSION = 1

META_KEY = "schema_version"

MigrationFn = Callable[[sqlite3.Connection], None]


class SchemaVersionError(RuntimeError):
    """DB schema is newer than this package, or meta is unreadable."""


def read_schema_version(conn: sqlite3.Connection) -> int:
    """Return stored schema version; missing key ⇒ 0 (pre-versioned DBs)."""
    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?",
        (META_KEY,),
    ).fetchone()
    if row is None:
        return 0
    raw = row[0] if not isinstance(row, sqlite3.Row) else row["value"]
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise SchemaVersionError(
            f"unreadable meta.{META_KEY}={raw!r}; fix or recreate the store"
        ) from exc


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (META_KEY, str(version)),
    )


def _migrate_to_1(_conn: sqlite3.Connection) -> None:
    """Baseline: events + meta tables (created by Store SCHEMA script)."""
    return


# version number → step that brings DB *to* that version from version-1
MIGRATIONS: dict[int, MigrationFn] = {
    1: _migrate_to_1,
}


def migrate(
    conn: sqlite3.Connection,
    *,
    target: int = SCHEMA_VERSION,
) -> int:
    """Apply migrations until ``target``. Refuse DB newer than package.

    Returns the version written (or already present).
    """
    current = read_schema_version(conn)
    if current > target:
        raise SchemaVersionError(
            f"database schema_version={current} is newer than this package "
            f"(supports ≤{target}). Upgrade roxabi-sense."
        )
    if current == target:
        return current

    for version in range(current + 1, target + 1):
        step = MIGRATIONS.get(version)
        if step is None:
            raise SchemaVersionError(
                f"missing migration step for schema_version={version}"
            )
        step(conn)
        _set_version(conn, version)
    conn.commit()
    return target


def schema_info() -> dict[str, Any]:
    """Static package schema capability (docs / doctor)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "meta_key": META_KEY,
        "sync_protocol_version": 1,  # envelope v1 frozen in ADR-003
    }
