"""Read-only agent session snapshots (Grok + Claude registries).

Re-reads JSON only when mtime+size signature changes (via SessionRegistry).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from roxabi_sense.store import Store
from roxabi_sense.util.session_registry import SessionRegistry
from roxabi_sense.util.time import parse_ts, utc_now_z

SNAPSHOT = "agent_sessions_snapshot"
_REFRESH_S = 900.0
_LAST_OK = "agent_sessions_last_ok"


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
        last = store.last_by_kind(SNAPSHOT)
        last_ok = store.get_meta(_LAST_OK) or (last.ts if last is not None else None)
        stored_fp = _payload_fp(last.payload if last is not None else None)
        unchanged = fingerprint == (self._last_fingerprint or stored_fp)
        if unchanged and not _snapshot_stale(last_ok):
            self._last_fingerprint = fingerprint
            return 0
        self._last_fingerprint = fingerprint
        if unchanged and last is not None:
            store.set_meta(_LAST_OK, utc_now_z())
            return 0
        store.append(SNAPSHOT, {"count": len(sessions), "sessions": sessions})
        store.set_meta(_LAST_OK, utc_now_z())
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


def _payload_fp(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    raw = payload.get("sessions")
    if not isinstance(raw, list):
        return None
    return AgentSessionsCollector._stable_fp([s for s in raw if isinstance(s, dict)])


def _snapshot_stale(ts: str | None, *, now: datetime | None = None) -> bool:
    if not ts:
        return True
    n = now or datetime.now(UTC)
    try:
        age = (n - parse_ts(ts)).total_seconds()
    except ValueError:
        return True
    return age > _REFRESH_S
