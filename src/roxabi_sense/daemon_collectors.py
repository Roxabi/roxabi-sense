"""Collector assembly for daemon / once (keeps daemon.py under size gate)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from roxabi_sense.collectors import (
    AgentSessionsCollector,
    FocusAtspiCollector,
    IdleCollector,
    MprisCollector,
    ProcessPresenceCollector,
    TmuxSessionsCollector,
)
from roxabi_sense.collectors.idle_facts import append_idle_transition
from roxabi_sense.collectors.idle_watch import SOURCE as WAYLAND_IDLE_SOURCE
from roxabi_sense.config import SenseConfig
from roxabi_sense.store import Store


def _utc_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class FocusEventGate:
    """Leading + trailing rate limit for event-driven focus probes."""

    min_interval: float
    last_probe: float = 0.0
    trailing_at: float | None = None

    def on_event(self, now: float) -> bool:
        """True → probe now. False → trailing scheduled at trailing_at."""
        if now - self.last_probe >= self.min_interval:
            self.last_probe = now
            self.trailing_at = None
            return True
        self.trailing_at = self.last_probe + self.min_interval
        return False

    def on_timer(self, now: float) -> bool:
        if self.trailing_at is not None and now >= self.trailing_at:
            self.last_probe = now
            self.trailing_at = None
            return True
        return False


def handle_idle_msg(
    msg: dict[str, Any],
    *,
    store: Store,
    cfg: SenseConfig,
    last_activity_ts: str | None,
) -> None:
    typ = msg.get("type")
    if typ == "ready":
        store.set_meta("idle_watch", "ready")
        print("sense idle-watch: ready (logind demoted)", flush=True)
    elif typ == "error":
        store.set_meta("idle_watch", "dead")
        print(f"sense idle-watch error: {msg.get('error')}", flush=True)
    elif typ == "idle" and isinstance(msg.get("idle"), bool):
        idle_flag = bool(msg["idle"])
        ts = _utc_stamp()
        with store.batch():
            append_idle_transition(
                store,
                idle=idle_flag,
                source=WAYLAND_IDLE_SOURCE,
                threshold_s=cfg.idle_threshold_s,
                ts=ts,
                last_activity_ts=None if idle_flag else last_activity_ts,
            )
            store.set_meta("last_tick", ts)


def want_wayland_idle(cfg: SenseConfig) -> bool:
    if not cfg.idle:
        return False
    return cfg.idle_backend in {"auto", "wayland"}


def want_logind_idle(cfg: SenseConfig, *, wayland_healthy: bool) -> bool:
    if not cfg.idle:
        return False
    if cfg.idle_backend == "logind":
        return True
    if cfg.idle_backend in {"off", "wayland"}:
        return False
    return not wayland_healthy


def build_poll_collectors(
    cfg: SenseConfig,
    *,
    logind_idle: bool = True,
) -> list[Any]:
    collectors: list[Any] = []
    if cfg.agent_sessions:
        collectors.append(AgentSessionsCollector())
    if cfg.process_presence:
        collectors.append(ProcessPresenceCollector(cfg.process_names))
    if cfg.idle and logind_idle:
        collectors.append(IdleCollector(threshold_s=cfg.idle_threshold_s, enabled=True))
    if cfg.mpris:
        collectors.append(MprisCollector())
    if cfg.tmux:
        collectors.append(TmuxSessionsCollector())
    return collectors


def build_collectors(cfg: SenseConfig) -> list[Any]:
    use_logind = cfg.idle and cfg.idle_backend in {"logind", "auto"}
    cols = build_poll_collectors(cfg, logind_idle=use_logind)
    if cfg.focus:
        cols.append(FocusAtspiCollector())
    return cols


def tick_all(collectors: list[Any], store: Store) -> int:
    wrote = 0
    with store.batch():
        for c in collectors:
            try:
                wrote += int(c.tick(store) or 0)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"sense collector error [{getattr(c, 'name', '?')}]: {exc}",
                    flush=True,
                )
    return wrote


def tick_one(
    collector: Any,
    store: Store,
    *,
    label: str,
    method: str = "tick",
) -> int:
    """Run collector.tick / tick_focus / tick_desktop under a store batch."""
    fn = getattr(collector, method, None)
    if fn is None:
        fn = collector.tick
    with store.batch():
        try:
            return int(fn(store) or 0)
        except Exception as exc:  # noqa: BLE001
            print(f"sense collector error [{label}]: {exc}", flush=True)
            return 0
