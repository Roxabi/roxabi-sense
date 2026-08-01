"""Compiled products over the event store (day recap, presence, status, etc.)."""

from roxabi_sense.report.day import DayRecap, compile_day_recap, format_day_recap
from roxabi_sense.report.event_summary import summarize_event
from roxabi_sense.report.presence import (
    Presence,
    derive_presence,
    format_presence_lines,
    presence_from_store,
)
from roxabi_sense.report.status import (
    StatusSnapshot,
    load_status_snapshot,
    status_snapshot,
    status_snapshot_missing,
)

__all__ = [
    "DayRecap",
    "Presence",
    "StatusSnapshot",
    "compile_day_recap",
    "derive_presence",
    "format_day_recap",
    "format_presence_lines",
    "load_status_snapshot",
    "presence_from_store",
    "status_snapshot",
    "status_snapshot_missing",
    "summarize_event",
]
