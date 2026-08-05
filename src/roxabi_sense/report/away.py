"""Away / idle gap inference for day recap (ADR-002)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from roxabi_sense.store import Event
from roxabi_sense.util.time import parse_ts, to_z

IDLE_GAP_S = 300.0  # protocol + degraded silence threshold (ADR-002)
_ACTIVITY_KINDS = frozenset({"focus", "desktop_snapshot"})


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


def away_segments(
    events: list[Event],
    *,
    horizon: datetime,
    gap_s: float = IDLE_GAP_S,
    window_start: datetime | None = None,
    prior_idle: Event | None = None,
) -> list[AwaySegment]:
    """
    Prefer protocol idle transitions (ADR-002); else degraded activity gaps.

    ``prior_idle`` = last idle before ``window_start`` so overnight idle opened
    the previous local day still cuts focus dwell (no last-app soak).
    """
    protocol = _protocol_away(
        events,
        horizon=horizon,
        gap_s=gap_s,
        window_start=window_start,
        prior_idle=prior_idle,
    )
    if protocol:
        return protocol
    return _degraded_away(events, horizon=horizon, gap_s=gap_s, window_start=window_start)


def _idle_open_start(payload: dict, event_ts: str, gap_s: float) -> datetime:
    since_raw = payload.get("idle_since")
    if since_raw:
        try:
            return parse_ts(str(since_raw))
        except ValueError:
            pass
    return parse_ts(event_ts) - timedelta(seconds=gap_s)


def _idle_mode_of(payload: dict) -> str:
    src = str(payload.get("source") or "idle")
    return src if src in {"wayland-idle", "logind"} else src


def _emit(
    start: datetime,
    end: datetime,
    mode: str,
    *,
    window_start: datetime | None,
) -> AwaySegment | None:
    if window_start is not None and start < window_start:
        start = window_start
    if end <= start:
        return None
    return AwaySegment(
        start=to_z(start),
        end=to_z(end),
        duration_s=(end - start).total_seconds(),
        mode=mode,
    )


def _protocol_away(
    events: list[Event],
    *,
    horizon: datetime,
    gap_s: float,
    window_start: datetime | None,
    prior_idle: Event | None,
) -> list[AwaySegment]:
    idle_ev = [e for e in events if e.kind == "idle" and isinstance(e.payload.get("idle"), bool)]
    idle_ev.sort(key=lambda e: (e.ts, e.id))
    open_start: datetime | None = None
    open_mode = "wayland-idle"
    if prior_idle is not None and prior_idle.payload.get("idle") is True:
        open_start = _idle_open_start(prior_idle.payload, prior_idle.ts, gap_s)
        open_mode = _idle_mode_of(prior_idle.payload)
    if not idle_ev and open_start is None:
        return []
    out: list[AwaySegment] = []
    for e in idle_ev:
        if e.payload.get("idle") is True:
            # Already open (carry-in or prior True): keep earliest open_start.
            # Watch respawn / logind handoff can re-emit True without False;
            # clobbering would drop midnight→re-True and soak last app again.
            if open_start is None:
                open_start = _idle_open_start(e.payload, e.ts, gap_s)
                open_mode = _idle_mode_of(e.payload)
        elif e.payload.get("idle") is False and open_start is not None:
            seg = _emit(open_start, parse_ts(e.ts), open_mode, window_start=window_start)
            if seg is not None:
                out.append(seg)
            open_start = None
    if open_start is not None:
        seg = _emit(open_start, horizon, open_mode, window_start=window_start)
        if seg is not None:
            out.append(seg)
    return out


def _degraded_away(
    events: list[Event],
    *,
    horizon: datetime,
    gap_s: float,
    window_start: datetime | None,
) -> list[AwaySegment]:
    times = sorted(parse_ts(e.ts) for e in events if e.kind in _ACTIVITY_KINDS)
    if not times:
        return []
    uniq: list[datetime] = [times[0]]
    for t in times[1:]:
        if (t - uniq[-1]).total_seconds() >= 0.5:
            uniq.append(t)
    out: list[AwaySegment] = []
    for i in range(len(uniq) - 1):
        if (uniq[i + 1] - uniq[i]).total_seconds() >= gap_s:
            seg = _emit(uniq[i], uniq[i + 1], "degraded-gap", window_start=window_start)
            if seg is not None:
                out.append(seg)
    if (horizon - uniq[-1]).total_seconds() >= gap_s:
        seg = _emit(uniq[-1], horizon, "degraded-gap", window_start=window_start)
        if seg is not None:
            out.append(seg)
    return out
