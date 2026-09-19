"""Heartbeat care_brief — compact JSON over day recap + presence.

Facts only: apps / presence / current stretch / last pause clock. No titles, no policy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from roxabi_sense.report.away import IDLE_GAP_S
from roxabi_sense.report.event_summary import cap_json_bytes
from roxabi_sense.util.time import parse_ts

_BRIEF_APPS = 8
_STALE_AGENT_S = 1800.0
_STRETCH_MAX_AGE_S = 120.0
_RECENT_AWAY_S = 15 * 60.0
_SHAPE = {
    "deep": "focused",
    "steady": "focused",
    "fragmented": "fragmented",
    "drifted": "drifted",
}


def compile_care_brief(
    recap: Any,
    presence: dict[str, Any],
    *,
    agent_snapshot_ts: str | None = None,
    agent_payload: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Small heartbeat JSON — apps/presence/shape, never window titles."""
    n = now or datetime.now(UTC)
    if n.tzinfo is None:
        n = n.replace(tzinfo=UTC)
    apps = getattr(recap, "top_apps", []) or []
    tracked_s = sum(float(getattr(a, "seconds", 0.0) or 0.0) for a in apps)
    away_s = float(getattr(recap, "away_total_s", 0.0) or 0.0)
    shape = _SHAPE.get(getattr(recap, "session_shape", None) or "", "unknown")
    if shape == "unknown":
        tracked_m = tracked_s / 60.0
        away_m = away_s / 60.0
        if tracked_m < 15 and away_m > tracked_m and away_m >= 15:
            shape = "away"

    attn = getattr(recap, "attention_segments", []) or []
    longest = None
    if attn:
        best = max(attn, key=lambda s: float(getattr(s, "duration_s", 0.0) or 0.0))
        longest = {
            "app": getattr(best, "app", "?"),
            "minutes": round(float(getattr(best, "duration_s", 0.0) or 0.0) / 60.0, 2),
        }
    pres_state = str(presence.get("state") or "")
    current = _current_stretch(attn, now=n, presence_state=pres_state)
    last_away = _last_away(
        getattr(recap, "away_segments", []) or [],
        now=n,
        presence_state=pres_state,
        idle_since=presence.get("idle_since"),
    )
    last_pause, minutes_since_pause = _pause_clock(
        getattr(recap, "away_segments", []) or [],
        now=n,
        presence_state=pres_state,
        idle_since=presence.get("idle_since"),
        first_event=getattr(recap, "first_event", None),
    )

    stays = getattr(recap, "terminal_stays", None)
    terminal = {"median_minutes": 0.0, "count_ge_5min": 0, "count_ge_10min": 0}
    if stays is not None:
        terminal = {
            "median_minutes": round(float(stays.median_s) / 60.0, 2),
            "count_ge_5min": int(stays.ge_5m),
            "count_ge_10min": int(stays.ge_10m),
        }

    meetings = getattr(recap, "meeting_sessions", []) or []
    in_call = [m for m in meetings if getattr(m, "phase", None) == "in_call"]
    meeting_s = float(getattr(recap, "meeting_total_s", 0.0) or 0.0)
    agent_body, agent_reason = _agent_brief(recap, agent_snapshot_ts, n, agent_payload)

    top = [
        {
            "app": getattr(a, "app", "?"),
            "minutes": getattr(
                a, "minutes", round(float(getattr(a, "seconds", 0.0) or 0.0) / 60.0, 2)
            ),
            "share": getattr(a, "share", 0.0),
        }
        for a in apps[:_BRIEF_APPS]
    ]
    pres = {
        "state": presence.get("state"),
        "idle_since": presence.get("idle_since"),
        "degraded": bool(presence.get("degraded")),
        "degraded_reason": presence.get("degraded_reason"),
        "confidence": presence.get("confidence"),
    }
    return cap_json_bytes(
        {
            "day": getattr(recap, "day", None),
            "first_event": getattr(recap, "first_event", None),
            "last_event": getattr(recap, "last_event", None),
            "presence": pres,
            "tracked_minutes": round(tracked_s / 60.0, 2),
            "away_minutes": round(away_s / 60.0, 2),
            "idle_events": int(getattr(recap, "idle_events", 0) or 0),
            "top_apps": top,
            "focus_switches": int(getattr(recap, "focus_switches", 0) or 0),
            "longest_focus_app": longest,
            "current_stretch": current,
            "last_away": last_away,
            "last_pause": last_pause,
            "minutes_since_pause": minutes_since_pause,
            "terminal_stays": terminal,
            "meetings": {"minutes": round(meeting_s / 60.0, 2), "count": len(in_call)},
            "agent_sessions": agent_body,
            "agent_sessions_reason": agent_reason,
            "shape": shape,
            "signals": _brief_signals(
                now=n,
                current=current,
                pause=last_away,
                shape=shape,
                in_call_n=len(in_call),
                agent_count=None if agent_body is None else int(agent_body.get("count") or 0),
                degraded=bool(pres["degraded"]),
            ),
        }
    )


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



def _agent_brief(
    recap: Any,
    snapshot_ts: str | None,
    now: datetime | None,
    payload: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    n = now or datetime.now(UTC)
    if snapshot_ts:
        try:
            age = (n - parse_ts(snapshot_ts)).total_seconds()
        except ValueError:
            age = None
        if age is not None and age > _STALE_AGENT_S:
            return None, "snapshot_stale"
    rows = getattr(recap, "agent_sessions", []) or []
    count: int | None = None
    if isinstance(payload, dict):
        raw = payload.get("sessions")
        if isinstance(raw, list):
            count = len(raw)
        elif payload.get("count") is not None:
            count = int(payload.get("count") or 0)
    if count is None:
        count = len(rows)
    if snapshot_ts is None and payload is None and count == 0:
        return None, "no_snapshot"
    minutes = None
    total = 0.0
    any_span = False
    for r in rows:
        fs, ls = getattr(r, "first_seen", None), getattr(r, "last_seen", None)
        if fs and ls:
            try:
                total += max(0.0, (parse_ts(ls) - parse_ts(fs)).total_seconds())
                any_span = True
            except ValueError:
                pass
    if any_span:
        minutes = round(total / 60.0, 2)
    reason = "tracked_sources_empty" if count == 0 else None
    return {"count": count, "minutes": minutes}, reason


def _brief_signals(
    *,
    now: datetime,
    current: dict[str, Any] | None,
    pause: dict[str, Any] | None,
    shape: str,
    in_call_n: int,
    agent_count: int | None,
    degraded: bool,
) -> list[str]:
    out: list[str] = []
    hour = now.astimezone().hour
    if hour >= 22 or hour < 5:
        out.append("late_night")
    pause_m = float((pause or {}).get("minutes") or 0)
    if pause is not None and pause_m >= 10 and hour >= 16:
        out.append("post_peak_idle")
    if current is not None and float(current.get("minutes") or 0) >= 40:
        out.append("long_focus")
    if shape == "fragmented":
        out.append("high_switch_rate")
    if in_call_n >= 3:
        out.append("stacked_meetings")
    if agent_count == 0:
        out.append("agent_sources_idle")
    if degraded:
        out.append("presence_degraded")
    return out
