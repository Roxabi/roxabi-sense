"""Bound focus-derived report products to evidence in an incomplete day prefix."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from roxabi_sense.report.meetings.sessions import MeetingSession
from roxabi_sense.store import Event
from roxabi_sense.util.time import parse_ts, to_z


def focus_boundary(events: list[Event], start: datetime, horizon: datetime) -> datetime:
    """The last admitted focus or coverage event, never an unrelated event."""
    last = max(
        (parse_ts(e.ts) for e in events if e.kind in {"focus", "focus_coverage", "focus_legacy"}),
        default=start,
    )
    return min(horizon, last)


def clip_inferred(sessions: list[MeetingSession], end: datetime) -> list[MeetingSession]:
    """Bound inferred sessions to the admitted event prefix, independently of focus."""
    result = []
    for session in sessions:
        start = parse_ts(session.start)
        stop = min(parse_ts(session.end), end)
        if start < stop:
            result.append(
                replace(session, end=to_z(stop), duration_s=(stop - start).total_seconds())
            )
    return result
