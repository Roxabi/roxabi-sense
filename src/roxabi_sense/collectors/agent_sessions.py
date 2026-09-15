"""Read-only agent session snapshots (Grok + Claude files + Herdr live list).

Re-reads JSON only when mtime+size signature changes (via SessionRegistry).
Herdr rows come from an injectable callable (default: util.mux.herdr_session_rows).
"""

from __future__ import annotations

from collections.abc import Callable
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
