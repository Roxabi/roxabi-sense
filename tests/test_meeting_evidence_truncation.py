"""Store-to-query coverage for incomplete all-kind day prefixes."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from roxabi_sense.query import SenseQuery
from roxabi_sense.store import Store
from roxabi_sense.util.time import parse_ts, to_z

DAY = "2026-07-30"
SOURCE = "11111111-1111-4111-8111-111111111111"
EPOCH = "33333333-3333-4333-8333-333333333333"
TAB = "44444444-4444-4444-8444-444444444444"


def test_truncated_focus_stops_at_focus_witness_not_unrelated_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "sense.db"
    with Store(db) as store:
        start = parse_ts(store.day_bounds(DAY)[0]) + timedelta(hours=1)

        def at(seconds: int) -> str:
            return to_z(start + timedelta(seconds=seconds))

        store.append("meeting_sources", {
            "v": 1, "scope": "chrome_google_meet", "sources": [SOURCE],
        }, ts=to_z(start - timedelta(seconds=1)))
        coverage_id = store.append("focus_coverage", {
            "state": "observed", "source": "cosmic", "window_id": "1:1",
        }, ts=at(0))
        focus = {"app": "editor", "title": "work", "source": "cosmic", "window_id": "1:1"}
        store.append("focus", {
            **focus, "_observed_at": at(0), "_native_observed_at": at(0),
            "_native_coverage_id": coverage_id,
        }, ts=at(0))
        for seq, (second, state) in enumerate(
            ((2, "joined"), (8, "joined"), (15, "joined"), (20, "not_joined"))
        ):
            if second == 15:
                store.append("focus", {
                    **focus, "_observed_at": at(10), "_native_observed_at": at(10),
                    "_native_coverage_id": coverage_id,
                }, ts=at(10))
            store.append("meeting_snapshot", {
                "v": 1, "scope": "chrome_google_meet", "source_id": SOURCE,
                "epoch": EPOCH, "seq": seq, "complete": True,
                "tabs": [{"tab_id": TAB, "state": state,
                          "basis": "leave_control" if state == "joined" else "home"}],
            }, ts=at(second))
        store.append("desktop_snapshot", {"windows": []}, ts=at(30))
        store.append("focus_coverage", {
            "state": "unknown", "source": "cosmic", "window_id": "1:1",
        }, ts=at(40))
        store.set_meta("focus_observation", json.dumps({
            "coverage_id": coverage_id, "source": "cosmic", "window_id": "1:1",
            "healthy": True, "last_observed_at": at(90), "fresh_until": at(120),
        }))

    monkeypatch.setattr("roxabi_sense.report.day._DAY_EVENT_LIMIT", 8)
    monkeypatch.setattr(
        "roxabi_sense.report.day.horizon_dt", lambda end, now: start + timedelta(seconds=100)
    )
    recap = SenseQuery(db_path=db, offline_threshold_s=120, idle_threshold_s=300).day_recap(
        DAY, detail="segments",
    )

    assert recap["events_truncated"] is True
    assert recap["focus_coverage_quality"] == "unknown"
    assert [(s["start"], s["end"], s["duration_s"]) for s in recap["focus_segments"]] == [
        (at(0), at(10), 10.0),
    ]
    assert sum(s["duration_s"] for s in recap["attention_segments"]) == 10
    assert dict(recap["time_by_app"])["editor"] == 10
    assert recap["current_attention"]["quality"] == "unknown"
    assert recap["current_attention"]["stretch"] is None
    assert recap["meeting_total_s"] == 18
    assert recap["focus_in_meeting_s"] == 8
    assert recap["focus_outside_meeting_s"] == 2
    assert recap["activity_union_s"] == 20


@pytest.mark.parametrize(
    ("with_focus", "joined_at", "cleared_at", "expected"),
    [(False, 10, 20, 10), (True, 3600, 3660, 60)],
)
def test_truncated_prefix_preserves_closed_desktop_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_focus: bool,
    joined_at: int, cleared_at: int, expected: int,
) -> None:
    db = tmp_path / "sense.db"
    with Store(db) as store:
        start = parse_ts(store.day_bounds(DAY)[0])
        if with_focus:
            store.append("focus", {"app": "editor", "title": "work"}, ts=to_z(start))
        store.append(
            "desktop_snapshot",
            {"windows": [
                {"app": "Google Chrome", "title": "meet.google.com/abc-defg-hij", "active": True},
                {"app": "editor", "title": "work", "active": False},
            ], "inventory": "full", "source": "atspi"},
            ts=to_z(start + timedelta(seconds=joined_at)),
        )
        store.append(
            "desktop_snapshot",
            {"windows": [
                {"app": "editor", "title": "work", "active": True},
                {"app": "other", "title": "idle", "active": False},
            ], "inventory": "full", "source": "atspi"},
            ts=to_z(start + timedelta(seconds=cleared_at)),
        )
        for second in range(cleared_at + 1, cleared_at + 5):
            store.append("process_snapshot", {}, ts=to_z(start + timedelta(seconds=second)))

    monkeypatch.setattr("roxabi_sense.report.day._DAY_EVENT_LIMIT", 4)
    monkeypatch.setattr(
        "roxabi_sense.report.day.horizon_dt",
        lambda end, now: start + timedelta(seconds=cleared_at + 100),
    )
    recap = SenseQuery(db_path=db, offline_threshold_s=120, idle_threshold_s=300).day_recap(
        DAY, detail="segments",
    )
    assert recap["events_truncated"] is True
    assert recap["meeting_total_s"] == 0
    assert recap["meeting_inferred_s"] == expected
    assert recap["meeting_quality"] == "inferred"
    assert recap["focus_coverage_quality"] == "unknown"
    assert "abc-defg-hij" not in json.dumps(recap)
