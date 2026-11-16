"""Desktop inventory facts and evidence-bounded meeting fidelity."""

from __future__ import annotations

from typing import Any, Literal

from roxabi_sense.store import Event

InventoryClass = Literal["full", "active_only", "none"]
MeetingFidelity = Literal["observed", "partial", "inferred", "unknown"]

_ACTIVE_ONLY_SOURCES = frozenset({"wlr", "x11", "noop"})


def inventory_for_windows(windows: list[Any], *, source: str | None = None) -> InventoryClass:
    """Fact for a desktop_snapshot payload (collector + compile)."""
    n = len(windows)
    if n == 0:
        return "none"
    if n >= 2:
        return "full"
    # One window: always active_only for meeting evidence purposes.
    return "active_only"


def is_clear_grade_inventory(
    inventory: str | None,
    *,
    n_windows: int,
    source: str | None,
) -> bool:
    """
    True if a no-meeting desktop sample may hard-clear an open session.

    Partial / active-only inventories must **not** clear (multitask on wlr/x11).
    """
    if inventory == "full":
        return True
    if inventory == "active_only":
        return False
    if inventory == "none":
        return False  # empty must not be emitted; if present, do not clear
    # Legacy rows without inventory key
    if n_windows >= 2:
        return True
    src = (source or "").lower()
    if src in _ACTIVE_ONLY_SOURCES:
        return False
    # Unknown single-window: treat as partial (prefer under-clear risk on wlr)
    return n_windows >= 2


def meeting_fidelity_from_events(
    events: list[Event],
    *,
    focus_backend: str | None = None,
) -> tuple[MeetingFidelity, str]:
    """Describe evidence, never infer participation from window inventory."""
    from roxabi_sense.report.meetings.evidence import compile_observations, seconds
    from roxabi_sense.report.meetings.signals import meeting_samples
    from roxabi_sense.util.time import parse_ts

    if not events:
        return "unknown", "No Chrome Google Meet source observations."
    start = min(parse_ts(event.ts) for event in events)
    end = max(parse_ts(event.ts) for event in events)
    observed, coverage, _, known = compile_observations(events, start, end)
    if known and seconds(coverage) >= (end - start).total_seconds():
        quality: MeetingFidelity = "observed"
    elif observed or coverage:
        quality = "partial"
    elif any(hint is not None for _, hint in meeting_samples(events)):
        quality = "inferred"
    else:
        quality = "unknown"
    return quality, "Chrome Google Meet observations only; unconfirmed states are unknown."
