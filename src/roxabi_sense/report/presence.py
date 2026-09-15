"""Shared presence derivation (ADR-002) — not a collector, not CLI-private."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from roxabi_sense.store import Store
from roxabi_sense.util.time import parse_ts

# Daemon offline if last_tick older than this (seconds).
DEFAULT_OFFLINE_THRESHOLD_S = 120.0
DEFAULT_IDLE_THRESHOLD_S = 300.0


@dataclass(frozen=True)
class Presence:
    state: str  # active | idle | offline
    authority: str
    confidence: str  # high | low | inferred
    degraded: bool
    last_tick_age_s: float | None
    idle_watch: str  # ready | dead | restarting | n/a
    idle_since: str | None
    threshold_s: float
    session_bound: bool
    degraded_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def age_seconds(ts: str | None, *, now: datetime | None = None) -> float | None:
    if not ts:
        return None
    n = now or datetime.now(UTC)
    if n.tzinfo is None:
        n = n.replace(tzinfo=UTC)
    try:
        return max(0.0, (n - parse_ts(ts)).total_seconds())
    except ValueError:
        return None


def session_bound_now() -> bool:
    """True when a Wayland (or X11) display is visible to this process."""
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))


def parse_session_bound_meta(raw: str | None) -> bool | None:
    """Parse daemon-written session_bound meta. None → caller uses process env."""
    if raw is None or raw == "":
        return None
    v = raw.strip().lower()
    if v in {"1", "true", "yes"}:
        return True
    if v in {"0", "false", "no"}:
        return False
    return None


def write_session_bound_meta(store: Store) -> None:
    """Persist graphical-session visibility from the daemon process env."""
    store.set_meta("session_bound", "1" if session_bound_now() else "0")


def _degraded_reason(
    *, bound: bool, watch: str, source: str = "", idle_flag: Any = None
) -> str | None:
    if watch == "dead":
        return "idle_watch_dead"
    if watch == "restarting":
        return "idle_watch_restarting"
    if idle_flag is True and source == "logind":
        return "logind_idle"
    if not bound:
        return "no_display"
    return None


def derive_presence(
    *,
    last_tick: str | None,
    idle_watch: str = "n/a",
    last_idle_payload: dict[str, Any] | None = None,
    now: datetime | None = None,
    offline_threshold_s: float = DEFAULT_OFFLINE_THRESHOLD_S,
    idle_threshold_s: float = DEFAULT_IDLE_THRESHOLD_S,
    session_bound: bool | None = None,
) -> Presence:
    """
    Pure presence SM from meta + latest idle fact.

    offline = daemon liveness stale (distinct from user idle).
    idle = authoritative input idle true.
    active = not offline and not input-idle.
    """
    n = now or datetime.now(UTC)
    if n.tzinfo is None:
        n = n.replace(tzinfo=UTC)
    bound = session_bound if session_bound is not None else session_bound_now()
    tick_age = age_seconds(last_tick, now=n)
    watch = idle_watch or "n/a"

    if tick_age is None or tick_age > offline_threshold_s:
        degraded = watch in {"dead", "restarting"} or not bound
        return Presence(
            state="offline",
            authority="daemon",
            confidence="high" if tick_age is not None else "inferred",
            degraded=degraded,
            last_tick_age_s=tick_age,
            idle_watch=watch,
            idle_since=None,
            threshold_s=idle_threshold_s,
            session_bound=bound,
            degraded_reason=_degraded_reason(bound=bound, watch=watch) if degraded else None,
        )

    payload = last_idle_payload if isinstance(last_idle_payload, dict) else {}
    idle_flag = payload.get("idle")
    source = str(payload.get("source") or "unknown")
    idle_since = payload.get("idle_since")
    idle_since_s = str(idle_since) if idle_since else None

    degraded = watch in {"dead", "restarting"} or not bound
    if idle_flag is True:
        conf = "high" if source.startswith("wayland") and watch == "ready" else "low"
        if source == "logind":
            conf = "low"
            degraded = True
        if watch == "dead":
            conf = "low"
            degraded = True
        return Presence(
            state="idle",
            authority=source,
            confidence=conf,
            degraded=degraded,
            last_tick_age_s=tick_age,
            idle_watch=watch,
            idle_since=idle_since_s,
            threshold_s=float(payload.get("threshold_s") or idle_threshold_s),
            session_bound=bound,
            degraded_reason=_degraded_reason(
                bound=bound, watch=watch, source=source, idle_flag=idle_flag
            )
            if degraded
            else None,
        )

    # Not input-idle. Watch death must not look like confident active.
    if watch == "dead":
        return Presence(
            state="active",
            authority="degraded",
            confidence="low",
            degraded=True,
            last_tick_age_s=tick_age,
            idle_watch=watch,
            idle_since=None,
            threshold_s=idle_threshold_s,
            session_bound=bound,
            degraded_reason="idle_watch_dead",
        )

    conf = "high" if watch in {"ready", "n/a"} and bound else "low"
    degraded = degraded or not bound
    return Presence(
        state="active",
        authority="input" if watch == "ready" else source if source != "unknown" else "input",
        confidence=conf,
        degraded=degraded,
        last_tick_age_s=tick_age,
        idle_watch=watch,
        idle_since=None,
        threshold_s=idle_threshold_s,
        session_bound=bound,
        degraded_reason=_degraded_reason(bound=bound, watch=watch, source=source)
        if degraded
        else None,
    )


def presence_from_store(
    store: Store,
    *,
    now: datetime | None = None,
    offline_threshold_s: float = DEFAULT_OFFLINE_THRESHOLD_S,
    idle_threshold_s: float = DEFAULT_IDLE_THRESHOLD_S,
) -> Presence:
    """Load meta + last idle fact and derive presence (CLI/MCP entry)."""
    last_tick = store.get_meta("last_tick")
    idle_watch = store.get_meta("idle_watch") or "n/a"
    last_idle = store.last_by_kind("idle")
    payload = last_idle.payload if last_idle is not None else None
    bound = parse_session_bound_meta(store.get_meta("session_bound"))
    return derive_presence(
        last_tick=last_tick,
        idle_watch=idle_watch,
        last_idle_payload=payload,
        now=now,
        offline_threshold_s=offline_threshold_s,
        idle_threshold_s=idle_threshold_s,
        session_bound=bound,
    )


def format_presence_lines(p: Presence) -> list[str]:
    age = "—" if p.last_tick_age_s is None else f"{p.last_tick_age_s:.0f}s"
    return [
        f"state: {p.state}",
        f"authority: {p.authority}",
        f"confidence: {p.confidence}",
        f"degraded: {str(p.degraded).lower()}",
        f"degraded_reason: {p.degraded_reason or '—'}",
        f"last_tick_age_s: {age}",
        f"idle_watch: {p.idle_watch}",
        f"idle_since: {p.idle_since or '—'}",
        f"threshold_s: {p.threshold_s:.0f}",
        f"session_bound: {str(p.session_bound).lower()}",
    ]
