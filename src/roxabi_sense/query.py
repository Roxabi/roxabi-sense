"""Transport-agnostic read API (MCP stdio, future HTTP, Cloudflare ports).

JSON-shaped methods only — no print, no MCP SDK, no collectors.
Surfaces map these dicts to tools / HTTP routes without reimplementing queries.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from roxabi_sense.config import SenseConfig
from roxabi_sense.report import (
    compile_day_recap,
    load_status_snapshot,
    summarize_event,
)
from roxabi_sense.store import DEFAULT_DAY_LIMIT, Store, clamp_event_limit

DetailLevel = Literal["coarse", "full"]

# Keys stripped (or path-redacted) under coarse detail — ADR-002 MCP redaction.
_COARSE_DROP_KEYS = frozenset(
    {
        "title",
        "title_raw",
        "frame_name",
        "name",
        "artist",
        "album",
        "url",
        "uri",
        "meeting_label",  # often derived from window titles
    }
)


@dataclass(frozen=True)
class SenseQuery:
    """Read-only query surface over local store (or future remote backend)."""

    db_path: Path
    offline_threshold_s: float
    idle_threshold_s: float
    detail: DetailLevel = "coarse"

    @classmethod
    def from_config(cls, cfg: SenseConfig) -> SenseQuery:
        return cls(
            db_path=cfg.db_path,
            offline_threshold_s=cfg.offline_threshold_s,
            idle_threshold_s=cfg.idle_threshold_s,
            detail=cfg.mcp_detail,
        )

    def sense_status(self) -> dict[str, Any]:
        """Daemon / presence health (tool: sense_status)."""
        snap = load_status_snapshot(
            self.db_path,
            offline_threshold_s=self.offline_threshold_s,
            idle_threshold_s=self.idle_threshold_s,
        )
        body = snap.to_dict()
        if self.detail == "coarse":
            body = _redact_obj(body)
            # Keep presence + meta; drop raw event payloads in coarse mode
            body.pop("last_event", None)
            body["latest_by_kind"] = {
                k: {"ts": v.get("ts"), "kind": v.get("kind")}
                for k, v in (body.get("latest_by_kind") or {}).items()
                if isinstance(v, dict)
            }
        return body

    def active_now(self) -> dict[str, Any]:
        """Current presence + latest focus + open agent sessions (tool: active_now)."""
        snap = load_status_snapshot(
            self.db_path,
            offline_threshold_s=self.offline_threshold_s,
            idle_threshold_s=self.idle_threshold_s,
        )
        focus = None
        sessions: list[dict[str, Any]] = []
        focus_ev = snap.latest_by_kind.get("focus")
        if focus_ev is not None:
            focus = {
                "ts": focus_ev.ts,
                "app": focus_ev.payload.get("app"),
                "title": focus_ev.payload.get("title"),
                "agent": focus_ev.payload.get("agent"),
            }
        sess_ev = snap.latest_by_kind.get("agent_sessions_snapshot")
        if sess_ev is not None:
            raw = sess_ev.payload.get("sessions") or []
            if isinstance(raw, list):
                sessions = [s for s in raw if isinstance(s, dict)]
        out: dict[str, Any] = {
            "presence": snap.presence.to_dict(),
            "last_tick": snap.last_tick,
            "machine": snap.machine,
            "db_exists": snap.db_exists,
            "focus": focus,
            "agent_sessions": sessions,
        }
        return _redact_obj(out) if self.detail == "coarse" else out

    def what_was_i_doing(
        self,
        day: str | None = None,
        *,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Timeline slice for a local calendar day (tool: what_was_i_doing)."""
        lim = clamp_event_limit(limit, default=DEFAULT_DAY_LIMIT)
        if not self.db_path.is_file():
            return {
                "db_exists": False,
                "day": day,
                "start": None,
                "end": None,
                "events": [],
                "error": "db_missing",
            }
        try:
            with Store(self.db_path) as store:
                start, end = store.day_bounds(day)
                events = store.events_for_day(day, limit=lim)
        except ValueError as exc:
            return {
                "db_exists": True,
                "day": day,
                "start": None,
                "end": None,
                "events": [],
                "error": "invalid_day",
                "message": str(exc),
            }
        rows: list[dict[str, Any]] = []
        for e in events:
            payload = e.payload if self.detail == "full" else _redact_obj(e.payload)
            row: dict[str, Any] = {
                "ts": e.ts,
                "kind": e.kind,
                "summary": summarize_event(e.kind, payload if isinstance(payload, dict) else {}),
            }
            if self.detail == "full":
                row["payload"] = e.payload
            rows.append(row)
        return {
            "db_exists": True,
            "day": day,
            "start": start,
            "end": end,
            "limit": lim,
            "count": len(rows),
            "events": rows,
        }

    def agent_sessions(self, day: str | None = None) -> dict[str, Any]:
        """Agent sessions from day recap (tool: agent_sessions)."""
        if not self.db_path.is_file():
            return {"db_exists": False, "day": day, "sessions": [], "error": "db_missing"}
        with Store(self.db_path) as store:
            recap = compile_day_recap(store, day)
        sessions = [
            {
                "agent": s.agent,
                "session_id": s.session_id,
                "cwd": s.cwd,
                "first_seen": s.first_seen,
                "last_seen": s.last_seen,
                "state": s.state,
            }
            for s in recap.agent_sessions
        ]
        body: dict[str, Any] = {
            "db_exists": True,
            "day": recap.day,
            "start": recap.start,
            "end": recap.end,
            "sessions": sessions,
        }
        return _redact_obj(body) if self.detail == "coarse" else body

    def day_recap(self, day: str | None = None) -> dict[str, Any]:
        """Compiled day recap JSON (bonus tool; same product as CLI recap)."""
        if not self.db_path.is_file():
            return {"db_exists": False, "day": day, "error": "db_missing"}
        try:
            with Store(self.db_path) as store:
                recap = compile_day_recap(store, day)
        except ValueError as exc:
            return {
                "db_exists": True,
                "day": day,
                "error": "invalid_day",
                "message": str(exc),
            }
        body = recap.to_dict()
        body["db_exists"] = True
        if self.detail == "full":
            return body
        # Coarse: key redaction + drop positional title/media firehoses (asdict tuples)
        body = _redact_obj(body)
        body["top_titles"] = []
        body["media"] = []
        return body


def _redact_obj(obj: Any) -> Any:
    """Deep redact for coarse export (titles, media, absolute paths)."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in _COARSE_DROP_KEYS:
                continue
            if k == "cwd" and isinstance(v, str):
                out[k] = _basename_path(v)
                continue
            if k == "path" and isinstance(v, str) and ("/" in v or v.startswith("~")):
                out[k] = _basename_path(v)
                continue
            out[k] = _redact_obj(v)
        return out
    if isinstance(obj, list):
        return [_redact_obj(x) for x in obj]
    if isinstance(obj, tuple):
        return [_redact_obj(x) for x in obj]
    return obj


def _basename_path(path: str) -> str:
    p = path.rstrip("/")
    if not p:
        return path
    return p.rsplit("/", 1)[-1]
