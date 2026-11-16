"""Rollups over focus segments: totals, top titles, local-hour buckets."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import TYPE_CHECKING

from roxabi_sense.report.away import AwaySegment
from roxabi_sense.report.segments import FocusSegment
from roxabi_sense.util.time import parse_ts

if TYPE_CHECKING:
    from roxabi_sense.report.meetings.sessions import MeetingSession


def sum_by(segments: list[FocusSegment], *, key) -> list[tuple[str, float]]:
    totals: dict[str, float] = defaultdict(float)
    for s in segments:
        totals[key(s)] += s.duration_s
    return sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))


def top_titles(segments: list[FocusSegment], *, limit: int) -> list[tuple[str, float, str]]:
    totals: dict[tuple[str, str], float] = defaultdict(float)
    for s in segments:
        if not s.title:
            continue
        totals[(s.title, s.app)] += s.duration_s
    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0][0]))
    return [(title, secs, app) for (title, app), secs in ranked[:limit]]


def hour_apps(
    segments: list[FocusSegment],
    away: list[AwaySegment] | None = None,
    *,
    meetings: list[MeetingSession] | None = None,
) -> list[tuple[str, list[tuple[str, float]]]]:
    """Bucket focus + optional away/meeting sessions into local-hour slices."""
    buckets: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    def _add(start_z: str, duration_s: float, label: str) -> None:
        remaining, cursor = duration_s, parse_ts(start_z)
        while remaining > 0:
            local = cursor.astimezone()
            until_hour = 3600 - (local.minute * 60 + local.second + local.microsecond / 1_000_000)
            slice_s = min(remaining, until_hour)
            buckets[local.strftime("%H:00")][label] += slice_s
            remaining -= slice_s
            cursor += timedelta(seconds=slice_s)
    for s in segments:
        _add(s.start, s.duration_s, s.app)
    from roxabi_sense.report.meetings.evidence import union

    observed = union([
        (parse_ts(m.start), parse_ts(m.end))
        for m in meetings or [] if m.phase == "in_call" and m.evidence == "observed"
    ])
    for start, end in observed:
        _add(start.isoformat(), (end - start).total_seconds(), "meeting")
    for a in away or []:
        if meetings is not None and a.presence == "meeting":
            continue
        _add(a.start, a.duration_s, "meeting" if a.presence == "meeting" else "away")
    return [
        (h, sorted(buckets[h].items(), key=lambda kv: (-kv[1], kv[0])))
        for h in sorted(buckets)
    ]
