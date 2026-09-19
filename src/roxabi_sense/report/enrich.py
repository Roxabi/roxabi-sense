"""Side aggregates for day recap (agents, processes, media)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from roxabi_sense.store import Event


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
