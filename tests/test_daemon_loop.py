"""Unit tests for daemon focus event/poll scheduling helpers.

Full run_daemon is integration-heavy (signals/GLib); we test the extracted
tick helpers and config-driven collector sets that encode the fallback policy.
"""

from __future__ import annotations

from pathlib import Path

from roxabi_sense.config import SenseConfig
from roxabi_sense.daemon import (
    build_collectors,
    build_poll_collectors,
    tick_all,
    tick_one,
)
from roxabi_sense.store import Store


class _Focus:
    name = "focus_atspi"

    def __init__(self) -> None:
        self.n = 0

    def tick(self, store: Store) -> int:
        self.n += 1
        store.append("focus", {"app": "x", "title": "t"})
        return 1


class _Boom:
    name = "boom"

    def tick(self, store: Store) -> int:
        raise RuntimeError("nope")


def test_tick_one_isolates_and_batches(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    assert tick_one(_Boom(), store, label="boom") == 0
    f = _Focus()
    assert tick_one(f, store, label="focus") == 1
    assert store.count() == 1
    store.close()


def test_poll_collectors_never_include_focus_by_default() -> None:
    cfg = SenseConfig(focus=True, focus_events=True)
    names = [c.name for c in build_poll_collectors(cfg)]
    assert "focus_atspi" not in names
    assert any(c.name == "focus_atspi" for c in build_collectors(cfg))


def test_focus_events_false_policy_via_collectors() -> None:
    """When events off, once still has focus; poll list still excludes it —
    daemon folds focus into poll via focus_on_poll flag (tested by mode logic)."""
    cfg = SenseConfig(focus=True, focus_events=False)
    assert cfg.focus_events is False
    assert any(c.name == "focus_atspi" for c in build_collectors(cfg))


def test_tick_all_still_batches_multiple(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    f1, f2 = _Focus(), _Focus()
    n = tick_all([f1, f2], store)
    assert n == 2
    assert f1.n == 1 and f2.n == 1
    store.close()


class _FocusDual:
    name = "focus_atspi"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def tick(self, store: Store) -> int:
        self.calls.append("tick")
        store.append("focus", {"app": "x", "title": "t"})
        return 1

    def tick_focus(self, store: Store) -> int:
        self.calls.append("tick_focus")
        store.append("focus", {"app": "x", "title": "t"})
        return 1

    def tick_desktop(self, store: Store) -> int:
        self.calls.append("tick_desktop")
        store.append("desktop_snapshot", {"windows": []})
        return 1


def test_tick_one_method_focus_and_desktop(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    f = _FocusDual()
    assert tick_one(f, store, label="ev", method="tick_focus") == 1
    assert tick_one(f, store, label="desk", method="tick_desktop") == 1
    assert f.calls == ["tick_focus", "tick_desktop"]
    store.close()


def test_config_focus_lighten_defaults() -> None:
    cfg = SenseConfig()
    assert cfg.focus_backup_seconds == 180.0
    assert cfg.focus_name_events == "throttled"
    assert cfg.focus_name_throttle_s == 10.0
    assert cfg.focus_event_min_interval_s == 0.5


def test_focus_event_gate_leading_and_trailing() -> None:
    from roxabi_sense.daemon_collectors import FocusEventGate

    g = FocusEventGate(min_interval=0.5)
    assert g.on_event(1.0) is True
    assert g.trailing_at is None
    # burst inside window → schedule trailing, no immediate probe
    assert g.on_event(1.1) is False
    assert g.trailing_at == 1.5
    assert g.on_event(1.2) is False
    assert g.trailing_at == 1.5
    # before trailing deadline
    assert g.on_timer(1.4) is False
    # trailing fires once
    assert g.on_timer(1.5) is True
    assert g.trailing_at is None
    assert g.on_timer(1.6) is False
