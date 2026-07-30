from __future__ import annotations

from pathlib import Path

from roxabi_sense.collectors.focus_atspi import FocusAtspiCollector, WindowInfo
from roxabi_sense.store import Store


def test_focus_writes_active_window(tmp_path: Path) -> None:
    windows = [
        WindowInfo(app="Discord", title="#chan", active=False, role="frame"),
        WindowInfo(app="Unnamed", title="grok — sense", active=True, role="frame"),
    ]
    store = Store(tmp_path / "s.db")
    c = FocusAtspiCollector(probe=lambda: windows)
    assert c.tick(store) == 2
    assert c.tick(store) == 0
    focus = store.last_by_kind("focus")
    assert focus is not None
    assert focus.payload["title"] == "grok — sense"
    snap = store.last_by_kind("desktop_snapshot")
    assert snap is not None
    assert snap.payload["focus"]["app"] == "Unnamed"
    store.close()
