"""Focus dwell + degraded away inference from activity gaps."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from roxabi_sense.store import Event
from roxabi_sense.util.time import parse_ts, to_z
from roxabi_sense.util.titles import normalize_title

# Ignore micro-focus flickers when attributing dwell time.
MIN_DWELL_S = 3.0

# Degraded idle: silence ≥ this long → away from last activity (ADR-002).
IDLE_GAP_S = 300.0
_ACTIVITY_KINDS = frozenset({"focus", "desktop_snapshot"})

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


@dataclass(frozen=True)
class AwaySegment:
    """Inferred absence or meeting overlay on input-idle / activity gaps."""

    start: str
    end: str
    duration_s: float
    mode: str = "degraded-gap"  # wayland-idle | logind | degraded-gap
    presence: str = "away"  # away | meeting (compile-time annotation)
    meeting_label: str | None = None
    meeting_provider: str | None = None  # meet | zoom | teams


def norm_app(raw: str | None) -> str:
    app = (raw or "?").strip() or "?"
    return _APP_ALIASES.get(app.lower(), app)


def horizon_dt(day_end_z: str, now: datetime | None) -> datetime:
    end = parse_ts(day_end_z)
    n = now or datetime.now(UTC)
    if n.tzinfo is None:
        n = n.replace(tzinfo=UTC)
    return min(n, end)


def away_segments(
    events: list[Event],
    *,
    horizon: datetime,
    gap_s: float = IDLE_GAP_S,
) -> list[AwaySegment]:
    """
    Prefer protocol idle transitions (ADR-002); else degraded activity gaps.

    Protocol: kind=idle with idle true/false and source (wayland-idle|logind).
    Degraded: silence ≥ gap_s on focus/desktop_snapshot from last activity.
    """
    protocol = _protocol_away_segments(events, horizon=horizon, gap_s=gap_s)
    if protocol:
        return protocol
    return _degraded_gap_away_segments(events, horizon=horizon, gap_s=gap_s)


def _protocol_away_segments(
    events: list[Event],
    *,
    horizon: datetime,
    gap_s: float,
) -> list[AwaySegment]:
    idle_ev = [e for e in events if e.kind == "idle" and isinstance(e.payload.get("idle"), bool)]
    if not idle_ev:
        return []
    idle_ev.sort(key=lambda e: (e.ts, e.id))
    out: list[AwaySegment] = []
    open_start: datetime | None = None
    open_mode = "wayland-idle"
    for e in idle_ev:
        src = str(e.payload.get("source") or "idle")
        mode = src if src in {"wayland-idle", "logind"} else src
        if e.payload.get("idle") is True:
            since_raw = e.payload.get("idle_since")
            if since_raw:
                try:
                    open_start = parse_ts(str(since_raw))
                except ValueError:
                    open_start = parse_ts(e.ts) - timedelta(seconds=gap_s)
            else:
                open_start = parse_ts(e.ts) - timedelta(seconds=gap_s)
            open_mode = mode
        elif e.payload.get("idle") is False and open_start is not None:
            end = parse_ts(e.ts)
            if end > open_start:
                out.append(
                    AwaySegment(
                        start=to_z(open_start),
                        end=to_z(end),
                        duration_s=(end - open_start).total_seconds(),
                        mode=open_mode,
                    )
                )
            open_start = None
    if open_start is not None and horizon > open_start:
        out.append(
            AwaySegment(
                start=to_z(open_start),
                end=to_z(horizon),
                duration_s=(horizon - open_start).total_seconds(),
                mode=open_mode,
            )
        )
    return out


def _degraded_gap_away_segments(
    events: list[Event],
    *,
    horizon: datetime,
    gap_s: float,
) -> list[AwaySegment]:
    """Away when activity gap ≥ gap_s; idle starts at last activity."""
    times: list[datetime] = []
    for e in events:
        if e.kind not in _ACTIVITY_KINDS:
            continue
        times.append(parse_ts(e.ts))
    if not times:
        return []
    times.sort()
    uniq: list[datetime] = [times[0]]
    for t in times[1:]:
        if (t - uniq[-1]).total_seconds() >= 0.5:
            uniq.append(t)

    out: list[AwaySegment] = []
    for i in range(len(uniq) - 1):
        gap = (uniq[i + 1] - uniq[i]).total_seconds()
        if gap >= gap_s:
            out.append(
                AwaySegment(
                    start=to_z(uniq[i]),
                    end=to_z(uniq[i + 1]),
                    duration_s=gap,
                    mode="degraded-gap",
                )
            )
    tail = (horizon - uniq[-1]).total_seconds()
    if tail >= gap_s:
        out.append(
            AwaySegment(
                start=to_z(uniq[-1]),
                end=to_z(horizon),
                duration_s=tail,
                mode="degraded-gap",
            )
        )
    return out


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
) -> list[tuple[str, list[tuple[str, float]]]]:
    """Bucket focus (+ optional away) dwell into local-hour slices."""
    buckets: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    def _add(start_z: str, duration_s: float, label: str) -> None:
        t0 = parse_ts(start_z).astimezone()
        remaining = duration_s
        cursor = t0
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
    if away:
        for a in away:
            label = "meeting" if a.presence == "meeting" else "away"
            _add(a.start, a.duration_s, label)

    ordered: list[tuple[str, list[tuple[str, float]]]] = []
    for hour in sorted(buckets.keys()):
        apps = sorted(buckets[hour].items(), key=lambda kv: (-kv[1], kv[0]))
        ordered.append((hour, apps))
    return ordered
