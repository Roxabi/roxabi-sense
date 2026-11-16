"""Focus coverage effects on the day recap."""

import json
from datetime import timedelta
from pathlib import Path

from roxabi_sense.report.care import compile_care_brief
from roxabi_sense.report.day import compile_day_recap
from roxabi_sense.store import Store
from roxabi_sense.util.time import parse_ts, to_z


def test_recap_excludes_unknown_focus_coverage_between_observations(tmp_path: Path) -> None:
    day = "2026-07-30"
    with Store(tmp_path / "sense.db") as store:
        day_start, _ = store.day_bounds(day)
        start = parse_ts(day_start) + timedelta(hours=12)
        focus = {"app": "editor", "title": "work", "source": "cosmic", "window_id": "1:1"}

        store.append(
            "idle",
            {"idle": False, "source": "wayland-idle", "threshold_s": 300},
            ts=to_z(start),
        )
        for seconds, state in ((0, "observed"), (60, "unknown"), (120, "observed")):
            timestamp = to_z(start + timedelta(seconds=seconds))
            coverage_id = store.append(
                "focus_coverage",
                {"state": state, "source": "cosmic", "window_id": "1:1"},
                ts=timestamp,
            )
            if state == "observed":
                store.append("focus", focus, ts=timestamp)
        store.set_meta("focus_observation", json.dumps({
            "coverage_id": coverage_id,
            "source": "cosmic",
            "window_id": "1:1",
            "healthy": True,
            "last_observed_at": to_z(start + timedelta(minutes=3)),
            "fresh_until": to_z(start + timedelta(minutes=4)),
        }))

        # Desktop activity remains dense even while focus coverage is unknown.
        for seconds in range(0, 181, 30):
            store.append(
                "desktop_snapshot",
                {"windows": [], "focus": {"app": "editor"}},
                ts=to_z(start + timedelta(seconds=seconds)),
            )

        recap = compile_day_recap(store, day, now=start + timedelta(minutes=3))

    assert dict(recap.time_by_app).get("editor", 0) == 120
    assert recap.focus_switches == 0
    assert recap.focus_switches_app == 0
    assert recap.focus_switches_context == 0


def test_expired_coverage_does_not_accrue_focus(tmp_path: Path) -> None:
    day = "2026-07-30"
    with Store(tmp_path / "sense.db") as store:
        day_start, _ = store.day_bounds(day)
        start = parse_ts(day_start) + timedelta(hours=12)
        store.append("idle", {"idle": False, "source": "wayland-idle"}, ts=to_z(start))
        coverage_id = store.append(
            "focus_coverage",
            {
                "state": "observed",
                "source": "cosmic",
                "window_id": "1:1",
            },
            ts=to_z(start),
        )
        store.append(
            "focus",
            {"app": "editor", "title": "work", "source": "cosmic", "window_id": "1:1"},
            ts=to_z(start),
        )
        store.set_meta("focus_observation", json.dumps({
            "coverage_id": coverage_id,
            "source": "cosmic",
            "window_id": "1:1",
            "healthy": True,
            "last_observed_at": to_z(start + timedelta(seconds=60)),
            "fresh_until": to_z(start + timedelta(minutes=4)),
        }))
        for seconds in range(0, 181, 30):
            store.append(
                "desktop_snapshot", {"windows": []},
                ts=to_z(start + timedelta(seconds=seconds)),
            )
        recap = compile_day_recap(store, day, now=start + timedelta(minutes=3))

    assert dict(recap.time_by_app).get("editor", 0) == 60


def test_unclosed_observed_tail_without_metadata_is_not_attributed(tmp_path: Path) -> None:
    day = "2026-07-30"
    with Store(tmp_path / "sense.db") as store:
        day_start, _ = store.day_bounds(day)
        start = parse_ts(day_start) + timedelta(hours=12)
        store.append("idle", {"idle": False, "source": "wayland-idle"}, ts=to_z(start))
        store.append(
            "focus_coverage",
            {"state": "observed", "source": "cosmic", "window_id": "1:1"},
            ts=to_z(start),
        )
        store.append(
            "focus",
            {"app": "editor", "title": "work", "source": "cosmic", "window_id": "1:1"},
            ts=to_z(start),
        )
        recap = compile_day_recap(store, day, now=start + timedelta(minutes=3))
    assert dict(recap.time_by_app).get("editor", 0) == 0


def test_healthy_unchanged_focus_is_not_inferred_away(tmp_path: Path) -> None:
    day = "2026-07-30"
    with Store(tmp_path / "sense.db") as store:
        day_start, _ = store.day_bounds(day)
        start = parse_ts(day_start) + timedelta(hours=12)
        store.append("idle", {"idle": False, "source": "wayland-idle"}, ts=to_z(start))
        coverage_id = store.append(
            "focus_coverage",
            {"state": "observed", "source": "cosmic", "window_id": "epoch:1"},
            ts=to_z(start),
        )
        store.append(
            "focus", {"app": "editor", "source": "cosmic", "window_id": "epoch:1"},
            ts=to_z(start),
        )
        store.set_meta("focus_observation", json.dumps({
            "coverage_id": coverage_id, "source": "cosmic", "window_id": "epoch:1",
            "last_observed_at": to_z(start + timedelta(minutes=10)),
            "fresh_until": to_z(start + timedelta(minutes=11)), "healthy": True,
        }))
        recap = compile_day_recap(store, day, now=start + timedelta(minutes=10))
    assert dict(recap.time_by_app)["editor"] == 600
    assert recap.away_total_s == 0


