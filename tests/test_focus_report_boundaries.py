"""Day carry-in and coarse privacy at the public report boundary."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from roxabi_sense.query import SenseQuery
from roxabi_sense.report.care import compile_care_brief
from roxabi_sense.report.day import compile_day_recap
from roxabi_sense.store import Store
from roxabi_sense.util.time import parse_ts, to_z


@pytest.mark.parametrize("history", ["legacy_only", "legacy_marker", "legacy_after_native"])
def test_prior_legacy_focus_does_not_create_focus_on_an_empty_day(
    tmp_path: Path, history: str
) -> None:
    day = "2026-07-30"
    with Store(tmp_path / "sense.db") as store:
        day_start, _ = store.day_bounds(day)
        midnight = parse_ts(day_start)
        previous = midnight - timedelta(hours=1)
        if history == "legacy_after_native":
            store.append("focus_coverage", {
                "state": "observed", "source": "cosmic", "window_id": "epoch:old",
            }, ts=to_z(previous - timedelta(minutes=5)))
            store.append("focus", {
                "app": "native", "source": "cosmic", "window_id": "epoch:old",
            }, ts=to_z(previous - timedelta(minutes=5)))
            store.append("focus_coverage", {
                "state": "unknown", "source": "cosmic", "reason": "source_changed",
            }, ts=to_z(previous - timedelta(minutes=1)))
        if history != "legacy_only":
            store.append("focus_legacy", {
                "source": "x11", "reason": "source_changed",
            }, ts=to_z(previous - timedelta(seconds=1)))
        store.append("focus", {"app": "editor", "source": "x11"}, ts=to_z(previous))
        recap = compile_day_recap(store, day, now=midnight + timedelta(hours=12))

    assert recap.event_count == 0
    assert recap.focus_segments == []
    assert recap.attention_segments == []
    assert recap.time_by_app == []


@pytest.mark.parametrize("identity", ["cwd", "pid"])
def test_coarse_day_recap_never_serializes_private_context_identity(
    tmp_path: Path, monkeypatch, identity: str
) -> None:
    day = "2026-07-30"
    db = tmp_path / "sense.db"
    with Store(db) as store:
        day_start, _ = store.day_bounds(day)
        start = parse_ts(day_start) + timedelta(hours=12)
        horizon = start + timedelta(minutes=10)
        store.append("idle", {"idle": False}, ts=to_z(start))
        if identity == "cwd":
            private_value = str(Path("/home/user") / "private-client")
            payload = {"app": "ghostty", "source": "x11", "agent": {"cwd": private_value}}
        else:
            private_value = str(874_359_123)
            payload = {"app": "editor", "source": "x11", "pid": int(private_value)}
        store.append("focus", payload, ts=to_z(start))

    monkeypatch.setattr("roxabi_sense.report.day.horizon_dt", lambda end, now: horizon)
    result = SenseQuery(db_path=db, offline_threshold_s=120, idle_threshold_s=300).day_recap(day)
    assert result["db_exists"] is True
    assert result["current_attention"]["quality"] == "legacy_unverified"
    assert result["current_attention"]["stretch"] is not None
    assert private_value not in json.dumps(result, ensure_ascii=False)


def test_cleared_agent_identity_starts_current_window_context(tmp_path: Path) -> None:
    day = "2026-07-30"
    with Store(tmp_path / "sense.db") as store:
        day_start, _ = store.day_bounds(day)
        start = parse_ts(day_start) + timedelta(hours=12)
        cleared = start + timedelta(minutes=40)
        horizon = start + timedelta(minutes=50)
        store.append("idle", {"idle": False}, ts=to_z(start))
        coverage_id = store.append("focus_coverage", {
            "state": "observed", "source": "cosmic", "window_id": "epoch:w",
        }, ts=to_z(start))
        store.append("focus", {
            "app": "editor", "title": "document", "source": "cosmic", "window_id": "epoch:w",
            "agent": {"agent": "omp", "session_id": "session-s"},
        }, ts=to_z(start))
        store.append("focus", {
            "app": "editor", "title": "document", "source": "cosmic", "window_id": "epoch:w",
            "agent": None,
        }, ts=to_z(cleared))
        store.set_meta("focus_observation", json.dumps({
            "coverage_id": coverage_id, "source": "cosmic", "window_id": "epoch:w",
            "last_observed_at": to_z(horizon),
            "fresh_until": to_z(horizon + timedelta(seconds=8)), "healthy": True,
        }))
        recap = compile_day_recap(store, day, now=horizon)
        brief = compile_care_brief(recap, {"state": "active"}, now=horizon)

    assert dict(recap.time_by_app)["editor"] == 50 * 60
    assert recap.current_attention is not None
    assert recap.current_attention.quality == "observed"
    assert recap.attention_segments[-1].session_id is None
    assert recap.attention_segments[-1].agent is None
    assert brief["current_stretch"] == {"app": "editor", "minutes": 10.0}
    assert "long_focus" not in brief["signals"]
