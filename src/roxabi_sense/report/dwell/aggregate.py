"""Rollups over focus segments: totals, top titles, local-hour buckets."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import TYPE_CHECKING

from roxabi_sense.report.away import AwaySegment
from roxabi_sense.report.segments import FocusSegment
from roxabi_sense.util.time import parse_ts

if TYPE_CHECKING:
    from roxabi_sense.report.meeting_sessions import MeetingSession


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
        remaining, cursor = duration_s, parse_ts(start_z).astimezone()
        while remaining > 0.5:
            hour_end = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            slice_s = min(remaining, (hour_end - cursor).total_seconds())
            if slice_s <= 0:
                break
            buckets[cursor.strftime("%H:00")][label] += slice_s
            remaining -= slice_s
            cursor = hour_end

    for s in segments:
        _add(s.start, s.duration_s, s.app)
    for m in meetings or []:
        _add(m.start, m.duration_s, "meeting" if m.phase == "in_call" else "tab_open")
    for a in away or []:
        if meetings is not None and a.presence == "meeting":
            continue
        _add(a.start, a.duration_s, "meeting" if a.presence == "meeting" else "away")
    return [
        (h, sorted(buckets[h].items(), key=lambda kv: (-kv[1], kv[0])))
        for h in sorted(buckets)
    ]
