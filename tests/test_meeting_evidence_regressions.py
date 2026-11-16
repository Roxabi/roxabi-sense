"""Store-to-query regressions for the meeting evidence contract."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from roxabi_sense.query import SenseQuery
from roxabi_sense.store import Store
from roxabi_sense.util.time import parse_ts, to_z

DAY = "2026-07-30"
SOURCE = "11111111-1111-4111-8111-111111111111"
OTHER_SOURCE = "22222222-2222-4222-8222-222222222222"
EPOCH = "33333333-3333-4333-8333-333333333333"
TAB_A = "44444444-4444-4444-8444-444444444444"
TAB_B = "55555555-5555-4555-8555-555555555555"


def _start(store: Store) -> datetime:
    return parse_ts(store.day_bounds(DAY)[0])


def _registry(store: Store, start: datetime, *sources: str) -> None:
    store.append(
        "meeting_sources",
        {"v": 1, "scope": "chrome_google_meet", "sources": list(sources)},
        ts=to_z(start - timedelta(seconds=1)),
    )


def _snapshot(
    store: Store,
    start: datetime,
    second: int,
    seq: int,
    tabs: list[tuple[str, str]],
    *,
    complete: bool = True,
    source: str = SOURCE,
    epoch: str = EPOCH,
) -> None:
    bases = {"joined": "leave_control", "not_joined": "home", "unknown": "no_response"}
    store.append(
        "meeting_snapshot",
        {
            "v": 1,
            "scope": "chrome_google_meet",
            "source_id": source,
            "epoch": epoch,
            "seq": seq,
            "complete": complete,
            "tabs": [{"tab_id": tab, "state": state, "basis": bases[state]}
                     for tab, state in tabs],
        },
        ts=to_z(start + timedelta(seconds=second)),
    )


def _recap(db: Path, monkeypatch: pytest.MonkeyPatch, horizon: datetime) -> dict:
    monkeypatch.setattr("roxabi_sense.report.day.horizon_dt", lambda end, now: horizon)
    return SenseQuery(db_path=db, offline_threshold_s=120, idle_threshold_s=300).day_recap(
        DAY, detail="segments"
    )


def test_overlapping_observed_calls_are_unioned_and_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "sense.db"
    with Store(db) as store:
        start = _start(store)
        _registry(store, start, SOURCE)
        _snapshot(store, start, 0, 0, [(TAB_A, "joined"), (TAB_B, "not_joined")])
        _snapshot(store, start, 5, 1, [(TAB_A, "joined"), (TAB_B, "joined")])
        _snapshot(store, start, 10, 2, [(TAB_A, "not_joined"), (TAB_B, "joined")])
        _snapshot(store, start, 15, 3, [(TAB_A, "not_joined"), (TAB_B, "not_joined")])
        _snapshot(store, start, 20, 4, [])

    recap = _recap(db, monkeypatch, start + timedelta(seconds=20))
    assert recap["meeting_total_s"] == 15
    assert recap["meeting_inferred_s"] == 0
    assert recap["meeting_coverage_s"] == 20
    assert recap["meeting_unknown_s"] == 0
    assert recap["activity_union_s"] == 15
    assert all(s["evidence"] == "observed" for s in recap["meeting_sessions"])
    serialized = json.dumps(recap)
    for private_id in (SOURCE, EPOCH, TAB_A, TAB_B):
        assert private_id not in serialized


@pytest.mark.parametrize("interruption", ["unknown", "incomplete", "stale", "epoch"])
def test_observation_interruption_never_bridges_joined_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interruption: str
) -> None:
    db = tmp_path / "sense.db"
    with Store(db) as store:
        start = _start(store)
        _registry(store, start, SOURCE)
        _snapshot(store, start, 0, 0, [(TAB_A, "joined")])
        _snapshot(store, start, 5, 1, [(TAB_A, "joined")])
        if interruption == "unknown":
            _snapshot(store, start, 10, 2, [(TAB_A, "unknown")])
            _snapshot(store, start, 15, 3, [(TAB_A, "joined")])
            _snapshot(store, start, 20, 4, [(TAB_A, "not_joined")])
        elif interruption == "incomplete":
            _snapshot(store, start, 10, 2, [], complete=False)
            _snapshot(store, start, 15, 3, [(TAB_A, "joined")])
            _snapshot(store, start, 20, 4, [(TAB_A, "not_joined")])
        elif interruption == "stale":
            _snapshot(store, start, 15, 2, [(TAB_A, "joined")])
            _snapshot(store, start, 20, 3, [(TAB_A, "not_joined")])
        else:
            _snapshot(store, start, 15, 0, [(TAB_A, "joined")], epoch=OTHER_SOURCE)
            _snapshot(store, start, 20, 1, [(TAB_A, "not_joined")], epoch=OTHER_SOURCE)

    recap = _recap(db, monkeypatch, start + timedelta(seconds=20))
    assert recap["meeting_total_s"] == 10
    assert recap["meeting_unknown_s"] > 0
    assert recap["meeting_quality"] == "partial"


def test_missing_registered_source_is_unknown_not_zero_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "sense.db"
    with Store(db) as store:
        start = _start(store)
        _registry(store, start, SOURCE, OTHER_SOURCE)
        _snapshot(store, start, 0, 0, [(TAB_A, "joined")])
        _snapshot(store, start, 5, 1, [(TAB_A, "not_joined")])
        _snapshot(store, start, 10, 2, [])

    recap = _recap(db, monkeypatch, start + timedelta(seconds=10))
    assert recap["meeting_total_s"] == 5
    assert recap["meeting_coverage_s"] == 0
    assert recap["meeting_unknown_s"] == 10
    assert recap["meeting_quality"] == "partial"


def test_legacy_title_is_inferred_and_excludes_observed_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "sense.db"
    with Store(db) as store:
        start = _start(store)
        _registry(store, start, SOURCE)
        store.append(
            "desktop_snapshot",
            {"windows": [{"app": "Google Chrome", "active": True,
                          "title": "Meet – abc-defg-hij - Camera and microphone recording"}],
             "inventory": "full", "source": "atspi"},
            ts=to_z(start),
        )
        _snapshot(store, start, 0, 0, [(TAB_A, "joined")])
        _snapshot(store, start, 5, 1, [(TAB_A, "joined")])
        _snapshot(store, start, 10, 2, [(TAB_A, "not_joined")])

    recap = _recap(db, monkeypatch, start + timedelta(seconds=310))
    assert recap["meeting_total_s"] == 10
    assert recap["meeting_inferred_s"] == 290
    assert "abc-defg-hij" not in json.dumps(recap)
