"""Coverage exclusions for historical focus dwell."""
from __future__ import annotations

from datetime import datetime

from roxabi_sense.store import Event
from roxabi_sense.util.time import parse_ts


def coverage_cuts(
    coverage: list[Event], focus: list[Event], horizon: datetime
) -> list[tuple[datetime, datetime]]:
    cuts: list[tuple[datetime, datetime]] = []
    for i, event in enumerate(coverage):
        start = parse_ts(event.ts)
        end = parse_ts(coverage[i + 1].ts) if i + 1 < len(coverage) else horizon
        if event.payload.get("state") == "legacy_unverified":
            continue
        if event.payload.get("state") != "observed":
            cuts.append((start, end))
            continue
        previous_focus = next((f for f in reversed(focus) if parse_ts(f.ts) <= start), None)
        window_id = event.payload.get("window_id")
        matches = (
            previous_focus is not None
            and previous_focus.payload.get("source") == event.payload.get("source")
            and (not window_id or previous_focus.payload.get("window_id") == window_id)
        )
        previous = coverage[i - 1] if i else None
        recovering = bool(previous and (
            previous.payload.get("state") != "observed"
            or previous.payload.get("source") != event.payload.get("source")
            or previous.payload.get("window_id") != window_id
        ))
        if recovering or not matches:
            matching = [
                parse_ts(f.ts) for f in focus
                if start <= parse_ts(f.ts) < end
                and f.payload.get("source") == event.payload.get("source")
                and (not window_id or f.payload.get("window_id") == window_id)
            ]
            cuts.append((start, min(matching, default=end)))
    return cuts


def identity_cuts(
    coverage: list[Event], horizon: datetime, source: str | None, window_id: str | None
) -> list[tuple[datetime, datetime]]:
    return [
        (parse_ts(event.ts), parse_ts(coverage[i + 1].ts) if i + 1 < len(coverage) else horizon)
        for i, event in enumerate(coverage)
        if event.payload.get("state") in {"observed", "legacy_unverified"}
        and (
            event.payload.get("source") != source
            or (event.payload.get("window_id") and event.payload.get("window_id") != window_id)
        )
    ]
