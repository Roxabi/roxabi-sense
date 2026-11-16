"""Focus coverage effects on the day recap."""

import json
from datetime import timedelta
from pathlib import Path

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
