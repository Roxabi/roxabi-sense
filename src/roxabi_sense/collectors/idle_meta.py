"""Idle backend chain meta (wayland → logind → noop) — ADR-002 aligned."""

from __future__ import annotations

from typing import Literal

from roxabi_sense.config import SenseConfig
from roxabi_sense.store import Store

IdleBackendId = Literal["wayland-idle", "logind", "noop", "off", "auto"]
IdleStatus = Literal["available", "degraded", "unavailable"]


def write_idle_meta(
    store: Store,
    cfg: SenseConfig,
    *,
    wayland_healthy: bool,
    logind_active: bool,
) -> None:
    """Write meta.idle_backend + meta.idle_status for doctor/capabilities."""
    backend, status, reason = resolve_idle_chain(
        cfg, wayland_healthy=wayland_healthy, logind_active=logind_active
    )
    store.set_meta("idle_backend", backend)
    store.set_meta("idle_status", status)
    store.set_meta("idle_chain_reason", reason)


def resolve_idle_chain(
    cfg: SenseConfig,
    *,
    wayland_healthy: bool,
    logind_active: bool,
) -> tuple[str, IdleStatus, str]:
    """Return (backend_id, status, short_reason)."""
    if not cfg.idle or cfg.idle_backend == "off":
        return "off", "unavailable", "idle disabled"

    prefer = cfg.idle_backend  # auto | wayland | logind

    if prefer == "logind":
        if logind_active:
            return "logind", "available", "config=logind"
        return "logind", "degraded", "logind configured but not polling"

    if prefer == "wayland":
        if wayland_healthy:
            return "wayland-idle", "available", "config=wayland"
        return "noop", "unavailable", "wayland required but watch dead"

    # auto: wayland → logind → noop
    if wayland_healthy:
        return "wayland-idle", "available", "auto: wayland primary"
    if logind_active:
        return "logind", "degraded", "auto: logind fallback (wayland not ready)"
    return "noop", "unavailable", "auto: no wayland and no logind"
