"""Live meeting evidence parity across the store, query and CLI surfaces."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from roxabi_sense.cli import main
from roxabi_sense.query import SenseQuery
from roxabi_sense.store import Store
from roxabi_sense.store.meeting_protocol import CONFLICTS_META, SCOPE
from roxabi_sense.util.time import parse_ts, to_z

DAY = "2026-07-30"
SOURCE = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
EPOCH = "33333333-3333-4333-8333-333333333333"
TAB = "44444444-4444-4444-8444-444444444444"


def _query(db: Path) -> SenseQuery:
    return SenseQuery(db_path=db, offline_threshold_s=120, idle_threshold_s=300)


def _pin_now(monkeypatch: pytest.MonkeyPatch, now: datetime) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)

    monkeypatch.setattr("roxabi_sense.report.meetings.evidence.datetime", FixedDateTime)


def _sources(store: Store, ts: datetime, *sources: str) -> None:
    store.append("meeting_sources", {"v": 1, "scope": SCOPE, "sources": list(sources)}, ts=to_z(ts))


def _snapshot(store: Store, ts: datetime, source: str, seq: int, state: str) -> None:
    store.append(
        "meeting_snapshot",
        {"v": 1, "scope": SCOPE, "source_id": source, "epoch": EPOCH,
         "seq": seq, "complete": True,
         "tabs": [{"tab_id": TAB, "state": state,
                   "basis": "leave_control" if state == "joined" else "home"}]},
        ts=to_z(ts),
    )


def _cli_meeting(
    db: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> dict:
    monkeypatch.setenv("SENSE_DB", str(db))
    assert main(["status", "--json"]) == 0
    output = capsys.readouterr().out
    return json.loads(output[output.index("{"):])["annotations"]["meeting"]


def test_missing_store_has_same_unknown_annotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "missing.db"
    query = _query(db).active_now()["annotations"]["meeting"]
    cli = _cli_meeting(db, monkeypatch, capsys)
    assert cli == query
    assert {key: query[key] for key in ("phase", "inferred_phase", "coverage", "fidelity")} == {
        "phase": "unknown", "inferred_phase": None, "coverage": "unknown", "fidelity": "unknown",
    }
    assert query["fidelity_note"]
    assert not db.exists()


def test_timeline_includes_meeting_summaries_without_identifiers(tmp_path: Path) -> None:
    db = tmp_path / "sense.db"
    with Store(db) as store:
        start = parse_ts(store.day_bounds(DAY)[0])
        _sources(store, start + timedelta(seconds=1), SOURCE)
        _snapshot(store, start + timedelta(seconds=2), SOURCE, 0, "joined")
    timeline = _query(db).what_was_i_doing(DAY)
    rows = {row["kind"]: row for row in timeline["events"]}
    assert rows["meeting_sources"]["summary"] == "scope=chrome_google_meet sources=1"
    assert rows["meeting_snapshot"]["summary"] == "scope=chrome_google_meet complete=True tabs=1"
    assert all("payload" not in row for row in rows.values())
    for private in (SOURCE, EPOCH, TAB):
        assert private not in json.dumps(timeline)


@pytest.mark.parametrize("healthy", [False, True])
def test_registered_conflict_is_shared_and_preserves_healthy_joined(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    healthy: bool,
) -> None:
    db = tmp_path / "sense.db"
    now = datetime(2026, 7, 30, 12, tzinfo=UTC)
    _pin_now(monkeypatch, now)
    with Store(db) as store:
        _sources(store, now - timedelta(seconds=3), *([SOURCE, OTHER] if healthy else [SOURCE]))
        _snapshot(store, now - timedelta(seconds=2), SOURCE, 0, "joined")
        if healthy:
            _snapshot(store, now - timedelta(seconds=1), OTHER, 0, "joined")
        store.set_meta(CONFLICTS_META, json.dumps({"v": 1, "scope": SCOPE, "sources": [SOURCE]}))
    meeting = _query(db).active_now()["annotations"]["meeting"]
    assert _cli_meeting(db, monkeypatch, capsys) == meeting
    assert meeting["phase"] == ("in_call" if healthy else "unknown")
    assert meeting["coverage"] == "unknown"
    assert meeting["fidelity"] == ("partial" if healthy else "unknown")
    assert meeting["reason"] == "source_conflict"
    for private in (SOURCE, OTHER, EPOCH, TAB):
        assert private not in json.dumps(meeting)


@pytest.mark.parametrize(
    "raw", ["not json", '{"v":1,"scope":"chrome_google_meet","sources":["not-a-uuid"]}']
)
def test_malformed_conflict_metadata_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    raw: str,
) -> None:
    db = tmp_path / "sense.db"
    now = datetime(2026, 7, 30, 12, tzinfo=UTC)
    _pin_now(monkeypatch, now)
    with Store(db) as store:
        _sources(store, now - timedelta(seconds=2), SOURCE)
        _snapshot(store, now - timedelta(seconds=1), SOURCE, 0, "joined")
        store.set_meta(CONFLICTS_META, raw)
    meeting = _query(db).active_now()["annotations"]["meeting"]
    assert _cli_meeting(db, monkeypatch, capsys) == meeting
    assert (meeting["phase"], meeting["coverage"], meeting["fidelity"], meeting["reason"]) == (
        "unknown", "unknown", "unknown", "source_conflict",
    )


def test_current_conflict_does_not_rewrite_historical_meeting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "sense.db"
    with Store(db) as store:
        start = parse_ts(store.day_bounds(DAY)[0])
        _sources(store, start - timedelta(seconds=1), SOURCE)
        _snapshot(store, start, SOURCE, 0, "joined")
        _snapshot(store, start + timedelta(seconds=5), SOURCE, 1, "not_joined")
    monkeypatch.setattr(
        "roxabi_sense.report.day.horizon_dt", lambda end, now: start + timedelta(seconds=5)
    )
    before = _query(db).day_recap(DAY, detail="segments")
    with Store(db) as store:
        store.set_meta(CONFLICTS_META, json.dumps({"v": 1, "scope": SCOPE, "sources": [SOURCE]}))
    after = _query(db).day_recap(DAY, detail="segments")
    assert before["meeting_total_s"] == after["meeting_total_s"] == 5
    assert before["meeting_coverage_s"] == after["meeting_coverage_s"] == 5
    assert before["meeting_sessions"] == after["meeting_sessions"]
