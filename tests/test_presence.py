"""Presence derivation + status CLI (ADR-002)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from roxabi_sense.cli import main
from roxabi_sense.collectors.idle_facts import append_idle_transition, compute_idle_since
from roxabi_sense.report.presence import derive_presence, presence_from_store
from roxabi_sense.store import Store


def test_derive_offline_missing_tick() -> None:
    p = derive_presence(last_tick=None, idle_watch="n/a")
    assert p.state == "offline"
    assert p.authority == "daemon"
    assert p.last_tick_age_s is None


def test_derive_offline_stale_tick() -> None:
    now = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)
    old = (now - timedelta(seconds=500)).strftime("%Y-%m-%dT%H:%M:%SZ")
    p = derive_presence(
        last_tick=old,
        idle_watch="ready",
        now=now,
        offline_threshold_s=120,
    )
    assert p.state == "offline"
    assert p.last_tick_age_s is not None
    assert p.last_tick_age_s >= 500


def test_derive_idle_wayland_high_confidence() -> None:
    now = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)
    tick = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    p = derive_presence(
        last_tick=tick,
        idle_watch="ready",
        last_idle_payload={
            "idle": True,
            "source": "wayland-idle",
            "threshold_s": 300,
            "idle_since": "2026-07-30T11:55:00Z",
        },
        now=now,
        session_bound=True,
    )
    assert p.state == "idle"
    assert p.authority == "wayland-idle"
    assert p.confidence == "high"
    assert p.degraded is False
    assert p.idle_since == "2026-07-30T11:55:00Z"


def test_derive_watch_dead_not_confident_active() -> None:
    now = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)
    tick = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    p = derive_presence(
        last_tick=tick,
        idle_watch="dead",
        last_idle_payload={"idle": False, "source": "wayland-idle"},
        now=now,
        session_bound=True,
    )
    assert p.state == "active"
    assert p.confidence == "low"
    assert p.degraded is True


def test_presence_from_store(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    with Store(db) as store:
        store.set_meta("last_tick", "2026-07-30T12:00:00Z")
        store.set_meta("idle_watch", "ready")
        append_idle_transition(
            store,
            idle=True,
            source="wayland-idle",
            threshold_s=300,
            ts="2026-07-30T12:00:00Z",
        )
        p = presence_from_store(
            store,
            now=datetime(2026, 7, 30, 12, 0, 30, tzinfo=UTC),
            offline_threshold_s=120,
        )
    assert p.state == "idle"
    assert p.authority == "wayland-idle"


def test_idle_since_bias() -> None:
    since = compute_idle_since(enter_ts="2026-07-30T12:05:00Z", threshold_s=300)
    assert since == "2026-07-30T12:00:00Z"


def test_status_cli_offline(tmp_path: Path, monkeypatch, capsys) -> None:
    db = tmp_path / "missing.db"
    monkeypatch.setenv("SENSE_DB", str(db))
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "state: offline" in out
    assert "last_tick_age_s:" in out
    assert "confidence:" in out
    assert "degraded:" in out


def test_status_cli_json_stale(tmp_path: Path, monkeypatch, capsys) -> None:
    db = tmp_path / "s.db"
    monkeypatch.setenv("SENSE_DB", str(db))
    with Store(db) as store:
        store.set_meta("last_tick", "2020-01-01T00:00:00Z")
        store.set_meta("idle_watch", "n/a")
    assert main(["status", "--json"]) == 0
    out = capsys.readouterr().out
    assert '"state": "offline"' in out
