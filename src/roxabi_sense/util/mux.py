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
_HERDR_ID_KINDS = frozenset({"id", "uuid", "session_id"})

_cache: dict[str, tuple[float, Any]] = {}

__all__ = [
    "herdr_bin",
    "herdr_focused_pane",
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


def _herdr_candidates() -> tuple[Path, Path]:
    return (Path.home() / ".local" / "bin" / "herdr", Path("/usr/local/bin/herdr"))


def herdr_bin() -> str | None:
    candidates = _herdr_candidates()
    for path in candidates:
        if path.is_file():
            return str(path)
    found = shutil.which("herdr")
    if found and Path(found) in candidates:
        return found
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
    return _herdr_state()["agents"]


def herdr_focused_pane() -> dict[str, str] | None:
    """Server-focused Herdr pane (agent or plain shell): ``{pane_id, cwd}`` or None.

    Herdr follows the OS-focused client window; Ghostty titles do not. None when
    Herdr is down, too old for ``api snapshot``, or reports no focused pane.
    """
    return _herdr_state()["focused"]


def _herdr_state() -> dict[str, Any]:
    # One `herdr api snapshot` per cache window serves agents + focus together.
    return _cached("herdr_state", _herdr_state_uncached)


def _herdr_state_uncached() -> dict[str, Any]:
    binary = herdr_bin()
    if not binary:
        return {"agents": [], "focused": None}
    result = _herdr_result(_run([binary, "api", "snapshot"]))
    snap = result.get("snapshot") if isinstance(result, dict) else None
    if isinstance(snap, dict):
        return {"agents": _dict_rows(snap.get("agents")), "focused": _snapshot_focus(snap)}
    # Herdr without `api snapshot`: agent panes only, focus unknown.
    listed = _herdr_result(_run([binary, "agent", "list"]))
    agents = listed.get("agents") if isinstance(listed, dict) else None
    return {"agents": _dict_rows(agents), "focused": None}


def _herdr_result(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("error"):
        return None
    result = data.get("result")
    return result if isinstance(result, dict) else data


def _dict_rows(rows: Any) -> list[dict[str, Any]]:
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _snapshot_focus(snap: dict[str, Any]) -> dict[str, str] | None:
    pane_id = snap.get("focused_pane_id")
    for pane in _dict_rows(snap.get("panes")):
        if pane_id and pane.get("pane_id") == pane_id:
            return {"pane_id": str(pane_id), "cwd": str(pane.get("cwd") or "")}
    return None


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
    text = str(value)
    kind = sess.get("kind")
    if kind == "path" or "/" in text or ".jsonl" in text:
        return Path(text).stem
    if kind in _HERDR_ID_KINDS:
        return text
    return ""


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
        "attached": False,
        "focused": bool(agent.get("focused")),
        "pane_title": str(agent.get("terminal_title_stripped") or ""),
    }