def test_source_loss_preserves_observed_dwell_and_not_current_stretch(tmp_path: Path) -> None:
    day = "2026-07-30"
    with Store(tmp_path / "sense.db") as store:
        day_start, _ = store.day_bounds(day)
        start = parse_ts(day_start) + timedelta(hours=12)
        store.append("idle", {"idle": False, "source": "wayland-idle"}, ts=to_z(start))
        store.append(
            "focus_coverage",
            {"state": "observed", "source": "cosmic", "window_id": "epoch:1"},
            ts=to_z(start),
        )
        store.append(
            "focus", {"app": "editor", "source": "cosmic", "window_id": "epoch:1"},
            ts=to_z(start),
        )
        store.append(
            "focus_coverage", {"state": "unknown", "source": "cosmic", "reason": "lost"},
            ts=to_z(start + timedelta(minutes=1)),
        )
        recap = compile_day_recap(store, day, now=start + timedelta(minutes=2))
    brief = compile_care_brief(recap, {"state": "active"}, now=start + timedelta(minutes=2))
    assert dict(recap.time_by_app)["editor"] == 60
    assert recap.away_total_s == 0
    assert brief["current_stretch"] is None


def test_midnight_carry_in_remains_bounded_by_current_observation(tmp_path: Path) -> None:
    day = "2026-07-30"
    with Store(tmp_path / "sense.db") as store:
        day_start, _ = store.day_bounds(day)
        midnight = parse_ts(day_start)
        before = midnight - timedelta(seconds=30)
        store.append("idle", {"idle": False}, ts=to_z(before))
        coverage_id = store.append(
            "focus_coverage",
            {"state": "observed", "source": "cosmic", "window_id": "epoch:1"},
            ts=to_z(before),
        )
        store.append(
            "focus", {"app": "editor", "source": "cosmic", "window_id": "epoch:1"},
            ts=to_z(before),
        )
        store.set_meta("focus_observation", json.dumps({
            "coverage_id": coverage_id, "source": "cosmic", "window_id": "epoch:1",
            "last_observed_at": to_z(midnight + timedelta(minutes=1)),
            "fresh_until": to_z(midnight + timedelta(minutes=2)), "healthy": True,
        }))
        recap = compile_day_recap(store, day, now=midnight + timedelta(minutes=1))
    assert dict(recap.time_by_app)["editor"] == 60
    assert recap.away_total_s == 0


def test_native_loss_then_legacy_source_is_unverified_not_lost(tmp_path: Path) -> None:
    day = "2026-07-30"
    with Store(tmp_path / "sense.db") as store:
        day_start, _ = store.day_bounds(day)
        start = parse_ts(day_start) + timedelta(hours=12)
        store.append("idle", {"idle": False}, ts=to_z(start))
        store.append("focus_coverage", {
            "state": "observed", "source": "cosmic", "window_id": "epoch:1",
        }, ts=to_z(start))
        store.append("focus", {
            "app": "editor", "source": "cosmic", "window_id": "epoch:1",
        }, ts=to_z(start))
        store.append("focus_coverage", {
            "state": "unknown", "source": "cosmic", "reason": "lost",
        }, ts=to_z(start + timedelta(minutes=1)))
        store.append("focus_legacy", {"source": "x11"}, ts=to_z(start + timedelta(minutes=2)))
        store.append("focus", {"app": "browser", "source": "x11"},
                     ts=to_z(start + timedelta(minutes=2)))
        recap = compile_day_recap(store, day, now=start + timedelta(minutes=3))
    assert dict(recap.time_by_app)["editor"] == 60
    assert dict(recap.time_by_app)["browser"] == 60
    assert recap.focus_coverage_quality == "legacy_unverified"
    assert recap.current_focus_quality == "legacy_unverified"


def test_native_ghostty_uses_terminal_classification(tmp_path: Path) -> None:
    day = "2026-07-30"
    with Store(tmp_path / "sense.db") as store:
        day_start, _ = store.day_bounds(day)
        start = parse_ts(day_start) + timedelta(hours=12)
        store.append("idle", {"idle": False}, ts=to_z(start))
        coverage_id = store.append("focus_coverage", {
            "state": "observed", "source": "cosmic", "window_id": "epoch:1",
        }, ts=to_z(start))
        store.append("focus", {
            "app": "com.mitchellh.ghostty", "source": "cosmic", "window_id": "epoch:1",
        }, ts=to_z(start))
        store.set_meta("focus_observation", json.dumps({
            "coverage_id": coverage_id, "source": "cosmic", "window_id": "epoch:1",
            "last_observed_at": to_z(start + timedelta(minutes=1)),
            "fresh_until": to_z(start + timedelta(minutes=2)), "healthy": True,
        }))
        recap = compile_day_recap(store, day, now=start + timedelta(minutes=1))
    assert dict(recap.time_by_app)["ghostty"] == 60
    assert recap.terminal_stays.visits == 1
