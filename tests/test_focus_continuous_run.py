"""Care must retain a validated, continuous attention run across metadata changes."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from roxabi_sense.query import SenseQuery
from roxabi_sense.report.care import compile_care_brief
from roxabi_sense.report.day import compile_day_recap
from roxabi_sense.store import Store
from roxabi_sense.util.time import parse_ts, to_z


def _observation(store: Store, coverage_id: int, window_id: str, end: datetime) -> None:
    store.set_meta("focus_observation", json.dumps({
        "coverage_id": coverage_id,
        "source": "cosmic",
        "window_id": window_id,
        "last_observed_at": to_z(end),
        "fresh_until": to_z(end + timedelta(seconds=8)),
        "healthy": True,
    }))


def test_title_update_at_observation_end_preserves_current_stretch(
    tmp_path: Path, monkeypatch
) -> None:
    day = "2026-07-30"
    db = tmp_path / "sense.db"
    with Store(db) as store:
        day_start, _ = store.day_bounds(day)
        start = parse_ts(day_start) + timedelta(hours=12)
        last_observed = start + timedelta(minutes=40)
        horizon = last_observed + timedelta(seconds=1)
        store.append("idle", {"idle": False}, ts=to_z(start))
        coverage_id = store.append("focus_coverage", {
            "state": "observed", "source": "cosmic", "window_id": "epoch:w",
        }, ts=to_z(start))
        store.append("focus", {
            "app": "editor", "title": "first", "source": "cosmic", "window_id": "epoch:w",
        }, ts=to_z(start))
        store.append("focus", {
            "app": "editor", "title": "updated", "source": "cosmic", "window_id": "epoch:w",
        }, ts=to_z(last_observed))
        _observation(store, coverage_id, "epoch:w", last_observed)

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return horizon if tz is not None else horizon.replace(tzinfo=None)

        monkeypatch.setattr("roxabi_sense.report.status.datetime", FrozenDatetime)
        active = SenseQuery(db_path=db, offline_threshold_s=120, idle_threshold_s=300).active_now()
        recap = compile_day_recap(store, day, now=horizon)
        brief = compile_care_brief(recap, {"state": "active"}, now=horizon)

    assert active["focus_quality"] == "observed"
    assert active["focus"]["app"] == "editor"
    assert len(recap.attention_segments) == 1
    assert brief["current_stretch"] is not None
    assert brief["current_stretch"]["app"] == "editor"
    assert brief["current_stretch"]["minutes"] == 40
    assert "long_focus" in brief["signals"]


@pytest.mark.parametrize(("current_app", "minutes"), [("editor", 45), ("browser", 15)])
def test_continuous_same_session_across_native_windows_preserves_stretch(
    tmp_path: Path, current_app: str, minutes: int
) -> None:
    day = "2026-07-30"
    with Store(tmp_path / "sense.db") as store:
        day_start, _ = store.day_bounds(day)
        start = parse_ts(day_start) + timedelta(hours=12)
        switch = start + timedelta(minutes=30)
        horizon = start + timedelta(minutes=45)
        agent = {"agent": "agent", "session_id": "session-s"}
        store.append("idle", {"idle": False}, ts=to_z(start))
        store.append("focus_coverage", {
            "state": "observed", "source": "cosmic", "window_id": "epoch:a",
        }, ts=to_z(start))
        store.append("focus", {
            "app": "editor", "title": "a", "source": "cosmic",
            "window_id": "epoch:a", "agent": agent,
        }, ts=to_z(start))
        coverage_id = store.append("focus_coverage", {
            "state": "observed", "source": "cosmic", "window_id": "epoch:b",
        }, ts=to_z(switch))
        store.append("focus", {
            "app": current_app, "title": "b", "source": "cosmic",
            "window_id": "epoch:b", "agent": agent,
        }, ts=to_z(switch))
        _observation(store, coverage_id, "epoch:b", horizon)
        recap = compile_day_recap(store, day, now=horizon)
        brief = compile_care_brief(recap, {"state": "active"}, now=horizon)

    assert recap.current_attention is not None
    assert recap.current_attention.quality == "observed"
    assert len(recap.attention_segments) == (1 if current_app == "editor" else 2)
    assert recap.attention_segments[0].window_id == "epoch:a"
    assert recap.current_attention.window_id == "epoch:b"
    assert brief["current_stretch"] is not None
    assert brief["current_stretch"]["app"] == current_app
    assert brief["current_stretch"]["minutes"] == minutes
    assert ("long_focus" in brief["signals"]) == (minutes == 45)


@pytest.mark.parametrize("recovered_seconds", [2, 3])
def test_short_intervening_context_starts_a_new_occurrence(
    tmp_path: Path, recovered_seconds: int
) -> None:
    day = "2026-07-30"
    with Store(tmp_path / "sense.db") as store:
        day_start, _ = store.day_bounds(day)
        start = parse_ts(day_start) + timedelta(hours=12)
        first_switch = start + timedelta(minutes=40)
        recovery = first_switch + timedelta(seconds=1)
        horizon = recovery + timedelta(seconds=recovered_seconds)
        store.append("idle", {"idle": False}, ts=to_z(start))
        store.append("focus_coverage", {
            "state": "observed", "source": "cosmic", "window_id": "epoch:a",
        }, ts=to_z(start))
        store.append("focus", {
            "app": "editor", "source": "cosmic", "window_id": "epoch:a",
        }, ts=to_z(start))
        store.append("focus_coverage", {
            "state": "observed", "source": "cosmic", "window_id": "epoch:b",
        }, ts=to_z(first_switch))
        store.append("focus", {
            "app": "browser", "source": "cosmic", "window_id": "epoch:b",
        }, ts=to_z(first_switch))
        coverage_id = store.append("focus_coverage", {
            "state": "observed", "source": "cosmic", "window_id": "epoch:a",
        }, ts=to_z(recovery))
        store.append("focus", {
            "app": "editor", "source": "cosmic", "window_id": "epoch:a",
        }, ts=to_z(recovery))
        _observation(store, coverage_id, "epoch:a", horizon)
        recap = compile_day_recap(store, day, now=horizon)
        brief = compile_care_brief(recap, {"state": "active"}, now=horizon)
    assert recap.current_attention is not None
    assert recap.current_attention.quality == "observed"
    assert recap.current_attention.context_started_at == to_z(recovery)
    if recovered_seconds < 3:
        assert brief["current_stretch"] is None
    else:
        assert brief["current_stretch"] == {"app": "editor", "minutes": 0.05}
    assert "long_focus" not in brief["signals"]
