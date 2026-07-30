from __future__ import annotations

import stat
from pathlib import Path

import pytest

from roxabi_sense.store import Store


def test_append_and_query(tmp_path: Path) -> None:
    db = tmp_path / "sense.db"
    store = Store(db)
    store.append("idle", {"idle": False})
    store.append("media", {"player": "spotify", "title": "x"})
    assert store.count() == 2
    last = store.last_event()
    assert last is not None
    assert last.kind == "media"
    by = store.last_by_kind("idle")
    assert by is not None
    assert by.payload["idle"] is False
    start, end = store.day_bounds()
    rows = store.events_between(start, end)
    assert len(rows) >= 2
    store.close()


def test_db_permissions(tmp_path: Path) -> None:
    db = tmp_path / "data" / "sense.db"
    store = Store(db)
    store.append("idle", {"idle": False})
    store.close()
    mode = stat.S_IMODE(db.stat().st_mode)
    assert mode & 0o077 == 0  # not group/other readable
    dir_mode = stat.S_IMODE(db.parent.stat().st_mode)
    assert dir_mode & 0o077 == 0


def test_day_bounds_half_open_and_length(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    start, end = store.day_bounds("2026-07-30")
    assert start.endswith("Z") and end.endswith("Z")
    # end is next local midnight → always after start
    assert end > start
    # invalid day
    with pytest.raises(ValueError):
        store.day_bounds("not-a-date")
    store.close()


def test_events_for_day_filters_kinds(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    store.append("noise", {"x": 1})
    store.append("idle", {"idle": False})
    store.append("focus", {"app": "a", "title": "t"})
    rows = store.events_for_day(kinds=("idle", "focus"), limit=10)
    kinds = {r.kind for r in rows}
    assert "noise" not in kinds
    assert "idle" in kinds
    store.close()


def test_batch_single_commit(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    with store.batch():
        store.append("idle", {"idle": False})
        store.append("idle", {"idle": True})
        store.set_meta("k", "v")
    assert store.count() == 2
    assert store.get_meta("k") == "v"
    store.close()


def test_meta_and_empty(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    assert store.last_event() is None
    assert store.last_by_kind("idle") is None
    store.set_meta("a", "1")
    assert store.get_meta("a") == "1"
    store.close()
