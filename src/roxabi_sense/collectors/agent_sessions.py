"""Read-only agent session snapshots (Grok + Claude registries).

Re-reads JSON only when mtime+size signature changes (via SessionRegistry).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from roxabi_sense.store import Store
from roxabi_sense.util.session_registry import SessionRegistry

SNAPSHOT = "agent_sessions_snapshot"


class AgentSessionsCollector:
    name = "agent_sessions"

    def __init__(
        self,
        grok_path: Path | None = None,
        claude_history: Path | None = None,
        *,
        claude_sessions_dir: Path | None = None,
        registry: SessionRegistry | None = None,
    ) -> None:
        home = Path.home()
        # claude_history kept for API compat; real Claude IDs come from sessions/
        self.claude_history = claude_history or home / ".claude" / "history.jsonl"
        if registry is not None:
            self._reg = registry
        else:
            self._reg = SessionRegistry(
                grok_path=grok_path or home / ".grok" / "active_sessions.json",
                claude_dir=claude_sessions_dir or home / ".claude" / "sessions",
            )
        self._last_fingerprint: str | None = None

    def tick(self, store: Store) -> int:
        sessions = self._reg.load_all()
        fingerprint = self._stable_fp(sessions)
        if fingerprint == self._last_fingerprint:
            return 0
        self._last_fingerprint = fingerprint
        store.append(SNAPSHOT, {"count": len(sessions), "sessions": sessions})
        return 1

    @staticmethod
    def _stable_fp(sessions: list[dict[str, Any]]) -> str:
        import json

        rows = []
        for s in sessions:
            rows.append(
                {
                    "agent": s.get("agent"),
                    "session_id": s.get("session_id"),
                    "pid": s.get("pid"),
                    "cwd": s.get("cwd"),
                }
            )
        return json.dumps(rows, sort_keys=True, separators=(",", ":"))
