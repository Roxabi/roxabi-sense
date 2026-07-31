"""Meeting-window annotation on idle/away segments."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from roxabi_sense.report.day import compile_day_recap, format_day_recap
from roxabi_sense.report.meeting import (
    annotate_away_with_meetings,
    match_meeting_title,
    meeting_hint_from_windows,
)
from roxabi_sense.report.segments import AwaySegment
from roxabi_sense.store import Event, Store


def test_match_meet_titles() -> None:
    h = match_meeting_title(
        "Meet – Authentic x Silex - Session 2 - Camera and microphone recording"
        " - Google Chrome - Mickael",
        "Google Chrome",
    )
    assert h is not None
    assert h.provider == "meet"
    assert "Authentic" in (h.label or "")

    assert match_meeting_title("meet.google.com/rhf-ejmd-ipg - Google Chrome") is not None
    assert match_meeting_title("meet.google.com is sharing your screen.") is not None
    assert match_meeting_title("Google Meet") is not None
    assert match_meeting_title("Zoom Meeting - My Call") is not None
    assert match_meeting_title("Standup | Microsoft Teams") is not None

    # no false positives on terminal / docs
    assert match_meeting_title("Claap Arman extraction max data reunion - grok") is None
    assert match_meeting_title("docs - Google Chrome") is None
    assert match_meeting_title("let's meet later - notes") is None


def test_meeting_hint_prefers_active_window() -> None:
    wins = [
        {
            "app": "Google Chrome",
            "title": "Meet – Background - Camera and microphone recording",
            "active": False,
        },
        {
            "app": "Google Chrome",
            "title": "Meet – Focused - Camera and microphone recording",
            "active": True,
        },
    ]
    h = meeting_hint_from_windows(wins)
    assert h is not None
    assert "Focused" in h.label
    assert h.active is True


def test_annotate_last_known_windows_during_idle() -> None:
    """Sparse snapshots: last-known Meet title before gap still annotates away."""
    away = [
        AwaySegment(
            start="2026-07-31T08:59:17Z",
            end="2026-07-31T09:30:22Z",
            duration_s=1865.0,
            mode="wayland-idle",
        )
    ]
    events = [
        Event(
            id=1,
            ts="2026-07-31T08:50:00Z",
            kind="desktop_snapshot",
            payload={
                "windows": [
                    {
                        "app": "Google Chrome",
                        "title": (
                            "Meet – Authentic x Silex - Session 2 - "
                            "Camera and microphone recording - Google Chrome"
                        ),
                        "active": False,
                    },
                    {"app": "ghostty", "title": "work - grok", "active": True},
                ]
            },
        ),
        # sparse snap mid-gap (same Meet still open)
        Event(
            id=2,
            ts="2026-07-31T09:16:50Z",
            kind="desktop_snapshot",
            payload={
                "windows": [
                    {
                        "app": "Google Chrome",
                        "title": (
                            "Meet – Authentic x Silex - Session 2 - "
                            "Camera and microphone recording - Google Chrome"
                        ),
                        "active": False,
                    }
                ]
            },
        ),
    ]
    out = annotate_away_with_meetings(away, events)
    assert len(out) == 1
    assert out[0].presence == "meeting"
    assert out[0].meeting_provider == "meet"
    assert out[0].mode == "wayland-idle"
    assert out[0].meeting_label and "Authentic" in out[0].meeting_label


def test_annotate_pure_away_without_meeting() -> None:
    away = [
        AwaySegment(
            start="2026-07-30T16:47:00Z",
            end="2026-07-30T18:32:00Z",
            duration_s=6300.0,
            mode="degraded-gap",
        )
    ]
    events = [
        Event(
            id=1,
            ts="2026-07-30T16:40:00Z",
            kind="desktop_snapshot",
            payload={
                "windows": [
                    {"app": "ghostty", "title": "work - grok", "active": True},
                ]
            },
        ),
    ]
    out = annotate_away_with_meetings(away, events)
    assert out[0].presence == "away"
    assert out[0].meeting_label is None


def test_recap_meeting_vs_away_totals(tmp_path: Path) -> None:
    db = tmp_path / "sense.db"
    with Store(db) as store:
        store.append(
            "focus",
            {"app": "ghostty", "title": "work - grok"},
            ts="2026-07-31T08:50:00Z",
        )
        store.append(
            "desktop_snapshot",
            {
                "windows": [
                    {
                        "app": "Google Chrome",
                        "title": (
                            "Meet – Authentic x Silex - Session 2 - "
                            "Camera and microphone recording - Google Chrome"
                        ),
                        "active": False,
                    },
                    {"app": "ghostty", "title": "work - grok", "active": True},
                ]
            },
            ts="2026-07-31T08:55:00Z",
        )
        store.append(
            "idle",
            {
                "idle": True,
                "source": "wayland-idle",
                "threshold_s": 300,
                "idle_since": "2026-07-31T08:59:17Z",
            },
            ts="2026-07-31T09:04:17Z",
        )
        store.append(
            "idle",
            {"idle": False, "source": "wayland-idle", "threshold_s": 300},
            ts="2026-07-31T09:30:22Z",
        )
        store.append(
            "focus",
            {"app": "Discord", "title": "#veille"},
            ts="2026-07-31T09:30:24Z",
        )
        # second pure-away gap later (no Meet windows)
        store.append(
            "idle",
            {
                "idle": True,
                "source": "wayland-idle",
                "threshold_s": 300,
                "idle_since": "2026-07-31T11:00:00Z",
            },
            ts="2026-07-31T11:05:00Z",
        )
        store.append(
            "idle",
            {"idle": False, "source": "wayland-idle", "threshold_s": 300},
            ts="2026-07-31T11:20:00Z",
        )
        store.append(
            "desktop_snapshot",
            {"windows": [{"app": "ghostty", "title": "solo", "active": True}]},
            ts="2026-07-31T10:50:00Z",
        )
        recap = compile_day_recap(
            store,
            "2026-07-31",
            now=datetime(2026, 7, 31, 12, 0, 0, tzinfo=UTC),
        )

    assert recap.meeting_total_s >= 30 * 60
    assert recap.away_total_s >= 14 * 60  # pure 11:00–11:20 gap
    assert any(a.presence == "meeting" for a in recap.away_segments)
    assert any(a.presence == "away" for a in recap.away_segments)

    text = format_day_recap(recap)
    assert "meeting=" in text
    assert "Idle gaps" in text
    assert "Authentic" in text or "meeting ·" in text
    # hour bucket uses "meeting" label
    hour_labels = {app for _, apps in recap.hour_apps for app, _ in apps}
    assert "meeting" in hour_labels
