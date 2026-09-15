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


_COARSE_DROP_KEYS = frozenset(
    {
        "title",
        "title_raw",
        "frame_name",
        "name",
        "artist",
        "album",
        "url",
        "uri",
        "meeting_label",
        "label",
        "call_id",
        "pid",
        "agent_pid",
    }
)


def redact_coarse(obj: Any, *, extra_drop: frozenset[str] = frozenset()) -> Any:
    """Deep redact for coarse export (titles, media, pids, absolute paths)."""
    drop = _COARSE_DROP_KEYS | extra_drop
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in drop:
                continue
            if k == "cwd" and isinstance(v, str):
                out[k] = _basename_path(v)
                continue
            if k == "path" and isinstance(v, str) and ("/" in v or v.startswith("~")):
                out[k] = _basename_path(v)
                continue
            out[k] = redact_coarse(v, extra_drop=extra_drop)
        return out
    if isinstance(obj, list):
        return [redact_coarse(x, extra_drop=extra_drop) for x in obj]
    if isinstance(obj, tuple):
        return [redact_coarse(x, extra_drop=extra_drop) for x in obj]
    return obj


def _basename_path(path: str) -> str:
    p = path.rstrip("/")
    if not p:
        return path
    return p.rsplit("/", 1)[-1]
