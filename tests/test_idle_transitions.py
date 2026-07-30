"""Idle transition writer + demote semantics."""

from __future__ import annotations

from pathlib import Path

from roxabi_sense.collectors.idle import IdleCollector
from roxabi_sense.collectors.idle_facts import append_idle_transition, compute_idle_since
from roxabi_sense.config import SenseConfig
from roxabi_sense.daemon_collectors import want_logind_idle, want_wayland_idle
from roxabi_sense.store import Store


def test_append_idle_enter_sets_idle_since(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    with Store(db) as store:
        append_idle_transition(
            store,
            idle=True,
            source="wayland-idle",
            threshold_s=300,
            ts="2026-07-30T12:05:00Z",
        )
        ev = store.last_by_kind("idle")
    assert ev is not None
    assert ev.payload["idle"] is True
    assert ev.payload["source"] == "wayland-idle"
    assert ev.payload["threshold_s"] == 300
    assert ev.payload["idle_since"] == "2026-07-30T12:00:00Z"


def test_idle_since_with_last_activity(tmp_path: Path) -> None:
    since = compute_idle_since(
        enter_ts="2026-07-30T12:10:00Z",
        threshold_s=300,
        last_activity_ts="2026-07-30T12:08:00Z",
    )
    assert since == "2026-07-30T12:08:00Z"


def test_logind_demoted_when_wayland_healthy() -> None:
    cfg = SenseConfig(idle=True, idle_backend="auto")
    assert want_wayland_idle(cfg) is True
    assert want_logind_idle(cfg, wayland_healthy=True) is False
    assert want_logind_idle(cfg, wayland_healthy=False) is True


def test_idle_collector_disabled(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    c = IdleCollector(enabled=False)
    assert c.tick(store) == 0
    store.close()


def test_no_heartbeat_kind_from_once(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "s.db"
    monkeypatch.setenv("SENSE_DB", str(db))
    from roxabi_sense.cli import main

    # Disable noisy collectors that need system tools — still no heartbeat
    main(["once"])
    with Store(db) as store:
        rows = store.events_between("1970-01-01T00:00:00Z", "2100-01-01T00:00:00Z", limit=5000)
        kinds = {r.kind for r in rows}
        assert "heartbeat" not in kinds
        assert store.get_meta("last_tick") is not None
