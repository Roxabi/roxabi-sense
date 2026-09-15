"""Side aggregates for day recap (agents, processes, media)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from roxabi_sense.report.event_summary import cap_json_bytes
from roxabi_sense.store import Event
from roxabi_sense.util.time import parse_ts


@dataclass(frozen=True)
class AgentSessionRow:
    agent: str
    session_id: str
    cwd: str | None
    first_seen: str
    last_seen: str
    state: str | None = None


@dataclass(frozen=True)
class MediaTrack:
    player: str
    artist: str | None
    title: str | None
    first_seen: str


def agent_sessions(events: list[Event]) -> list[AgentSessionRow]:
    first: dict[str, dict[str, Any]] = {}
    last_ts: dict[str, str] = {}
    for e in events:
        if e.kind != "agent_sessions_snapshot":
            continue
        sessions = e.payload.get("sessions") or []
        if not isinstance(sessions, list):
            continue
        for s in sessions:
            if not isinstance(s, dict):
                continue
            sid = str(s.get("session_id") or "")
            if not sid:
                agent = str(s.get("agent") or "?")
                state = str(s.get("state") or "")
                sid = f"{agent}:{state or s.get('cwd') or 'anon'}"
            if sid not in first:
                first[sid] = {
                    "agent": str(s.get("agent") or "?"),
                    "session_id": str(s.get("session_id") or sid),
                    "cwd": s.get("cwd"),
                    "first_seen": e.ts,
                    "state": s.get("state"),
                }
            elif s.get("cwd") and not first[sid].get("cwd"):
                first[sid]["cwd"] = s.get("cwd")
            last_ts[sid] = e.ts

    rows = [
        AgentSessionRow(
            agent=str(meta["agent"]),
            session_id=str(meta["session_id"]),
            cwd=str(meta["cwd"]) if meta.get("cwd") else None,
            first_seen=str(meta["first_seen"]),
            last_seen=last_ts.get(sid, str(meta["first_seen"])),
            state=str(meta["state"]) if meta.get("state") else None,
        )
        for sid, meta in first.items()
    ]
    rows.sort(key=lambda r: (r.first_seen, r.agent, r.session_id))
    return rows


def processes_seen(events: list[Event]) -> list[str]:
    seen: set[str] = set()
    for e in events:
        if e.kind != "process_snapshot":
            continue
        procs = e.payload.get("processes") or {}
        if not isinstance(procs, dict):
            continue
        for name, info in procs.items():
            if isinstance(info, dict) and info.get("running"):
                seen.add(str(name))
    return sorted(seen)


def media_tracks(events: list[Event]) -> list[MediaTrack]:
    out: list[MediaTrack] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for e in events:
        players: list[dict[str, Any]] = []
        if e.kind == "media":
            players = [e.payload]
        elif e.kind == "media_snapshot":
            raw = e.payload.get("players") or []
            if isinstance(raw, list):
                players = [p for p in raw if isinstance(p, dict)]
        else:
            continue
        for p in players:
            status = str(p.get("status") or "").lower()
            if status and status not in {"playing", "paused"}:
                continue
            player = str(p.get("player") or "?")
            artist = p.get("artist")
            title = p.get("title")
            key = (player, str(artist) if artist else None, str(title) if title else None)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                MediaTrack(
                    player=player,
                    artist=str(artist) if artist else None,
                    title=str(title) if title else None,
                    first_seen=e.ts,
                )
            )
    return out


_BRIEF_APPS = 8
_STALE_AGENT_S = 1800.0
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
    agent_body, agent_reason = _agent_brief(recap, agent_snapshot_ts, now, agent_payload)

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
            "terminal_stays": terminal,
            "meetings": {"minutes": round(meeting_s / 60.0, 2), "count": len(in_call)},
            "agent_sessions": agent_body,
            "agent_sessions_reason": agent_reason,
            "shape": shape,
            "signals": _brief_signals(
                last_event=getattr(recap, "last_event", None),
                longest=longest,
                shape=shape,
                in_call_n=len(in_call),
                agent_count=None if agent_body is None else int(agent_body.get("count") or 0),
                degraded=bool(pres["degraded"]),
                away_minutes=away_s / 60.0,
            ),
        }
    )


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
    last_event: str | None,
    longest: dict[str, Any] | None,
    shape: str,
    in_call_n: int,
    agent_count: int | None,
    degraded: bool,
    away_minutes: float,
) -> list[str]:
    out: list[str] = []
    if last_event:
        try:
            hour = parse_ts(last_event).astimezone().hour
            if hour >= 22 or hour < 5:
                out.append("late_night")
            if hour >= 16 and away_minutes >= 45:
                out.append("post_peak_idle")
        except ValueError:
            pass
    if longest is not None and float(longest.get("minutes") or 0) >= 40:
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
