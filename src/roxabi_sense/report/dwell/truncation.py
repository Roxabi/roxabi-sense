"""Bound focus-derived report products to evidence in an incomplete day prefix."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from roxabi_sense.report.meetings.sessions import MeetingSession
from roxabi_sense.store import Event, Store
from roxabi_sense.util.time import parse_ts, to_z


def focus_boundary(events: list[Event], start: datetime, horizon: datetime) -> datetime:
    """The last admitted focus or coverage event, never an unrelated event."""
    last = max(
        (parse_ts(e.ts) for e in events if e.kind in {"focus", "focus_coverage", "focus_legacy"}),
        default=start,
    )
    return min(horizon, last)


def _tern_prefix(
    store: Store, events: list[Event], start: str, horizon: datetime, truncated: bool,
) -> tuple[list[Event], str | None, datetime]:
    prefix_end = parse_ts(events[-1].ts) if truncated and events else (
        parse_ts(start) if truncated else horizon
    )
    snapshot_end = prefix_end if truncated else max(horizon, datetime.now(UTC))
    snapshots = list(store.iter_kind_before(
        "tern_snapshot", to_z(snapshot_end + timedelta(seconds=1))
    ))
    if truncated:
        last_key = (events[-1].ts, events[-1].id) if events else (start, 0)
        snapshots = [event for event in snapshots if (event.ts, event.id) <= last_key]
    return snapshots, None if truncated else store.get_meta("tern_observation"), prefix_end


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
