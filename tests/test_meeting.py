"""Meeting classification, sessions (ADR-004), idle overlay."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from roxabi_sense.report.day import compile_day_recap, format_day_recap
from roxabi_sense.report.meeting import (
    MeetingHint,
    annotate_away_with_meetings,
    match_meeting_title,
    meeting_hint_from_windows,
)
from roxabi_sense.report.meeting_sessions import (
    MeetingSession,
    meeting_sessions,
    sessions_from_samples,
)
from roxabi_sense.report.segments import AwaySegment
from roxabi_sense.store import Event, Store

_MEET_IN_CALL = (
    "Meet – qwb-fxnf-dje - Camera and microphone recording - Google Chrome"
)
_MEET_AUTH = (
    "Meet – Authentic x Silex - Session 2 - Camera and microphone recording"
    " - Google Chrome"
)


def test_match_meet_titles() -> None:
    h = match_meeting_title(
        "Meet – Authentic x Silex - Session 2 - Camera and microphone recording"
        " - Google Chrome - Mickael",
        "Google Chrome",
    )
    assert h is not None
    assert h.provider == "meet"
    assert h.phase == "in_call"
    assert "Authentic" in (h.label or "")

    room = match_meeting_title("meet.google.com/rhf-ejmd-ipg - Google Chrome")
    assert room is not None and room.phase == "in_call"
    assert room.call_id == "rhf-ejmd-ipg"
    assert match_meeting_title("meet.google.com is sharing your screen.") is not None
    bare = match_meeting_title("Google Meet - Google Chrome")
    assert bare is not None and bare.phase == "tab_open"
    nbsp = match_meeting_title("Google\xa0Meet - Google Chrome - Mickael")
    assert nbsp is not None and nbsp.phase == "tab_open"
    landing = match_meeting_title("meet.google.com/landing - Google Chrome")
    assert landing is not None and landing.phase == "tab_open"
    assert match_meeting_title("Zoom Meeting - My Call") is not None
    assert match_meeting_title("Standup | Microsoft Teams") is not None

    assert match_meeting_title("Claap Arman extraction max data reunion - grok") is None
    assert match_meeting_title("docs - Google Chrome") is None
    assert match_meeting_title("let's meet later - notes") is None
    assert match_meeting_title("ADULT TIME - Audio playing - Google Chrome") is None


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


def test_annotate_from_in_call_session_overlap() -> None:
    away = [
        AwaySegment(
            start="2026-07-31T08:59:17Z",
            end="2026-07-31T09:30:22Z",
            duration_s=1865.0,
            mode="wayland-idle",
        )
    ]
    sessions = [
        MeetingSession(
            start="2026-07-31T08:50:00Z",
            end="2026-07-31T09:35:00Z",
            duration_s=2700.0,
            provider="meet",
            label="Authentic x Silex",
            phase="in_call",
            call_id=None,
        )
    ]
    out = annotate_away_with_meetings(away, sessions)
    assert out[0].presence == "meeting"
    assert out[0].meeting_provider == "meet"
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
    out = annotate_away_with_meetings(away, [])
    assert out[0].presence == "away"
    assert out[0].meeting_label is None


def test_annotate_ignores_tab_open_only() -> None:
    away = [
        AwaySegment(
            start="2026-08-03T10:30:00Z",
            end="2026-08-03T10:40:00Z",
            duration_s=600.0,
            mode="wayland-idle",
        )
    ]
    sessions = [
        MeetingSession(
            start="2026-08-03T10:20:00Z",
            end="2026-08-03T10:45:00Z",
            duration_s=1500.0,
            provider="meet",
            label="landing",
            phase="tab_open",
        )
    ]
    out = annotate_away_with_meetings(away, sessions)
    assert out[0].presence == "away"


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
                        "title": _MEET_AUTH,
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
    assert recap.away_total_s >= 14 * 60
    assert any(a.presence == "meeting" for a in recap.away_segments)
    assert any(a.presence == "away" for a in recap.away_segments)
    assert any(m.phase == "in_call" for m in recap.meeting_sessions)

    text = format_day_recap(recap)
    assert "meeting=" in text
    assert "Idle gaps" in text
    assert "Meetings" in text
    assert "in_call" in text
    hour_labels = {app for _, apps in recap.hour_apps for app, _ in apps}
    assert "meeting" in hour_labels


def test_meeting_total_contract(tmp_path: Path) -> None:
    """AC3: meeting_total_s ≡ Σ in_call; tab_open excluded."""
    db = tmp_path / "sense.db"
    with Store(db) as store:
        store.append(
            "desktop_snapshot",
            {
                "windows": [
                    {"app": "Google Chrome", "title": _MEET_IN_CALL, "active": True}
                ]
            },
            ts="2026-08-03T09:00:00Z",
        )
        store.append(
            "desktop_snapshot",
            {
                "windows": [
                    {
                        "app": "Google Chrome",
                        "title": "meet.google.com/landing - Google Chrome",
                        "active": True,
                    }
                ]
            },
            ts="2026-08-03T10:00:00Z",
        )
        store.append(
            "desktop_snapshot",
            {"windows": [{"app": "ghostty", "title": "done", "active": True}]},
            ts="2026-08-03T10:10:00Z",
        )
        recap = compile_day_recap(
            store,
            "2026-08-03",
            now=datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC),
        )

    in_call_sum = sum(
        m.duration_s for m in recap.meeting_sessions if m.phase == "in_call"
    )
    tab_sum = sum(m.duration_s for m in recap.meeting_sessions if m.phase == "tab_open")
    assert recap.meeting_total_s == in_call_sum
    assert recap.meeting_tab_open_s == tab_sum
    assert recap.meeting_total_s >= 50 * 60
    assert recap.meeting_tab_open_s >= 5 * 60
    assert "tab_open" in format_day_recap(recap)


def test_meeting_sessions_survive_multitask_and_split_tab_open() -> None:
    """Desktop keeps Meet in background; focus elsewhere does not end in_call."""
    events = [
        Event(
            id=1,
            ts="2026-08-03T09:01:44Z",
            kind="focus",
            payload={
                "app": "Google Chrome",
                "title": "meet.google.com/qwb-fxnf-dje - Google Chrome",
            },
        ),
        Event(
            id=2,
            ts="2026-08-03T09:03:40Z",
            kind="desktop_snapshot",
            payload={
                "windows": [
                    {
                        "app": "Google Chrome",
                        "title": _MEET_IN_CALL,
                        "active": False,
                    },
                    {"app": "ghostty", "title": "work - grok", "active": True},
                ]
            },
        ),
        Event(
            id=3,
            ts="2026-08-03T09:20:00Z",
            kind="focus",
            payload={"app": "Discord", "title": "#questions"},
        ),
        Event(
            id=4,
            ts="2026-08-03T10:12:40Z",
            kind="desktop_snapshot",
            payload={
                "windows": [
                    {
                        "app": "Google Chrome",
                        "title": _MEET_IN_CALL,
                        "active": False,
                    }
                ]
            },
        ),
        Event(
            id=5,
            ts="2026-08-03T10:24:40Z",
            kind="desktop_snapshot",
            payload={
                "windows": [
                    {
                        "app": "Google Chrome",
                        "title": "Google Meet - Google Chrome",
                        "active": True,
                    }
                ]
            },
        ),
        Event(
            id=6,
            ts="2026-08-03T10:33:40Z",
            kind="desktop_snapshot",
            payload={
                "windows": [
                    {
                        "app": "Google Chrome",
                        "title": "Google Meet - Google Chrome",
                        "active": False,
                    }
                ]
            },
        ),
    ]
    sessions = meeting_sessions(
        events,
        horizon=datetime(2026, 8, 3, 11, 0, 0, tzinfo=UTC),
    )
    assert len(sessions) >= 2
    in_call = [s for s in sessions if s.phase == "in_call"]
    tab = [s for s in sessions if s.phase == "tab_open"]
    assert len(in_call) == 1
    assert in_call[0].call_id == "qwb-fxnf-dje"
    assert in_call[0].duration_s >= 80 * 60
    assert in_call[0].duration_s <= 85 * 60
    assert tab
    assert tab[0].duration_s >= 8 * 60


def test_desktop_without_meeting_ends_session() -> None:
    """AC2: non-meeting desktop inventory hard-clears in_call (not horizon bleed)."""
    events = [
        Event(
            id=1,
            ts="2026-08-03T09:00:00Z",
            kind="desktop_snapshot",
            payload={
                "windows": [
                    {"app": "Google Chrome", "title": _MEET_IN_CALL, "active": True}
                ]
            },
        ),
        Event(
            id=2,
            ts="2026-08-03T09:10:00Z",
            kind="desktop_snapshot",
            payload={
                "windows": [{"app": "ghostty", "title": "solo", "active": True}]
            },
        ),
    ]
    sessions = meeting_sessions(
        events,
        horizon=datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC),
    )
    assert len(sessions) == 1
    assert sessions[0].phase == "in_call"
    assert sessions[0].end == "2026-08-03T09:10:00Z"
    assert sessions[0].duration_s == 600.0


def test_call_id_upgrade_does_not_split() -> None:
    """Spec edge: None→call_id upgrades; known different call_id splits."""
    h0 = MeetingHint(
        provider="meet",
        label="Meet – join",
        title="Meet – join - Camera and microphone recording",
        phase="in_call",
        call_id=None,
    )
    h1 = MeetingHint(
        provider="meet",
        label="meet.google.com/abc-defg-hij",
        title="meet.google.com/abc-defg-hij",
        phase="in_call",
        call_id="abc-defg-hij",
    )
    h2 = MeetingHint(
        provider="meet",
        label="meet.google.com/xxx-yyyy-zzz",
        title="meet.google.com/xxx-yyyy-zzz",
        phase="in_call",
        call_id="xxx-yyyy-zzz",
    )
    t0 = datetime(2026, 8, 3, 10, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 8, 3, 10, 5, 0, tzinfo=UTC)
    t2 = datetime(2026, 8, 3, 10, 10, 0, tzinfo=UTC)
    horizon = datetime(2026, 8, 3, 10, 20, 0, tzinfo=UTC)
    one = sessions_from_samples([(t0, h0), (t1, h1)], horizon=horizon)
    assert len(one) == 1
    assert one[0].call_id == "abc-defg-hij"
    assert one[0].duration_s == 20 * 60
    two = sessions_from_samples([(t0, h1), (t2, h2)], horizon=horizon)
    assert len(two) == 2
    assert two[0].call_id == "abc-defg-hij"
    assert two[1].call_id == "xxx-yyyy-zzz"


def test_in_call_window_wins_over_active_tab_open() -> None:
    wins = [
        {
            "app": "Google Chrome",
            "title": "Google Meet - Google Chrome",
            "active": True,
        },
        {
            "app": "Google Chrome",
            "title": _MEET_IN_CALL,
            "active": False,
        },
    ]
    h = meeting_hint_from_windows(wins)
    assert h is not None
    assert h.phase == "in_call"
