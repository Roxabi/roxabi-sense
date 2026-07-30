from __future__ import annotations

from pathlib import Path

from roxabi_sense.collectors.idle import IdleCollector
from roxabi_sense.store import Store


def test_idle_tick_writes_once(tmp_path: Path, monkeypatch) -> None:
    store = Store(tmp_path / "s.db")
    c = IdleCollector()
    monkeypatch.setattr(c, "_read", lambda: {"idle": False, "locked": False, "source": "test"})
    assert c.tick(store) == 1
    assert c.tick(store) == 0
    monkeypatch.setattr(c, "_read", lambda: {"idle": True, "locked": False, "source": "test"})
    assert c.tick(store) == 1
    ev = store.last_by_kind("idle")
    assert ev is not None
    assert ev.payload["idle"] is True
    store.close()
