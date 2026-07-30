from __future__ import annotations

from pathlib import Path

from roxabi_sense.collectors.process_presence import ProcessPresenceCollector
from roxabi_sense.store import Store


def test_snapshot_only_and_fingerprint(tmp_path: Path, monkeypatch) -> None:
    store = Store(tmp_path / "s.db")
    c = ProcessPresenceCollector(("chrome", "discord", "-evil", "bad*"))
    # invalid names filtered
    assert "chrome" in c.names and "discord" in c.names
    assert "-evil" not in c.names and "bad*" not in c.names

    monkeypatch.setattr(c, "_pgrep", lambda name: [1, 2] if name == "chrome" else [])
    assert c.tick(store) == 1
    assert c.tick(store) == 0
    snap = store.last_by_kind("process_snapshot")
    assert snap is not None
    assert snap.payload["processes"]["chrome"]["running"] is True
    assert snap.payload["processes"]["discord"]["running"] is False
    assert store.last_by_kind("process") is None  # no per-process rows
    store.close()
