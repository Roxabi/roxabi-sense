"""Transport-agnostic SenseQuery (MCP / future HTTP)."""

from __future__ import annotations

from pathlib import Path

from roxabi_sense.config import SenseConfig
from roxabi_sense.query import SenseQuery, _redact_obj
from roxabi_sense.store import Store


def test_sense_status_missing_db(tmp_path: Path) -> None:
    q = SenseQuery(db_path=tmp_path / "no.db", offline_threshold_s=120, idle_threshold_s=300)
    body = q.sense_status()
    assert body["db_exists"] is False
    assert body["presence"]["state"] == "offline"


def test_active_now_and_timeline(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    with Store(db) as store:
        store.append(
            "focus",
            {
                "app": "ghostty",
                "title": "SECRET-TITLE",
                "agent": {"agent": "grok", "cwd": "/home/u/proj"},
            },
        )
        store.append(
            "agent_sessions_snapshot",
            {
                "count": 1,
                "sessions": [
                    {
                        "agent": "grok",
                        "session_id": "1",
                        "cwd": "/home/u/proj",
                        "state": "active",
                    }
                ],
            },
        )
        store.set_meta("last_tick", "2026-08-01T12:00:00Z")
    q = SenseQuery(
        db_path=db,
        offline_threshold_s=120,
        idle_threshold_s=300,
        detail="coarse",
    )
    now = q.active_now()
    assert now["db_exists"] is True
    assert now["focus"] is not None
    assert now["focus"]["app"] == "ghostty"
    assert "title" not in now["focus"]  # coarse redaction
    assert now["agent_sessions"]
    assert now["agent_sessions"][0]["cwd"] == "proj"  # basename only
    assert "annotations" in now
    assert now["annotations"]["meeting"]["phase"] is None
    assert "fidelity" in now["annotations"]["meeting"]

    tl = q.what_was_i_doing(limit=10)
    assert tl["count"] >= 1
    assert "events" in tl
    # summaries present, no raw payloads in coarse
    assert "payload" not in tl["events"][0]


def test_redact_strips_titles() -> None:
    out = _redact_obj({"app": "x", "title": "secret", "nested": {"title_raw": "y", "ok": 1}})
    assert out == {"app": "x", "nested": {"ok": 1}}


def test_from_config_mcp_detail(tmp_path: Path) -> None:
    cfg = SenseConfig(db_path=tmp_path / "s.db", mcp_detail="full")
    q = SenseQuery.from_config(cfg)
    assert q.detail == "full"


def test_day_recap_missing(tmp_path: Path) -> None:
    q = SenseQuery(db_path=tmp_path / "no.db", offline_threshold_s=1, idle_threshold_s=1)
    assert q.day_recap()["error"] == "db_missing"


def test_active_now_meeting_annotation_in_call(tmp_path: Path) -> None:
    """ADR-004 live path: open Meet session appears under annotations.meeting."""
    db = tmp_path / "s.db"
    with Store(db) as store:
        store.append(
            "desktop_snapshot",
            {
                "windows": [
                    {
                        "app": "Google Chrome",
                        "title": (
                            "Meet – abc-defg-hij - Camera and microphone recording "
                            "- Google Chrome"
                        ),
                        "active": True,
                    },
                    {"app": "ghostty", "title": "side", "active": False},
                ],
                "inventory": "full",
                "source": "atspi",
            },
            ts="2026-08-03T15:00:00Z",
        )
        store.set_meta("last_tick", "2026-08-03T15:05:00Z")
        store.set_meta("focus_backend", "atspi")
    from datetime import UTC, datetime

    from roxabi_sense.report.meeting_sessions import meeting_annotation_now

    with Store(db) as store:
        ev = store.events_for_day(
            "2026-08-03",
            kinds=("focus", "desktop_snapshot"),
            limit=100,
        )
        ann = meeting_annotation_now(
            ev,
            now=datetime(2026, 8, 3, 15, 30, 0, tzinfo=UTC),
            focus_backend="atspi",
        )
    assert ann["phase"] == "in_call"
    assert ann["provider"] == "meet"
    assert ann["call_id"] == "abc-defg-hij"
    assert ann["fidelity"] == "full"

    # Coarse strips title-derived fields when phase is set
    redacted = _redact_obj(ann)
    assert "label" not in redacted
    assert "call_id" not in redacted
    assert redacted["phase"] == "in_call"


def test_day_recap_coarse_strips_titles_and_media(tmp_path: Path) -> None:
    """ADR-002: top_titles are positional tuples — must not leak under coarse."""
    db = tmp_path / "s.db"
    secret = "SECRET-MEET-TITLE"
    with Store(db) as store:
        store.append(
            "focus",
            {"app": "Google Chrome", "title": secret, "active": True},
        )
        store.append(
            "media_snapshot",
            {
                "players": [
                    {
                        "player": "spotify",
                        "artist": "X",
                        "title": "SECRET-SONG",
                        "status": "Playing",
                    }
                ]
            },
        )
        store.set_meta("last_tick", "2026-08-01T12:00:00Z")
    q = SenseQuery(db_path=db, offline_threshold_s=120, idle_threshold_s=300, detail="coarse")
    body = q.day_recap()
    blob = str(body)
    assert secret not in blob
    assert "SECRET-SONG" not in blob
    assert body.get("top_titles") == []
    assert body.get("media") == []
    # ADR-004 sessions: label/call_id are title-derived — coarse must strip
    for sess in body.get("meeting_sessions") or []:
        assert "label" not in sess
        assert "call_id" not in sess


def test_invalid_day_stable_error(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    Store(db).close()
    q = SenseQuery(db_path=db, offline_threshold_s=1, idle_threshold_s=1)
    out = q.what_was_i_doing(day="not-a-date")
    assert out["error"] == "invalid_day"

