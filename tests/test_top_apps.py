"""top_apps aggregate + session_shape heuristic (#47 / #48)."""

from __future__ import annotations

from pathlib import Path

from roxabi_sense.query import SenseQuery
from roxabi_sense.report.segments import AwaySegment, FocusSegment
from roxabi_sense.report.top_apps import session_shape, top_apps
from roxabi_sense.store import Store


def _seg(app: str, start: str, end: str, duration_s: float) -> FocusSegment:
    return FocusSegment(
        start=start, end=end, duration_s=duration_s, app=app, title=f"{app}-t"
    )


def test_top_apps_ranked_with_minutes() -> None:
    segs = [
        _seg("chrome", "2026-07-30T10:00:00Z", "2026-07-30T10:30:00Z", 1800.0),
        _seg("ghostty", "2026-07-30T10:30:00Z", "2026-07-30T11:00:00Z", 1800.0),
        _seg("chrome", "2026-07-30T11:00:00Z", "2026-07-30T11:15:00Z", 900.0),
    ]
    apps = top_apps(segs)
    assert apps[0].app == "chrome"
    assert apps[0].seconds == 2700.0
    assert apps[0].minutes == 45.0
    assert apps[0].share == 0.6
    assert apps[1].app == "ghostty"
    assert apps[1].minutes == 30.0


def test_session_shape_insufficient() -> None:
    segs = [_seg("a", "2026-07-30T10:00:00Z", "2026-07-30T10:05:00Z", 300.0)]
    assert session_shape(segs) is None


def test_session_shape_deep() -> None:
    # two long blocks, few switches, ≥15m tracked
    segs = [
        _seg("ghostty", "2026-07-30T10:00:00Z", "2026-07-30T10:20:00Z", 1200.0),
        _seg("ghostty", "2026-07-30T10:20:00Z", "2026-07-30T10:40:00Z", 1200.0),
    ]
    assert session_shape(segs) == "deep"


def test_session_shape_fragmented() -> None:
    segs = [
        _seg(f"app{i}", f"2026-07-30T10:{i:02d}:00Z", f"2026-07-30T10:{i:02d}:30Z", 30.0)
        for i in range(20)
    ]
    # 20 * 30s = 600s — below 900 min threshold → None
    assert session_shape(segs) is None
    # extend durations to clear 15m threshold with short median
    segs = [
        _seg(f"app{i}", f"2026-07-30T10:{i:02d}:00Z", f"2026-07-30T10:{i:02d}:50Z", 50.0)
        for i in range(20)
    ]
    # 1000s, median 50s < 120 → fragmented
    assert session_shape(segs) == "fragmented"


def test_session_shape_drifted() -> None:
    segs = [
        _seg("a", "2026-07-30T10:00:00Z", "2026-07-30T10:20:00Z", 1200.0),
        _seg("b", "2026-07-30T12:00:00Z", "2026-07-30T12:20:00Z", 1200.0),
    ]
    away = [
        AwaySegment(
            start="2026-07-30T10:20:00Z",
            end="2026-07-30T12:00:00Z",
            duration_s=6000.0,
            mode="wayland-idle",
            presence="away",
        )
    ]
    assert session_shape(segs, away) == "drifted"


def test_day_recap_includes_top_apps(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    with Store(db) as store:
        store.append(
            "focus",
            {"app": "ghostty", "title": "x", "source": "atspi"},
            ts="2026-07-30T10:00:00Z",
        )
        for m in (2, 4, 6, 8):
            store.append(
                "desktop_snapshot",
                {"windows": [], "focus": {"app": "ghostty"}},
                ts=f"2026-07-30T10:0{m}:00Z",
            )
        store.append(
            "focus",
            {"app": "chrome", "title": "y", "source": "x11"},
            ts="2026-07-30T10:10:00Z",
        )
        for m in (12, 14, 16, 18):
            store.append(
                "desktop_snapshot",
                {"windows": [], "focus": {"app": "chrome"}},
                ts=f"2026-07-30T10:{m}:00Z",
            )
    q = SenseQuery(
        db_path=db,
        offline_threshold_s=600.0,
        idle_threshold_s=300.0,
        detail="coarse",
    )
    body = q.top_apps("2026-07-30")
    assert body["db_exists"] is True
    assert "apps" in body
    assert body["apps"]
    assert "seconds" in body["apps"][0]
    assert "minutes" in body["apps"][0]
    # coarse: no titles in this endpoint
    assert "title" not in body["apps"][0]
    recap = q.day_recap("2026-07-30")
    assert "top_apps" in recap
    assert "session_shape" in recap
