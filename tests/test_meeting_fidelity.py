"""Meeting fidelity / desktop inventory class."""

from __future__ import annotations

from roxabi_sense.report.meeting_fidelity import (
    inventory_for_windows,
    is_clear_grade_inventory,
    meeting_fidelity_from_events,
)
from roxabi_sense.store import Event


def test_inventory_for_windows() -> None:
    assert inventory_for_windows([]) == "none"
    assert inventory_for_windows([{"app": "a"}]) == "active_only"
    assert inventory_for_windows([{"app": "a"}, {"app": "b"}]) == "full"


def test_clear_grade_requires_full() -> None:
    assert is_clear_grade_inventory("full", n_windows=2, source="atspi") is True
    assert is_clear_grade_inventory("active_only", n_windows=1, source="wlr") is False
    assert is_clear_grade_inventory(None, n_windows=1, source="wlr") is False
    assert is_clear_grade_inventory(None, n_windows=2, source="wlr") is True


def test_fidelity_from_events_full() -> None:
    events = [
        Event(
            id=1,
            ts="2026-08-03T09:00:00Z",
            kind="desktop_snapshot",
            payload={
                "windows": [{"app": "a"}, {"app": "b"}],
                "inventory": "full",
                "source": "atspi",
            },
        )
    ]
    fid, note = meeting_fidelity_from_events(events, focus_backend="atspi")
    assert fid == "full"
    assert "trustworthy" in note


def test_fidelity_active_only_backend() -> None:
    events = [
        Event(
            id=1,
            ts="2026-08-03T09:00:00Z",
            kind="desktop_snapshot",
            payload={
                "windows": [{"app": "ghostty"}],
                "inventory": "active_only",
                "source": "wlr",
            },
        )
    ]
    fid, note = meeting_fidelity_from_events(events, focus_backend="wlr")
    assert fid == "active_only"
    assert "under-count" in note
