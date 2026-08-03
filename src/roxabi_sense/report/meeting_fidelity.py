"""Desktop inventory class for meeting-session honesty (ADR-004).

Call duration is trustworthy when the host can list **background** meeting
chrome. Single-window backends (typical wlr/x11) are ``active_only`` and may
under-count multitask calls unless partial-inventory samples are ignored for
clear (see ``meeting_samples``).
"""

from __future__ import annotations

from typing import Any, Literal

from roxabi_sense.store import Event

InventoryClass = Literal["full", "active_only", "none"]
MeetingFidelity = Literal["full", "active_only", "none", "unknown"]

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
    """
    Day-level honesty label for meeting totals.

    Returns (class, short operator note).
    """
    saw_full = False
    saw_partial = False
    saw_any = False
    for e in events:
        if e.kind != "desktop_snapshot" or not isinstance(e.payload, dict):
            continue
        wins = e.payload.get("windows")
        if not isinstance(wins, list):
            continue
        saw_any = True
        inv = e.payload.get("inventory")
        if not isinstance(inv, str):
            inv = inventory_for_windows(wins, source=str(e.payload.get("source") or ""))
        if inv == "full":
            saw_full = True
        elif inv == "active_only":
            saw_partial = True

    backend = (focus_backend or "").lower()
    if saw_full:
        note = (
            "desktop inventory full — multitask call duration is trustworthy "
            "(window-signal, not calendar hangup)"
        )
        return "full", note
    if saw_partial or backend in _ACTIVE_ONLY_SOURCES:
        note = (
            "desktop inventory active_only — multitask may under-count meetings; "
            "prefer AT-SPI / multi-window backend for honest totals"
        )
        return "active_only", note
    if not saw_any:
        if backend == "atspi":
            return (
                "unknown",
                "no desktop_snapshot yet — meeting fidelity unknown until ticks",
            )
        if backend in _ACTIVE_ONLY_SOURCES:
            return (
                "active_only",
                f"focus_backend={backend} typically single-window desktop",
            )
        return "unknown", "no desktop_snapshot evidence for meeting fidelity"
    return "unknown", "desktop snapshots present but inventory class unclear"
