"""Idle backend chain meta (#45)."""

from __future__ import annotations

from pathlib import Path

from roxabi_sense.collectors.idle_meta import resolve_idle_chain, write_idle_meta
from roxabi_sense.config import SenseConfig
from roxabi_sense.store import Store


def test_auto_wayland_primary() -> None:
    cfg = SenseConfig(idle=True, idle_backend="auto")
    b, s, r = resolve_idle_chain(cfg, wayland_healthy=True, logind_active=True)
    assert b == "wayland-idle"
    assert s == "available"
    assert "wayland" in r


def test_auto_logind_fallback() -> None:
    cfg = SenseConfig(idle=True, idle_backend="auto")
    b, s, r = resolve_idle_chain(cfg, wayland_healthy=False, logind_active=True)
    assert b == "logind"
    assert s == "degraded"


def test_auto_noop() -> None:
    cfg = SenseConfig(idle=True, idle_backend="auto")
    b, s, _ = resolve_idle_chain(cfg, wayland_healthy=False, logind_active=False)
    assert b == "noop"
    assert s == "unavailable"


def test_write_meta(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    cfg = SenseConfig(idle=True, idle_backend="auto")
    write_idle_meta(store, cfg, wayland_healthy=False, logind_active=True)
    assert store.get_meta("idle_backend") == "logind"
    assert store.get_meta("idle_status") == "degraded"
    assert store.get_meta("idle_chain_reason")
    store.close()
