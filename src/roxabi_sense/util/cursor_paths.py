"""Read-only Cursor workspace discovery (no chat/composer bodies).

Scans VS Code–style workspaceStorage under ~/.config/Cursor (or override).
Never opens state.vscdb / chat DBs — only workspace.json metadata + mtimes.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from roxabi_sense.util.time import to_z

# Linux default install root (Cursor is a VS Code fork).
DEFAULT_CURSOR_ROOT = Path.home() / ".config" / "Cursor"


def default_workspace_storage(root: Path | None = None) -> Path:
    base = root if root is not None else DEFAULT_CURSOR_ROOT
    return base / "User" / "workspaceStorage"


def load_cursor_workspaces(
    root: Path | None = None,
    *,
    max_workspaces: int = 20,
    max_age_days: float = 30.0,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Return coarse Cursor workspace facts (agent=cursor), newest first.

    Fields: agent, session_id, cwd, opened_at (mtime ISO-Z), state, source.
    Empty if path missing or unreadable.
    """
    storage = default_workspace_storage(root)
    if not storage.is_dir():
        return []

    now_s = now if now is not None else time.time()
    max_age_s = max(0.0, max_age_days) * 86400.0
    rows: list[tuple[float, dict[str, Any]]] = []

    try:
        children = list(storage.iterdir())
    except OSError:
        return []

    for entry in children:
        if not entry.is_dir():
            continue
        ws_json = entry / "workspace.json"
        if not ws_json.is_file():
            continue
        try:
            st = ws_json.stat()
        except OSError:
            continue
        mtime = float(st.st_mtime)
        if max_age_s > 0 and (now_s - mtime) > max_age_s:
            continue
        cwd = _folder_from_workspace_json(ws_json)
        if not cwd:
            continue
        rows.append(
            (
                mtime,
                {
                    "agent": "cursor",
                    "session_id": entry.name,
                    "cwd": cwd,
                    "pid": None,
                    "opened_at": to_z(datetime.fromtimestamp(mtime, tz=UTC)),
                    "state": "workspace_present",
                    "source": str(ws_json),
                },
            )
        )

    rows.sort(key=lambda r: (-r[0], r[1].get("session_id") or ""))
    limit = max(1, int(max_workspaces))
    return [r[1] for r in rows[:limit]]


def workspace_storage_signature(root: Path | None = None) -> tuple[Any, ...]:
    """Coarse dir signature for collector dedup (name + mtime + size of workspace.json)."""
    storage = default_workspace_storage(root)
    if not storage.is_dir():
        return ()
    sig: list[tuple[str, int, int]] = []
    try:
        for entry in storage.iterdir():
            if not entry.is_dir():
                continue
            ws_json = entry / "workspace.json"
            try:
                st = ws_json.stat()
            except OSError:
                continue
            sig.append((entry.name, st.st_mtime_ns, st.st_size))
    except OSError:
        return ()
    return tuple(sorted(sig))


def _folder_from_workspace_json(path: Path) -> str | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, Mapping):
        return None
    folder = raw.get("folder")
    if isinstance(folder, str) and folder.strip():
        return _uri_to_path(folder.strip())
    # multi-root workspace
    ws = raw.get("workspace")
    if isinstance(ws, str) and ws.strip():
        return _uri_to_path(ws.strip())
    return None


def _uri_to_path(uri: str) -> str | None:
    if uri.startswith("file://"):
        parsed = urlparse(uri)
        path = unquote(parsed.path or "")
        # Windows file:///C:/... → keep as-is; Linux /home/...
        if path.startswith("/") and len(path) >= 3 and path[2] == ":":
            # rare: /C:/Users → C:/Users
            path = path[1:]
        return path or None
    # plain path
    if uri.startswith("/") or (len(uri) > 2 and uri[1] == ":"):
        return uri
    return None
