"""Read-only agent session snapshots (Grok active_sessions + light Claude probe)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from roxabi_sense.store import Store

KIND = "agent_session"
SNAPSHOT = "agent_sessions_snapshot"


class AgentSessionsCollector:
    name = "agent_sessions"

    def __init__(
        self,
        grok_path: Path | None = None,
        claude_history: Path | None = None,
    ) -> None:
        home = Path.home()
        self.grok_path = grok_path or home / ".grok" / "active_sessions.json"
        self.claude_history = claude_history or home / ".claude" / "history.jsonl"
        self._last_fingerprint: str | None = None

    def tick(self, store: Store) -> int:
        sessions = self._collect_sessions()
        # Fingerprint stable fields only — not Claude mtime (churns while Claude runs).
        fingerprint = json.dumps(self._stable(sessions), sort_keys=True, separators=(",", ":"))
        if fingerprint == self._last_fingerprint:
            return 0
        self._last_fingerprint = fingerprint
        # Snapshot-only: full list in one row (avoids N+1 per session).
        store.append(SNAPSHOT, {"count": len(sessions), "sessions": sessions})
        return 1

    @staticmethod
    def _stable(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for s in sessions:
            item = {k: v for k, v in s.items() if k != "history_mtime"}
            out.append(item)
        return out

    def _collect_sessions(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        out.extend(self._grok_sessions())
        out.extend(self._claude_hint())
        return out

    def _grok_sessions(self) -> list[dict[str, Any]]:
        if not self.grok_path.is_file():
            return []
        try:
            raw = json.loads(self.grok_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(raw, list):
            return []
        sessions: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            sessions.append(
                {
                    "agent": "grok",
                    "session_id": item.get("session_id"),
                    "pid": item.get("pid"),
                    "cwd": item.get("cwd"),
                    "opened_at": item.get("opened_at"),
                    "state": "open",
                    "source": str(self.grok_path),
                }
            )
        return sessions

    def _claude_hint(self) -> list[dict[str, Any]]:
        """Presence only — Claude has no active_sessions.json equivalent yet."""
        if not self.claude_history.is_file():
            return []
        try:
            mtime = self.claude_history.stat().st_mtime
        except OSError:
            return []
        return [
            {
                "agent": "claude",
                "state": "history_present",
                "history_mtime": mtime,
                "source": str(self.claude_history),
            }
        ]
