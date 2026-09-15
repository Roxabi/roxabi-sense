"""Read-only agent session snapshots (Grok + Claude files + Herdr live list).

Re-reads JSON only when mtime+size signature changes (via SessionRegistry).
Herdr rows come from an injectable callable (default: util.mux.herdr_session_rows).
"""

from __future__ import annotations

from collections.abc import Callable
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
        herdr_sessions: Callable[[], list[dict[str, Any]]] | None = None,
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
        self._herdr_sessions = herdr_sessions
        self._last_fingerprint: str | None = None

    def tick(self, store: Store) -> int:
        sessions = _merge_herdr(self._reg.load_all(), self._live_herdr())
        fingerprint = self._stable_fp(sessions)
        if fingerprint == self._last_fingerprint:
            return 0
        self._last_fingerprint = fingerprint
        store.append(SNAPSHOT, {"count": len(sessions), "sessions": sessions})
        return 1

    def _live_herdr(self) -> list[dict[str, Any]]:
        fn = self._herdr_sessions
        if fn is None:
            from roxabi_sense.util.mux import herdr_session_rows

            fn = herdr_session_rows
        rows = fn()
        return rows if isinstance(rows, list) else []

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
                    "state": s.get("state"),
                }
            )
        return json.dumps(rows, sort_keys=True, separators=(",", ":"))


def _merge_herdr(
    file_rows: list[dict[str, Any]],
    herdr_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep file-registry rows; overlay missing cwd/state; append unique herdr ids."""
    sessions = [dict(s) for s in file_rows]
    index: dict[str, int] = {}
    for i, s in enumerate(sessions):
        sid = s.get("session_id")
        if isinstance(sid, str) and sid:
            index[sid] = i
    for raw in herdr_rows:
        if not isinstance(raw, dict):
            continue
        sid = raw.get("session_id")
        if isinstance(sid, str) and sid in index:
            existing = sessions[index[sid]]
            if not existing.get("cwd") and raw.get("cwd"):
                existing["cwd"] = raw["cwd"]
            if not existing.get("state") and raw.get("state"):
                existing["state"] = raw["state"]
            continue
        if not sid and not raw.get("cwd"):
            continue
        sessions.append(
            {
                "agent": raw.get("agent"),
                "session_id": sid,
                "cwd": raw.get("cwd"),
                "state": raw.get("state"),
                "source": raw.get("source", "herdr"),
                "pid": raw.get("pid"),
            }
        )
        if isinstance(sid, str) and sid:
            index[sid] = len(sessions) - 1
    return sessions
