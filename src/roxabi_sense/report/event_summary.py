"""Shared short-form event lines (CLI day / future MCP timeline)."""

from __future__ import annotations

import json
from typing import Any


def summarize_event(kind: str, payload: dict[str, Any]) -> str:
    """One-line human summary for a raw store event (not day recap)."""
    if kind == "agent_sessions_snapshot":
        sessions = payload.get("sessions") or []
        bits = []
        for s in sessions[:6]:
            if not isinstance(s, dict):
                continue
            bits.append(f"{s.get('agent')}:{s.get('cwd') or s.get('state')}")
        return f"n={payload.get('count')} " + "; ".join(bits)
    if kind == "tmux_snapshot":
        panes = payload.get("panes") or []
        bits = [
            f"{p.get('session')}:{p.get('command')}@{p.get('path')}"
            for p in panes[:6]
            if isinstance(p, dict)
        ]
        return f"n={payload.get('count')} " + "; ".join(bits)
    if kind == "process_snapshot":
        procs = payload.get("processes") or {}
        running = [k for k, v in procs.items() if isinstance(v, dict) and v.get("running")]
        return "running=" + ",".join(running)
    if kind == "idle":
        return (
            f"idle={payload.get('idle')} source={payload.get('source')} "
            f"since={payload.get('idle_since')}"
        )
    if kind == "media":
        player = payload.get("player")
        status = payload.get("status")
        artist = payload.get("artist")
        title = payload.get("title")
        return f"{player} {status}: {artist} — {title}"
    if kind == "media_snapshot":
        players = payload.get("players") or []
        return f"players={len(players)}"
    if kind == "agent_session":
        return f"{payload.get('agent')} {payload.get('cwd') or payload.get('state')}"
    if kind == "focus":
        agent = payload.get("agent") or {}
        agent_bit = ""
        if isinstance(agent, dict) and agent.get("agent"):
            agent_bit = f" → {agent.get('agent')}"
            if agent.get("cwd"):
                agent_bit += f"@{agent.get('cwd')}"
        return f"{payload.get('app')}: {payload.get('title')}{agent_bit}"
    if kind == "desktop_snapshot":
        focus = payload.get("focus") or {}
        n = len(payload.get("windows") or [])
        return f"n={n} focus={focus.get('app')}: {focus.get('title')}"
    return json.dumps(payload, ensure_ascii=False)[:100]
