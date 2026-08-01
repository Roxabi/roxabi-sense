"""Shared status_snapshot for all surfaces."""

from __future__ import annotations

from pathlib import Path

from roxabi_sense.report import (
    load_status_snapshot,
    status_snapshot,
    status_snapshot_missing,
)
from roxabi_sense.store import Store


def test_status_snapshot_missing_db(tmp_path: Path) -> None:
    db = tmp_path / "missing.db"
    snap = status_snapshot_missing(db)
    assert snap.db_exists is False
    assert snap.events == 0
    assert snap.presence.state == "offline"
    body = snap.to_dict()
    assert body["db_exists"] is False
    assert body["presence"]["state"] == "offline"


def test_status_snapshot_from_store(tmp_path: Path) -> None:
    db = tmp_path / "sense.db"
    with Store(db) as store:
        store.append("idle", {"idle": False, "source": "test"})
        store.set_meta("last_tick", "2026-08-01T12:00:00Z")
        store.set_meta("machine", "laptop")
        snap = status_snapshot(store)
    assert snap.db_exists is True
    assert snap.events == 1
    assert snap.last_tick == "2026-08-01T12:00:00Z"
    assert snap.machine == "laptop"
    assert snap.last_event is not None
    assert snap.last_event.kind == "idle"
    assert "idle" in snap.latest_by_kind


def test_load_status_snapshot_opens_db(tmp_path: Path) -> None:
    db = tmp_path / "sense.db"
    with Store(db) as store:
        store.append("focus", {"app": "x", "title": "t"})
        store.set_meta("last_tick", "2026-08-01T12:00:00Z")
    snap = load_status_snapshot(db)
    assert snap.db_exists is True
    assert snap.events == 1
