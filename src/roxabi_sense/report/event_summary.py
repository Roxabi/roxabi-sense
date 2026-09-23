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
    if kind == "herdr_snapshot":
        panes = payload.get("panes") or []
        bits = [
            f"{p.get('agent')}@{p.get('cwd')}"
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
        "pane_title",
        "terminal_title",
        "terminal_title_stripped",
    }
)


CARE_BRIEF_MAX_BYTES = 8192


def _is_title_row(obj: list[Any] | tuple[Any, ...]) -> bool:
    """Positional top_titles row: (title, seconds, app)."""
    return (
        len(obj) == 3
        and isinstance(obj[0], str)
        and isinstance(obj[1], (int, float))
        and not isinstance(obj[1], bool)
        and isinstance(obj[2], str)
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
    if isinstance(obj, (list, tuple)):
        if _is_title_row(obj):
            return ["", obj[1], obj[2]]
        return [redact_coarse(x, extra_drop=extra_drop) for x in obj]
    return obj


def cap_json_bytes(
    obj: dict[str, Any],
    *,
    max_bytes: int = CARE_BRIEF_MAX_BYTES,
    # Repo/session rows go first: heartbeat nudges key off `signals` (e.g. late_night).
    trim_keys: tuple[str, ...] = ("agent_repos", "focus_repos", "top_apps", "signals"),
) -> dict[str, Any]:
    """Fail closed: drop extra list rows until serialized size fits."""

    def nbytes(o: dict[str, Any]) -> int:
        return len(json.dumps(o, ensure_ascii=False).encode())

    if nbytes(obj) <= max_bytes:
        return obj
    out = dict(obj)
    out["truncated"] = True
    for key in trim_keys:
        items = out.get(key)
        if not isinstance(items, list):
            continue
        while items and nbytes(out) > max_bytes:
            items.pop()
            out[key] = items
    for key in ("terminal_stays", "longest_focus_app", "meetings", "agent_sessions"):
        if nbytes(out) <= max_bytes:
            return out
        out.pop(key, None)
    if nbytes(out) <= max_bytes:
        return out
    stub = {"truncated": True, "day": obj.get("day"), "db_exists": obj.get("db_exists")}
    return stub if nbytes(stub) <= max_bytes else {"truncated": True}


def _basename_path(path: str) -> str:
    p = path.rstrip("/")
    if not p:
        return path
    return p.rsplit("/", 1)[-1]
