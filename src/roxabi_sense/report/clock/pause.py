"""Stretch, recent-away, and pause-clock facts for the care brief."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from roxabi_sense.report.away import IDLE_GAP_S
from roxabi_sense.util.time import parse_ts

_STRETCH_MAX_AGE_S = 120.0
_RECENT_AWAY_S = 15 * 60.0


def _current_stretch(
    attn: list[Any], *, now: datetime, presence_state: str
) -> dict[str, Any] | None:
    if presence_state != "active" or not attn:
        return None
    last = max(attn, key=lambda s: str(getattr(s, "end", "") or ""))
    try:
        age = (now - parse_ts(str(last.end))).total_seconds()
    except (TypeError, ValueError):
        return None
    if age > _STRETCH_MAX_AGE_S:
        return None
    return {
        "app": getattr(last, "app", "?"),
        "minutes": round(float(getattr(last, "duration_s", 0.0) or 0.0) / 60.0, 2),
    }


def _last_away(
    aways: list[Any],
    *,
    now: datetime,
    presence_state: str,
    idle_since: Any,
) -> dict[str, Any] | None:
    if presence_state == "idle":
        minutes: float | None = None
        if idle_since:
            try:
                minutes = round((now - parse_ts(str(idle_since))).total_seconds() / 60.0, 2)
            except ValueError:
                minutes = None
        return {"minutes": minutes, "ongoing": True}
    pure = [a for a in aways if getattr(a, "presence", "away") != "meeting"]
    if not pure:
        return None
    last = max(pure, key=lambda a: str(getattr(a, "end", "") or ""))
    try:
        ended_ago = (now - parse_ts(str(last.end))).total_seconds()
    except (TypeError, ValueError):
        return None
    if ended_ago > _RECENT_AWAY_S:
        return None
    return {
        "minutes": round(float(getattr(last, "duration_s", 0.0) or 0.0) / 60.0, 2),
        "ongoing": False,
        "end": str(last.end),
    }


def _as_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parse_ts(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _pause_clock(
    aways: list[Any],
    *,
    now: datetime,
    presence_state: str,
    idle_since: Any,
    first_event: Any,
) -> tuple[dict[str, Any] | None, float | None]:
    """Last qualifying pause (≥ idle gap) and wall minutes since it ended.

    Qualifying = idle/away of at least IDLE_GAP_S (ADR-002). Ongoing idle
    shorter than that does not reset the clock. No recency window: a pause
    earlier today still answers minutes_since_pause.
    """
    idle_start = _as_dt(idle_since) if presence_state == "idle" else None
    if idle_start is not None:
        idle_s = (now - idle_start).total_seconds()
        if idle_s >= IDLE_GAP_S:
            return (
                {
                    "start": str(idle_since),
                    "end": None,
                    "minutes": round(idle_s / 60.0, 2),
                    "ongoing": True,
                },
                0.0,
            )

    qualifying: list[tuple[datetime, Any, float]] = []
    for away in aways:
        if getattr(away, "presence", "away") == "meeting":
            continue
        duration_s = float(getattr(away, "duration_s", 0.0) or 0.0)
        if duration_s < IDLE_GAP_S:
            continue
        ended = _as_dt(getattr(away, "end", None))
        if ended is None:
            continue
        qualifying.append((ended, away, duration_s))
    if qualifying:
        ended, away, duration_s = max(qualifying, key=lambda item: item[0])
        start = getattr(away, "start", None)
        return (
            {
                "start": str(start) if start else None,
                "end": str(away.end),
                "minutes": round(duration_s / 60.0, 2),
                "ongoing": False,
            },
            round(max(0.0, (now - ended).total_seconds() / 60.0), 2),
        )

    origin = _as_dt(first_event)
    if origin is None:
        return None, None
    return None, round(max(0.0, (now - origin).total_seconds() / 60.0), 2)
