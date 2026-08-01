"""Opt-in read-only Cursor workspace presence (ADR-001 agent source).

Disabled by default. Never writes into Cursor dirs; never opens chat DBs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from roxabi_sense.store import Store
from roxabi_sense.util.cursor_paths import (
    DEFAULT_CURSOR_ROOT,
    load_cursor_workspaces,
    workspace_storage_signature,
)

SNAPSHOT = "agent_sessions_snapshot"


class CursorSessionsCollector:
    """Emit agent_sessions_snapshot rows with agent=cursor when enabled."""

    name = "cursor_sessions"

    def __init__(
        self,
        *,
        root: Path | None = None,
        max_workspaces: int = 20,
        max_age_days: float = 30.0,
        enabled: bool = True,
    ) -> None:
        self.root = root if root is not None else DEFAULT_CURSOR_ROOT
        self.max_workspaces = max_workspaces
        self.max_age_days = max_age_days
        self.enabled = enabled
        self._last_sig: tuple[Any, ...] | None = None

    def tick(self, store: Store) -> int:
        if not self.enabled:
            return 0
        sig = workspace_storage_signature(self.root)
        if sig == self._last_sig:
            return 0
        self._last_sig = sig
        sessions = load_cursor_workspaces(
            self.root,
            max_workspaces=self.max_workspaces,
            max_age_days=self.max_age_days,
        )
        # Always write when sig changes (including empty → documents absence).
        store.append(
            SNAPSHOT,
            {
                "count": len(sessions),
                "sessions": sessions,
                "source": "cursor",
            },
        )
        return 1

    def fingerprint(self) -> str:
        sessions = load_cursor_workspaces(
            self.root,
            max_workspaces=self.max_workspaces,
            max_age_days=self.max_age_days,
        )
        return json.dumps(
            [
                {
                    "session_id": s.get("session_id"),
                    "cwd": s.get("cwd"),
                    "opened_at": s.get("opened_at"),
                }
                for s in sessions
            ],
            sort_keys=True,
            separators=(",", ":"),
        )
