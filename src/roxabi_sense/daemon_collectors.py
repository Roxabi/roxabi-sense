"""Collector assembly for daemon / once (keeps daemon.py under size gate)."""

from __future__ import annotations

from typing import Any

from roxabi_sense.collectors import (
    AgentSessionsCollector,
    FocusAtspiCollector,
    IdleCollector,
    MprisCollector,
    ProcessPresenceCollector,
    TmuxSessionsCollector,
)
from roxabi_sense.config import SenseConfig
from roxabi_sense.store import Store


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


def tick_one(collector: Any, store: Store, *, label: str) -> int:
    with store.batch():
        try:
            return int(collector.tick(store) or 0)
        except Exception as exc:  # noqa: BLE001
            print(f"sense collector error [{label}]: {exc}", flush=True)
            return 0
