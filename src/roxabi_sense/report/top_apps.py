"""Local day app-minutes aggregate + optional session_shape (#47 / #48).

Pure report helpers over focus dwell segments — no collectors, no cloud.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import median
from typing import Any

from roxabi_sense.report.segments import AwaySegment, FocusSegment, sum_by

# Minimum tracked focus before session_shape is meaningful.
_SHAPE_MIN_TRACKED_S = 900.0  # 15 minutes
_SHAPE_MIN_SEGMENTS = 2


@dataclass(frozen=True)
class AppDwell:
    """Ranked app dwell for a local day."""

    app: str
    seconds: float
    minutes: float
    share: float  # 0..1 of tracked focus dwell

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def top_apps(
    segments: list[FocusSegment],
    *,
    limit: int = 20,
) -> list[AppDwell]:
    """Rank apps by dwell seconds (from focus_segments)."""
    pairs = sum_by(segments, key=lambda s: s.app)
    total = sum(secs for _, secs in pairs)
    out: list[AppDwell] = []
    for app, secs in pairs[:limit]:
        out.append(
            AppDwell(
                app=app,
                seconds=round(float(secs), 1),
                minutes=round(float(secs) / 60.0, 2),
                share=round(float(secs) / total, 4) if total > 0 else 0.0,
            )
        )
    return out


def session_shape(
    segments: list[FocusSegment],
    away: list[AwaySegment] | None = None,
    *,
    min_tracked_s: float = _SHAPE_MIN_TRACKED_S,
) -> str | None:
    """Heuristic day shape — deterministic, local only (no XP/cloud).

    Returns one of:
      deep | steady | fragmented | drifted
    or None if insufficient data.

    Definitions (fixed thresholds):
    - **insufficient** (None): tracked focus < min_tracked_s or < 2 segments
    - **drifted**: away (non-meeting) ≥ 45% of (focus + away)
    - **fragmented**: ≥ 12 focus switches per hour of focus OR median dwell < 2m
    - **deep**: median dwell ≥ 10m AND ≤ 4 switches/hour
    - **steady**: otherwise
    """
    tracked = sum(s.duration_s for s in segments)
    if tracked < min_tracked_s or len(segments) < _SHAPE_MIN_SEGMENTS:
        return None

    away_list = away or []
    away_s = sum(a.duration_s for a in away_list if a.presence != "meeting")
    denom = tracked + away_s
    if denom > 0 and away_s / denom >= 0.45:
        return "drifted"

    switches = max(0, len(segments) - 1)
    hours = max(tracked / 3600.0, 1.0 / 60.0)  # floor 1 minute
    switch_rate = switches / hours
    dwells = [s.duration_s for s in segments if s.duration_s > 0]
    med = float(median(dwells)) if dwells else 0.0

    if switch_rate >= 12.0 or med < 120.0:
        return "fragmented"
    if med >= 600.0 and switch_rate <= 4.0:
        return "deep"
    return "steady"
