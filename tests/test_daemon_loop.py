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
