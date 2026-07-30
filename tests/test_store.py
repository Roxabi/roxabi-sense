from __future__ import annotations

from pathlib import Path

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
