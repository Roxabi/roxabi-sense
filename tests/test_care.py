"""care_brief: current stretch and last away, not day totals."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from roxabi_sense.report.care import compile_care_brief

# 17:00 UTC → hour ≥ 16 in UTC and CEST.
NOW = datetime(2026, 9, 17, 17, 0, tzinfo=UTC)


def _seg(app: str, minutes: float, end: str) -> SimpleNamespace:
    return SimpleNamespace(app=app, duration_s=minutes * 60.0, end=end)


def _away(minutes: float, end: str, presence: str = "away") -> SimpleNamespace:
    return SimpleNamespace(duration_s=minutes * 60.0, end=end, presence=presence)


def _recap(**kw: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "day": "2026-09-17",
        "first_event": None,
        "last_event": None,
        "top_apps": [],
        "away_total_s": 0.0,
        "session_shape": "steady",
        "attention_segments": [],
        "away_segments": [],
        "terminal_stays": None,
        "meeting_sessions": [],
        "meeting_total_s": 0.0,
        "idle_events": 0,
        "focus_switches": 0,
        "agent_sessions": [],
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_current_stretch_is_open_segment_not_day_max() -> None:
    recap = _recap(
        attention_segments=[
            _seg("slack", 50, "2026-09-17T10:00:00Z"),
            _seg("ghostty", 12, "2026-09-17T16:59:30Z"),
        ]
    )
    body = compile_care_brief(recap, {"state": "active"}, now=NOW)
    assert body["longest_focus_app"] == {"app": "slack", "minutes": 50.0}
    assert body["current_stretch"] == {"app": "ghostty", "minutes": 12.0}
    assert "long_focus" not in body["signals"]


def test_day_away_total_does_not_fire_post_peak() -> None:
    recap = _recap(
        away_total_s=50_000.0,
        away_segments=[_away(8, "2026-09-17T16:50:00Z")],
    )
    body = compile_care_brief(recap, {"state": "active"}, now=NOW)
    assert body["last_away"] == {"minutes": 8.0, "ongoing": False}
    assert "post_peak_idle" not in body["signals"]


def test_recent_pause_fires_post_peak_idle() -> None:
    recap = _recap(away_segments=[_away(12, "2026-09-17T16:50:00Z")])
    body = compile_care_brief(recap, {"state": "active"}, now=NOW)
    assert body["last_away"] == {"minutes": 12.0, "ongoing": False}
    assert "post_peak_idle" in body["signals"]


def test_idle_presence_is_ongoing_pause() -> None:
    recap = _recap()
    body = compile_care_brief(
        recap,
        {"state": "idle", "idle_since": "2026-09-17T16:40:00Z"},
        now=NOW,
    )
    assert body["last_away"] == {"minutes": 20.0, "ongoing": True}
    assert body["current_stretch"] is None
