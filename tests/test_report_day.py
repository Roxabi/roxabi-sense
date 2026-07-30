"""Day recap compilation tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from roxabi_sense.cli import main
from roxabi_sense.report.day import compile_day_recap, format_day_recap
from roxabi_sense.store import Store


def _seed(store: Store) -> None:
    # Local day 2026-07-30 in Europe/… — use explicit Z times inside that day.
    store.append(
        "focus",
        {
            "app": "ghostty",
            "title": "roxabi-sense - grok",
            "agent": {
                "agent": "grok",
                "session_id": "abc",
                "cwd": "/home/mickael/projects/roxabi-sense",
            },
        },
        ts="2026-07-30T10:00:00Z",
    )
    store.append(
        "focus",
        {
            "app": "ghostty",
            "title": "roxabi-sense - grok",
            "agent": {
                "agent": "grok",
                "session_id": "abc",
                "cwd": "/home/mickael/projects/roxabi-sense",
            },
        },
        ts="2026-07-30T10:00:05Z",
    )
    # desktop activity keeps degraded-away from cutting mid-session dwell
    for m in (2, 4, 6, 8):
        store.append(
            "desktop_snapshot",
            {"windows": [], "focus": {"app": "ghostty"}},
            ts=f"2026-07-30T10:0{m}:00Z",
        )
    store.append(
        "focus",
        {"app": "Google Chrome", "title": "docs"},
        ts="2026-07-30T10:10:00Z",
    )
    for m in (12, 14, 16, 18):
        store.append(
            "desktop_snapshot",
            {"windows": [], "focus": {"app": "Google Chrome"}},
            ts=f"2026-07-30T10:{m}:00Z",
        )
    store.append(
        "focus",
        {"app": "Unnamed", "title": "other - grok"},
        ts="2026-07-30T10:20:00Z",
    )
    store.append(
        "agent_sessions_snapshot",
        {
            "count": 2,
            "sessions": [
                {
                    "agent": "grok",
                    "session_id": "abc",
                    "cwd": "/home/mickael/projects/roxabi-sense",
                    "state": "open",
                },
                {"agent": "claude", "state": "history_present"},
            ],
        },
        ts="2026-07-30T10:05:00Z",
    )
    store.append(
        "agent_sessions_snapshot",
        {
            "count": 1,
            "sessions": [
                {
                    "agent": "grok",
                    "session_id": "abc",
                    "cwd": "/home/mickael/projects/roxabi-sense",
                    "state": "open",
                },
            ],
        },
        ts="2026-07-30T11:00:00Z",
    )
    store.append(
        "process_snapshot",
        {"processes": {"slack": {"running": True}, "spotify": {"running": False}}},
        ts="2026-07-30T10:01:00Z",
    )
    store.append(
        "media_snapshot",
        {
            "players": [
                {
                    "player": "spotify",
                    "status": "Playing",
                    "artist": "A",
                    "title": "B",
                }
            ]
        },
        ts="2026-07-30T10:15:00Z",
    )


def test_compile_day_recap_focus_and_repos(tmp_path: Path) -> None:
    db = tmp_path / "sense.db"
    with Store(db) as store:
        _seed(store)
        recap = compile_day_recap(
            store,
            "2026-07-30",
            now=datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC),
        )

    assert recap.kind_counts["focus"] == 4
    apps = dict(recap.time_by_app)
    # Unnamed collapsed into ghostty; dense desktop → real dwell
    assert "ghostty" in apps
    assert apps["ghostty"] >= 500  # ~10 min with desktop heartbeats
    assert "Google Chrome" in apps
    assert apps["Google Chrome"] >= 500
    assert any(s.agent == "grok" for s in recap.agent_sessions)
    assert "slack" in recap.processes_seen
    assert recap.media and recap.media[0].title == "B"

    text = format_day_recap(recap)
    assert "sense recap" in text
    assert "Away" in text
    assert "degraded" in text


def test_degraded_away_cuts_focus_attribution(tmp_path: Path) -> None:
    """Gap ≥5 min after last activity → away from last activity, not last app."""
    db = tmp_path / "sense.db"
    with Store(db) as store:
        store.append(
            "focus",
            {"app": "Google Chrome", "title": "Ether"},
            ts="2026-07-30T16:47:00Z",
        )
        # silence 105 min then focus again
        store.append(
            "focus",
            {"app": "ghostty", "title": "back - grok"},
            ts="2026-07-30T18:32:00Z",
        )
        recap = compile_day_recap(
            store,
            "2026-07-30",
            now=datetime(2026, 7, 30, 19, 0, 0, tzinfo=UTC),
        )
    assert recap.idle_mode == "degraded-gap"
    assert recap.away_total_s >= 100 * 60
    apps = dict(recap.time_by_app)
    # Chrome must not absorb the 105 min hole
    assert apps.get("Google Chrome", 0) < 60
    assert any(a.duration_s >= 100 * 60 for a in recap.away_segments)


def test_dense_desktop_keeps_presence(tmp_path: Path) -> None:
    """desktop_snapshot counts as activity — no false away mid-session."""
    db = tmp_path / "sense.db"
    with Store(db) as store:
        store.append(
            "focus",
            {"app": "ghostty", "title": "work - grok"},
            ts="2026-07-30T10:00:00Z",
        )
        # heartbeats every 2 min via desktop_snapshot for 12 min
        for i in range(1, 7):
            store.append(
                "desktop_snapshot",
                {"windows": [], "focus": {"app": "ghostty"}},
                ts=f"2026-07-30T10:{i * 2:02d}:00Z",
            )
        store.append(
            "focus",
            {"app": "slack", "title": "chat"},
            ts="2026-07-30T10:14:00Z",
        )
        recap = compile_day_recap(
            store,
            "2026-07-30",
            now=datetime(2026, 7, 30, 11, 0, 0, tzinfo=UTC),
        )
    apps = dict(recap.time_by_app)
    assert apps.get("ghostty", 0) >= 13 * 60  # ~14 min presence
    # no away inside the dense window
    mid_aways = [
        a
        for a in recap.away_segments
        if a.start >= "2026-07-30T10:00:00Z" and a.end <= "2026-07-30T10:14:00Z"
    ]
    assert mid_aways == []


def test_recap_cli(tmp_path: Path, monkeypatch, capsys) -> None:
    db = tmp_path / "sense.db"
    monkeypatch.setenv("SENSE_DB", str(db))
    with Store(db) as store:
        _seed(store)
    assert main(["recap", "--date", "2026-07-30"]) == 0
    out = capsys.readouterr().out
    assert "sense recap" in out
    assert "2026-07-30" in out


def test_recap_cli_json(tmp_path: Path, monkeypatch, capsys) -> None:
    db = tmp_path / "sense.db"
    monkeypatch.setenv("SENSE_DB", str(db))
    with Store(db) as store:
        _seed(store)
    assert main(["recap", "--date", "2026-07-30", "--json"]) == 0
    out = capsys.readouterr().out
    assert '"day": "2026-07-30"' in out
    assert "time_by_app" in out


def test_recap_missing_db(tmp_path: Path, monkeypatch, capsys) -> None:
    missing = tmp_path / "nope.db"
    monkeypatch.setenv("SENSE_DB", str(missing))
    assert main(["recap"]) == 1
    assert "db: missing" in capsys.readouterr().err


def test_recap_invalid_date(tmp_path: Path, monkeypatch, capsys) -> None:
    db = tmp_path / "sense.db"
    monkeypatch.setenv("SENSE_DB", str(db))
    Store(db).close()
    assert main(["recap", "--date", "nope"]) == 2
    assert "invalid day" in capsys.readouterr().err
