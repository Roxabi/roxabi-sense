"""Cross-source report regressions through a real store and Tern proof writer."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from test_tern_consumer_r1 import APP, DAY, Harness

from roxabi_sense.query import SenseQuery
from roxabi_sense.store import Store
from roxabi_sense.util.time import parse_ts, to_z

SOURCE = "11111111-1111-4111-8111-111111111111"
EPOCH = "33333333-3333-4333-8333-333333333333"
TAB = "44444444-4444-4444-8444-444444444444"


def _recap(db: Path, monkeypatch: pytest.MonkeyPatch, horizon) -> dict:
    monkeypatch.setattr("roxabi_sense.report.day.horizon_dt", lambda end, now: horizon)
    return SenseQuery(db_path=db, offline_threshold_s=120, idle_threshold_s=300).day_recap(
        DAY, detail="segments"
    )


def test_tern_context_and_observed_meeting_share_focus_without_double_counting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "sense.db"
    with Store(db) as store:
        start = parse_ts(store.day_bounds(DAY)[0]) + timedelta(hours=12)
        h = Harness(store, monkeypatch, start)
        store.append("meeting_sources", {
            "v": 1, "scope": "chrome_google_meet", "sources": [SOURCE],
        }, ts=to_z(start - timedelta(seconds=1)))
        h.poll()
        for _ in range(4):
            h.advance(5)
            h.poll()
        for seq, (second, state) in enumerate(
            ((2, "joined"), (7, "joined"), (12, "joined"), (17, "not_joined"))
        ):
            store.append("meeting_snapshot", {
                "v": 1, "scope": "chrome_google_meet", "source_id": SOURCE,
                "epoch": EPOCH, "seq": seq, "complete": True,
                "tabs": [{"tab_id": TAB, "state": state,
                          "basis": "leave_control" if state == "joined" else "home"}],
            }, ts=to_z(start + timedelta(seconds=second)))

    recap = _recap(db, monkeypatch, start + timedelta(seconds=20))
    assert recap["events_truncated"] is False
    assert dict(recap["time_by_app"])[APP] == 20
    assert dict(recap["time_by_repo"])["repo"] == 20
    assert recap["meeting_total_s"] == 15
    assert recap["meeting_inferred_s"] == 0
    assert recap["focus_in_meeting_s"] == 15
    assert recap["focus_outside_meeting_s"] == 5
    assert recap["activity_union_s"] == 20


def test_suffix_tern_proofs_cannot_extend_truncated_focus_or_promote_desktop_meeting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "sense.db"
    with Store(db) as store:
        start = parse_ts(store.day_bounds(DAY)[0]) + timedelta(hours=12)
        h = Harness(store, monkeypatch, start)
        h.poll()
        h.advance(10)
        # A native title transition publishes a fresh focus witness in the prefix.
        h.title = "renamed shell"
        h.poll()
        store.append("desktop_snapshot", {
            "windows": [
                {"app": "Google Chrome", "title": "meet.google.com/abc-defg-hij", "active": True},
                {"app": "editor", "title": "work", "active": False},
            ], "inventory": "full", "source": "atspi",
        }, ts=to_z(start + timedelta(seconds=12)))
        store.append("desktop_snapshot", {
            "windows": [
                {"app": "editor", "title": "work", "active": True},
                {"app": "other", "title": "idle", "active": False},
            ], "inventory": "full", "source": "atspi",
        }, ts=to_z(start + timedelta(seconds=22)))
        store.append("process_snapshot", {}, ts=to_z(start + timedelta(seconds=23)))
        prefix_size = len(store.events_for_day(DAY, kinds=(), limit=1000))
        # Changed pane produces a suffix snapshot and renews live proof/coverage metadata.
        h.advance(20)
        h.pane = "pane-b"
        h.poll()
        h.advance(5)
        h.poll()

    monkeypatch.setattr("roxabi_sense.report.day._DAY_EVENT_LIMIT", prefix_size)
    recap = _recap(db, monkeypatch, start + timedelta(seconds=100))
    assert recap["events_truncated"] is True
    assert recap["focus_coverage_quality"] == "unknown"
    assert sum(segment["duration_s"] for segment in recap["focus_segments"]) == 10
    assert max(segment["end"] for segment in recap["focus_segments"]) == to_z(
        start + timedelta(seconds=10)
    )
    assert recap["current_attention"]["quality"] == "unknown"
    assert recap["current_attention"]["stretch"] is None
    assert recap["meeting_total_s"] == 0
    assert recap["meeting_inferred_s"] == 10
    assert recap["meeting_quality"] == "inferred"
    assert recap["focus_in_meeting_s"] == 0
    assert recap["focus_outside_meeting_s"] == 10
    assert recap["activity_union_s"] == 10
