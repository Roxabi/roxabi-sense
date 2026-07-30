from __future__ import annotations

from pathlib import Path

from roxabi_sense.collectors import focus_atspi
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
    store.close()


def test_no_active_window_snapshot_only(tmp_path: Path) -> None:
    windows = [WindowInfo(app="Slack", title="x", active=False, role="frame")]
    store = Store(tmp_path / "s.db")
    c = FocusAtspiCollector(probe=lambda: windows)
    assert c.tick(store) == 1
    assert store.last_by_kind("focus") is None
    assert store.last_by_kind("desktop_snapshot") is not None
    store.close()


def test_default_probe_bad_json(monkeypatch) -> None:
    class R:
        stdout = "not-json\n"
        returncode = 0

    monkeypatch.setattr(focus_atspi.subprocess, "run", lambda *a, **k: R())
    assert focus_atspi._default_probe() == []


def test_default_probe_timeout(monkeypatch) -> None:
    def boom(*a, **k):
        raise focus_atspi.subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(focus_atspi.subprocess, "run", boom)
    assert focus_atspi._default_probe() == []
