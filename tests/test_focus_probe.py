"""FocusProbe protocol + collector source wiring."""

from __future__ import annotations

from pathlib import Path

from roxabi_sense.collectors.focus.collector import FocusCollector
from roxabi_sense.collectors.focus.protocol import FocusWindow
from roxabi_sense.store import Store


def test_fake_probe_writes_source(tmp_path: Path, monkeypatch) -> None:

    class _FakeProbe:
        source: str = "x11"

        def __init__(self, windows: list[FocusWindow]) -> None:
            self._windows = windows

        def probe(self) -> bool:
            return True

        def get_active(self) -> list[FocusWindow]:
            return list(self._windows)

        def get_desktop(self) -> list[FocusWindow]:
            return list(self._windows)

    monkeypatch.setattr(
        "roxabi_sense.collectors.focus.collector.resolve_app_name",
        lambda app, pid: app,
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus.collector.find_agent_link",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus.collector.children_map",
        lambda: {},
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus.collector.list_tmux_agent_panes",
        lambda: [],
    )
    probe = _FakeProbe(
        [FocusWindow(app="kitty", title="shell", active=True, role="frame", pid=1)]
    )
    store = Store(tmp_path / "s.db")
    c = FocusCollector(focus_probe=probe, sessions_loader=lambda: [])
    assert c.tick_focus(store) == 1
    ev = store.last_by_kind("focus")
    assert ev is not None
    assert ev.payload["source"] == "x11"
    assert ev.payload["app"] == "kitty"
    store.close()
