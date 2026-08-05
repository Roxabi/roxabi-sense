"""Focus dwell segments; away gaps live in ``report.away`` (ADR-002)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from roxabi_sense.report.away import IDLE_GAP_S, AwaySegment, away_segments
from roxabi_sense.store import Event
from roxabi_sense.util.time import parse_ts, to_z
from roxabi_sense.util.titles import normalize_title

if TYPE_CHECKING:
    from roxabi_sense.report.meeting_sessions import MeetingSession

# Re-export for existing importers (meeting, top_apps, tests).
__all__ = [
    "AwaySegment",
    "FocusSegment",
    "IDLE_GAP_S",
    "MIN_DWELL_S",
    "away_segments",
    "focus_segments",
    "horizon_dt",
    "hour_apps",
    "norm_app",
    "sum_by",
    "top_titles",
]

MIN_DWELL_S = 3.0  # ignore micro-focus flickers
_APP_ALIASES: dict[str, str] = {
    "unnamed": "ghostty",
    "xdg-desktop-portal-gtk": "dialog",
}


@dataclass(frozen=True)
class FocusSegment:
    start: str
    end: str
    duration_s: float
    app: str
    title: str
    cwd: str | None = None
    agent: str | None = None


def norm_app(raw: str | None) -> str:
    app = (raw or "?").strip() or "?"
    return _APP_ALIASES.get(app.lower(), app)


def horizon_dt(day_end_z: str, now: datetime | None) -> datetime:
    end = parse_ts(day_end_z)
    n = now or datetime.now(UTC)
    if n.tzinfo is None:
        n = n.replace(tzinfo=UTC)
    return min(n, end)


def focus_segments(
    focus_events: list[Event],
    away: list[AwaySegment],
    *,
    horizon: datetime,
) -> list[FocusSegment]:
    """Attribute focus dwell, cutting out away gaps."""
    if not focus_events:
        return []
    collapsed: list[tuple[datetime, str, str, str | None, str | None]] = []
    for e in focus_events:
        app = norm_app(str(e.payload.get("app") or ""))
        title = normalize_title(str(e.payload.get("title") or ""))
        ag = e.payload.get("agent") if isinstance(e.payload.get("agent"), dict) else {}
        cwd_s = str(ag["cwd"]) if isinstance(ag, dict) and ag.get("cwd") else None
        agent_s = str(ag["agent"]) if isinstance(ag, dict) and ag.get("agent") else None
        t = parse_ts(e.ts)
        if collapsed and collapsed[-1][1] == app and collapsed[-1][2] == title:
            prev = collapsed[-1]
            if cwd_s and not prev[3]:
                collapsed[-1] = (prev[0], app, title, cwd_s, agent_s or prev[4])
            continue
        collapsed.append((t, app, title, cwd_s, agent_s))

    away_ranges = [(parse_ts(a.start), parse_ts(a.end)) for a in away]
    segs: list[FocusSegment] = []
    for i, (t0, app, title, cwd, agent) in enumerate(collapsed):
        t1 = collapsed[i + 1][0] if i + 1 < len(collapsed) else horizon
        if t1 < t0:
            t1 = t0
        for a0, a1 in _subtract_ranges(t0, t1, away_ranges):
            dur = (a1 - a0).total_seconds()
            if dur < MIN_DWELL_S:
                continue
            segs.append(
                FocusSegment(
                    start=to_z(a0),
                    end=to_z(a1),
                    duration_s=dur,
                    app=app,
                    title=title,
                    cwd=cwd,
                    agent=agent,
                )
            )
    return segs


def _subtract_ranges(
    start: datetime,
    end: datetime,
    cuts: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """Return [start,end) minus overlapping cut intervals."""
    if end <= start:
        return []
    pieces: list[tuple[datetime, datetime]] = [(start, end)]
    for c0, c1 in cuts:
        nxt: list[tuple[datetime, datetime]] = []
        for p0, p1 in pieces:
            if c1 <= p0 or c0 >= p1:
                nxt.append((p0, p1))
                continue
            if p0 < c0:
                nxt.append((p0, min(c0, p1)))
            if c1 < p1:
                nxt.append((max(c1, p0), p1))
        pieces = [(a, b) for a, b in nxt if b > a]
    return pieces


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
