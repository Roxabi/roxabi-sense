"""Compiled products over the event store (day recap, presence, status, etc.)."""

from roxabi_sense.report.day import (
    DayRecap,
    compile_day_recap,
    format_day_recap,
    format_day_recap_share,
)
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
from roxabi_sense.report.top_apps import AppDwell, session_shape, top_apps

__all__ = [
    "AppDwell",
    "DayRecap",
    "Presence",
    "StatusSnapshot",
    "compile_day_recap",
    "derive_presence",
    "format_day_recap",
    "format_day_recap_share",
    "format_presence_lines",
    "load_status_snapshot",
    "presence_from_store",
    "session_shape",
    "status_snapshot",
    "status_snapshot_missing",
    "summarize_event",
    "top_apps",
]
