"""Scoped proof lifetimes are health metadata, not focus heartbeats."""
import json
from datetime import UTC, datetime, timedelta

from roxabi_sense.collectors.tern import observation
from roxabi_sense.store import Store


def window():
    return {
        "window_key": "", "conn": 1, "since": 2, "registered": True, "shown_pane": "pane",
        "panes": [
            {"pane_ref": "pane", "selected": True, "pane_working": True, "omp_foreground": True},
        ],
    }


def observation_meta(store):
    raw = store.get_meta("tern_observation")
    assert raw is not None
    return json.loads(raw)


def test_unchanged_observations_renew_only_meta_and_gap_closes_last_observation(
    tmp_path, monkeypatch
):
    clock = [100.0]
    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    class FakeDateTime:
        @staticmethod
        def now(_tz):
            return now[0]
    monkeypatch.setattr(observation, "_clock", lambda: clock[0])
    monkeypatch.setattr(observation, "datetime", FakeDateTime)
    writer = observation.ObservationWriter(freshness_s=10)
    with Store(tmp_path / "db") as store:
        assert writer.publish(store, [window()]) == 1
        initial = observation_meta(store)
        for _ in range(240):
            clock[0] += 5
            now[0] += timedelta(seconds=5)
            assert writer.publish(store, [window()]) == 0
        renewed = observation_meta(store)
        assert initial["snapshot_id"] == renewed["snapshot_id"]
        assert {x["proof_ref"] for x in initial["scopes"].values()} == {
            x["proof_ref"] for x in renewed["scopes"].values()}
        clock[0] += 11
        now[0] += timedelta(seconds=11)
        assert writer.publish(store, [window()]) == 1
        event = store.last_by_kind("tern_snapshot")
        assert event is not None
        assert {x["confirmed_through"] for x in event.payload["closed"]} == {
            x["last_observed_at"] for x in renewed["scopes"].values()}


def test_background_change_preserves_selected_proof(tmp_path, monkeypatch):
    monkeypatch.setattr(observation, "_clock", lambda: 100.0)
    writer = observation.ObservationWriter()
    with Store(tmp_path / "db") as store:
        state = window()
        writer.publish(store, [state])
        prior = observation_meta(store)["scopes"]
        state["panes"].append({"pane_ref": "other", "selected": False,
                               "pane_working": True, "omp_foreground": True})
        writer.publish(store, [state])
        after = observation_meta(store)["scopes"]
        assert all(after[key]["proof_ref"] == value["proof_ref"] for key, value in prior.items())


def test_same_pane_views_share_one_work_proof(tmp_path, monkeypatch):
    monkeypatch.setattr(observation, "_clock", lambda: 100.0)
    writer = observation.ObservationWriter()
    first = window()
    second = window()
    second["window_key"] = "2"
    second["conn"] = 3
    second["since"] = 4
    with Store(tmp_path / "db") as store:
        writer.publish(store, [first, second])
        event = store.last_by_kind("tern_snapshot")
        assert event is not None
        a, b = event.payload["windows"]
        assert a["panes"][0]["work_proof_ref"] == b["panes"][0]["work_proof_ref"]


def test_lease_renewals_do_not_create_events_but_expiry_breaks_proof(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(observation, "_clock", lambda: clock[0])
    writer = observation.ObservationWriter()
    state = window()
    state["_lease_seconds"] = 8
    with Store(tmp_path / "db") as store:
        writer.publish(store, [state])
        first = observation_meta(store)
        clock[0] += 5
        assert writer.publish(store, [state]) == 0
        renewed = observation_meta(store)
        assert first["snapshot_id"] == renewed["snapshot_id"]
        clock[0] += 8
        assert writer.publish(store, [state]) == 1
        recovered = observation_meta(store)
        assert {value["proof_ref"] for value in first["scopes"].values()} != {
            value["proof_ref"] for value in recovered["scopes"].values()
        }


def test_source_failure_closes_proofs_without_raising(tmp_path, monkeypatch):
    from roxabi_sense.collectors.tern.collector import TernCollector
    from roxabi_sense.collectors.tern.runner import TernUnavailable

    clock = [100.0]
    monkeypatch.setattr(observation, "_clock", lambda: clock[0])
    collector = TernCollector()
    monkeypatch.setattr(collector, "_collect", lambda _cancel: [window()])
    with Store(tmp_path / "db") as store:
        assert collector.tick(store) == 1
        before = observation_meta(store)
        clock[0] += 1

        def fail(_cancel):
            raise TernUnavailable("source failed")

        monkeypatch.setattr(collector, "_collect", fail)
        assert collector.tick(store) == 1
        event = store.last_by_kind("tern_snapshot")
        assert event is not None
        assert event.payload["state"] == "unknown"
        assert {x["confirmed_through"] for x in event.payload["closed"]} == {
            x["last_observed_at"] for x in before["scopes"].values()
        }
