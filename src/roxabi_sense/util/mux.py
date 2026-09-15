"""Shared tmux + herdr listing (short in-process cache, no title persistence)."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_CACHE_TTL_S = 2.0
_TMUX_CANDIDATES = ("/usr/bin/tmux", "/usr/local/bin/tmux")
_AGENT_COMMS = frozenset({"grok", "claude", "omp"})

_cache: dict[str, tuple[float, Any]] = {}

__all__ = [
    "herdr_bin",
    "herdr_session_id",
    "herdr_session_rows",
    "list_herdr_agents",
    "list_mux_agent_panes",
    "list_tmux_agent_panes",
    "list_tmux_panes",
    "reset_mux_cache",
    "tmux_bin",
]


def reset_mux_cache() -> None:
    _cache.clear()


def _cached(key: str, fn: Callable[[], Any]) -> Any:
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < _CACHE_TTL_S:
        return hit[1]
    value = fn()
    _cache[key] = (now, value)
    return value


def tmux_bin() -> str | None:
    found = shutil.which("tmux")
    if found:
        return found
    for candidate in _TMUX_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def herdr_bin() -> str | None:
    found = shutil.which("herdr")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "herdr"
    if local.is_file():
        return str(local)
    return None


def _run(argv: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def list_tmux_panes() -> list[dict[str, Any]]:
    return _cached("list_tmux_panes", _list_tmux_panes_uncached)


def _list_tmux_panes_uncached() -> list[dict[str, Any]]:
    binary = tmux_bin()
    if not binary:
        return []
    fmt = (
        "#{session_name}\t#{window_index}\t#{window_name}\t"
        "#{pane_current_command}\t#{pane_current_path}\t#{session_attached}\t"
        "#{pane_pid}\t#{pane_id}\t#{pane_title}"
    )
    raw = _run([binary, "list-panes", "-a", "-F", fmt])
    if raw is None:
        return []
    panes: list[dict[str, Any]] = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        session, win_i, win_name, cmd, path, attached, pid_s, pane_id = parts[:8]
        pane_title = parts[8] if len(parts) > 8 else ""
        if len(parts) > 9:
            pane_title = "\t".join(parts[8:])
        panes.append(
            {
                "mux": "tmux",
                "session": session,
                "window": win_i,
                "window_name": win_name,
                "command": cmd,
                "path": path,
                "attached": attached == "1",
                "pane_title": pane_title,
                "pane_pid": int(pid_s) if pid_s.isdigit() else None,
                "pane_id": pane_id,
            }
        )
    return panes


def list_herdr_agents() -> list[dict[str, Any]]:
    return _cached("list_herdr_agents", _list_herdr_agents_uncached)


def _list_herdr_agents_uncached() -> list[dict[str, Any]]:
    binary = herdr_bin()
    if not binary:
        return []
    raw = _run([binary, "agent", "list"])
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict) or data.get("error"):
        return []
    result = data.get("result")
    if isinstance(result, dict) and "agents" in result:
        agents = result.get("agents")
    else:
        agents = data.get("agents")
    if not isinstance(agents, list):
        return []
    return [a for a in agents if isinstance(a, dict)]


def list_tmux_agent_panes() -> list[dict[str, Any]]:
    return _cached("list_tmux_agent_panes", _list_tmux_agent_panes_uncached)


def _list_tmux_agent_panes_uncached() -> list[dict[str, Any]]:
    return [
        _tmux_agent_row(p)
        for p in list_tmux_panes()
        if p.get("command") in _AGENT_COMMS
    ]


def list_mux_agent_panes() -> list[dict[str, Any]]:
    return _cached("list_mux_agent_panes", _list_mux_agent_panes_uncached)


def _list_mux_agent_panes_uncached() -> list[dict[str, Any]]:
    rows = _list_tmux_agent_panes_uncached()
    rows.extend(_herdr_agent_row(a) for a in list_herdr_agents())
    return rows


def herdr_session_id(agent: dict[str, Any]) -> str:
    sess = agent.get("agent_session")
    if not isinstance(sess, dict):
        return ""
    value = sess.get("value")
    if value is None or value == "":
        return ""
    if sess.get("kind") == "path":
        return Path(str(value)).stem
    return str(value)


def herdr_session_rows(
    agents: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    src = agents if agents is not None else list_herdr_agents()
    rows: list[dict[str, Any]] = []
    for agent in src:
        session_id = herdr_session_id(agent)
        cwd = str(agent.get("cwd") or "")
        if not cwd and not session_id:
            continue
        rows.append(
            {
                "agent": str(agent.get("agent") or ""),
                "session_id": session_id,
                "cwd": cwd,
                "state": str(agent.get("agent_status") or ""),
                "source": "herdr",
            }
        )
    return rows


def _tmux_agent_row(pane: dict[str, Any]) -> dict[str, Any]:
    return {
        "mux": "tmux",
        "pane_pid": pane.get("pane_pid"),
        "pane_id": str(pane.get("pane_id") or ""),
        "command": str(pane.get("command") or ""),
        "path": str(pane.get("path") or ""),
        "attached": bool(pane.get("attached")),
        "pane_title": str(pane.get("pane_title") or ""),
    }


def _herdr_agent_row(agent: dict[str, Any]) -> dict[str, Any]:
    return {
        "mux": "herdr",
        "pane_pid": None,
        "pane_id": str(agent.get("pane_id") or ""),
        "command": str(agent.get("agent") or ""),
        "path": str(agent.get("cwd") or ""),
        "attached": bool(agent.get("focused")),
        "pane_title": str(agent.get("terminal_title_stripped") or ""),
    }
