"""Parse the allowlisted structural fields of Tern metadata into opaque identities."""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from typing import Any

from roxabi_sense.collectors.tern.runner import TernUnavailable


def opaque(kind: str, *parts: object) -> str:
    encoded = json.dumps([kind, *parts], sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def endpoint_ref() -> str:
    """Scope daemon identities to the caller's effective endpoint environment."""
    return opaque("endpoint", os.getuid(), os.environ.get("TERN_DAEMON_SOCKET"),
                  os.environ.get("XDG_RUNTIME_DIR"), os.environ.get("TERN_CONFIG_DIR"),
                  os.environ.get("HOME"))


def selected_semantic(pane: dict[str, Any]) -> dict[str, Any]:
    """Context proof excludes independently changing work-state evidence."""
    return {key: pane.get(key) for key in (
        "pane_ref", "host_ref", "cwd", "repo_ref", "session_context_ref"
    )}

def pane_work_semantic(pane: dict[str, Any]) -> dict[str, Any]:
    return {key: pane.get(key) for key in ("pane_ref", "repo_ref", "pane_working")}



def _id(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _rows(value: object) -> list[dict[str, Any]]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _hops(value: object) -> list[tuple[str, ...]]:
    if not isinstance(value, dict):
        return []
    return [tuple(str(row.get(k) or "") for k in ("principal", "method", "from", "host", "user"))
            for row in _rows(value.get("hops"))]


def clients(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only explicitly routed window clients; reject ambiguous incarnations."""
    rows = data.get("clients")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise TernUnavailable("invalid clients")
    out = []
    for row in rows:
        if row.get("kind") != "window":
            continue
        key, conn, since = row.get("window"), _id(row.get("conn")), _id(row.get("since"))
        if not isinstance(key, str) or conn is None or since is None:
            raise TernUnavailable("invalid window client")
        out.append({"window_key": key, "conn": conn, "since": since})
    return out


def selected_pane(data: dict[str, Any]) -> int | None:
    sessions = [s for s in _rows(data.get("sessions")) if s.get("shown") is True]
    if len(sessions) != 1:
        return None
    tabs = [t for t in _rows(sessions[0].get("tabs")) if t.get("shown") is True]
    if len(tabs) != 1:
        return None
    blocks = [b for b in _rows(tabs[0].get("blocks"))
              if b.get("focused") is True and b.get("live") is True and b.get("exited") is None]
    return _id(blocks[0].get("id")) if len(blocks) == 1 else None


def observation_shape(data: dict[str, Any]) -> str:
    """Compare allowlisted pane facts across a routed inspection, without process output."""
    rows = []
    for row in _rows(data.get("panes")):
        rows.append({
            "pane": row.get("pane"), "identity": row.get("identity"),
            "cwd": row.get("cwd"), "session": row.get("omp_session_file"),
            "working": row.get("working"), "exited": row.get("exited"),
        })
    rows.sort(key=lambda row: str(row["pane"]))
    return opaque("observation", selected_pane(data), rows)


def parse_window(
    data: dict[str, Any], route: dict[str, Any], endpoint: str,
    process: Callable[[str, int], dict[str, Any]], *, include_pane_id: bool = False,
) -> dict[str, Any]:
    """Project panes without retaining terminal titles, command lines or PIDs."""
    key = route["window_key"]
    matches = [c for c in clients(data) if c["window_key"] == key]
    if len(matches) != 1 or matches[0] != route:
        raise TernUnavailable("window client changed")
    selected = selected_pane(data)
    panes = []
    seen: set[int] = set()
    for row in _rows(data.get("panes")):
        pane = _id(row.get("pane"))
        if pane is None or pane in seen:
            raise TernUnavailable("ambiguous pane")
        seen.add(pane)
        if row.get("exited") is not None:
            continue
        hops = _hops(row.get("identity"))
        host_ref = opaque("host", endpoint, hops)
        pane_ref = opaque("pane", endpoint, hops, pane)
        cwd = row.get("cwd") if isinstance(row.get("cwd"), str) else None
        session = row.get("omp_session_file")
        session_ref = (
            opaque("session-context", endpoint, hops, session)
            if isinstance(session, str) and session else None
        )
        repo_ref = opaque("repo", endpoint, hops, cwd) if cwd else None
        working = row.get("working") if isinstance(row.get("working"), bool) else None
        foreground: bool | None = None
        if pane == selected or working is True:
            try:
                result = process(key, pane)
                fg = result.get("foreground")
                if (_id(result.get("pane")) == pane and isinstance(fg, dict)
                        and isinstance(fg.get("name"), str)):
                    foreground = fg["name"] == "omp"
            except TernUnavailable:
                pass
        panes.append({"pane_ref": pane_ref, "host_ref": host_ref, "cwd": cwd,
                      "repo_ref": repo_ref, "session_context_ref": session_ref,
                      "pane_working": working, "omp_foreground": foreground,
                      "omp_working": False if working is False else (
                          working if foreground is True else None
                      ),
                      "selected": pane == selected,
                      **({"_pane_id": pane} if include_pane_id else {})})
    panes.sort(key=lambda pane: pane["pane_ref"])
    if selected is not None and sum(p["selected"] for p in panes) != 1:
        selected = None
    return {**route, "shown_pane": next((p["pane_ref"] for p in panes if p["selected"]), None)
            if selected is not None else None, "panes": panes}
