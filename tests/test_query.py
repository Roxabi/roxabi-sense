"""Transport-agnostic SenseQuery (MCP / future HTTP)."""

from __future__ import annotations

import json
from pathlib import Path

from roxabi_sense.config import SenseConfig
from roxabi_sense.query import SenseQuery, _redact_obj
from roxabi_sense.store import Store
from roxabi_sense.util.time import utc_now_z


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
    out = _redact_obj(
        {
            "app": "x",
            "title": "secret",
            "pane_title": "p",
            "terminal_title": "t",
            "terminal_title_stripped": "s",
            "nested": {"title_raw": "y", "ok": 1},
        }
    )
    assert out == {"app": "x", "nested": {"ok": 1}}


def test_redact_coarse_strips_positional_titles() -> None:
    secret = "SECRET-MEET-TITLE"
    out = _redact_obj(
        {
            "top_titles": [(secret, 12.0, "Google Chrome")],
            "nested": [[secret, 3, "slack"]],
            "top_apps": [{"app": "chrome", "seconds": 1.0}],
        }
    )
    blob = str(out)
    assert secret not in blob
    assert out["top_titles"][0][1] == 12.0
    assert out["top_titles"][0][2] == "Google Chrome"
    assert out["nested"][0][2] == "slack"
    assert out["top_apps"][0]["seconds"] == 1.0


def test_cap_json_bytes_sets_truncated() -> None:
    from roxabi_sense.report.event_summary import cap_json_bytes

    fat = {
        "top_apps": [{"app": "x" * 80, "minutes": 1} for _ in range(40)],
        "signals": ["sig"] * 40,
    }
    out = cap_json_bytes(fat, max_bytes=256)
    assert out["truncated"] is True
    assert len(json.dumps(out).encode()) <= 256


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
                            "Meet – abc-defg-hij - Camera and microphone recording - Google Chrome"
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
        for i, app in enumerate(("Google Chrome", "ghostty", "slack", "Google Chrome", "ghostty")):
            store.append(
                "focus",
                {"app": app, "title": secret, "active": True, "pid": 11 + i},
                ts=f"2026-08-01T12:{i:02d}:00Z",
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
            ts="2026-08-01T12:05:00Z",
        )
        store.set_meta("last_tick", "2026-08-01T12:00:00Z")
    q = SenseQuery(db_path=db, offline_threshold_s=120, idle_threshold_s=300, detail="coarse")
    body = q.day_recap("2026-08-01")
    blob = str(body)
    assert secret not in blob
    assert "SECRET-SONG" not in blob
    assert body.get("top_titles") == []
    assert body.get("media") == []
    assert body.get("focus_segments")
    # ADR-004 sessions: label/call_id are title-derived — coarse must strip
    for sess in body.get("meeting_sessions") or []:
        assert "label" not in sess
        assert "call_id" not in sess
    for seg in body["focus_segments"]:
        assert "title" not in seg
        assert "pid" not in seg
        assert "cwd" not in seg
        assert "agent_pid" not in seg


def test_invalid_day_stable_error(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    Store(db).close()
    q = SenseQuery(db_path=db, offline_threshold_s=1, idle_threshold_s=1)
    out = q.what_was_i_doing(day="not-a-date")
    assert out["error"] == "invalid_day"


def _nested_keys(obj: object) -> set[str]:
    if isinstance(obj, dict):
        keys = set(obj)
        for v in obj.values():
            keys |= _nested_keys(v)
        return keys
    if isinstance(obj, list):
        keys: set[str] = set()
        for x in obj:
            keys |= _nested_keys(x)
        return keys
    return set()


def test_care_brief_small_and_private(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    secret = "SECRET-WIN-TITLE"
    with Store(db) as store:
        store.set_meta("last_tick", "2026-08-01T18:00:00Z")
        store.set_meta("idle_watch", "ready")
        store.set_meta("session_bound", "1")
        for i in range(120):
            minute = i % 60
            hour = 12 + i // 60
            store.append(
                "focus",
                {
                    "app": "ghostty" if i % 2 == 0 else "slack",
                    "title": secret,
                    "pid": 100 + i,
                    "active": True,
                },
                ts=f"2026-08-01T{hour:02d}:{minute:02d}:00Z",
            )
    q = SenseQuery(db_path=db, offline_threshold_s=120, idle_threshold_s=300)
    body = q.care_brief("2026-08-01")
    blob = json.dumps(body)
    assert len(blob.encode()) < 8192
    assert secret not in blob
    keys = _nested_keys(body)
    assert "title" not in keys
    assert "top_titles" not in keys
    assert "focus_segments" not in keys
    assert "attention_segments" not in keys
    assert body.get("db_exists") is True
    assert body["top_apps"]
    assert "shape" in body
    assert "signals" in body
    default = q.day_recap("2026-08-01")
    assert default is not body
    assert "session_shape" in default
    assert "focus_segments" in default
    assert default["top_apps"]
    assert "seconds" in default["top_apps"][0]
    assert "shape" not in default
    segs = q.day_recap("2026-08-01", detail="segments")
    assert segs.get("focus_segments")
    assert segs.get("top_titles") == []
    for seg in segs["focus_segments"]:
        assert "title" not in seg
        assert "pid" not in seg
        assert "cwd" not in seg
        assert "agent_pid" not in seg
    assert len(json.dumps(body).encode()) < 8192
    assert len(json.dumps(segs).encode()) > 8192


def test_care_brief_stale_last_ok_is_null(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    with Store(db) as store:
        store.set_meta("last_tick", "2026-08-01T18:00:00Z")
        store.set_meta("idle_watch", "ready")
        store.set_meta("session_bound", "1")
        store.set_meta("agent_sessions_last_ok", "2026-08-01T12:00:00Z")
        store.append(
            "agent_sessions_snapshot",
            {"count": 1, "sessions": [{"agent": "grok", "session_id": "1"}]},
            ts="2026-08-01T12:00:00Z",
        )
    q = SenseQuery(db_path=db, offline_threshold_s=120, idle_threshold_s=300)
    body = q.care_brief()
    assert body.get("agent_sessions") is None
    assert body.get("agent_sessions_reason") == "snapshot_stale"

    with Store(db) as store:
        store.set_meta("agent_sessions_last_ok", utc_now_z())
    fresh = q.care_brief()
    assert fresh.get("agent_sessions") == {"count": 1, "minutes": None}
    assert fresh.get("agent_sessions_reason") is None
