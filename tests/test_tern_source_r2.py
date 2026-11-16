"""Restart fencing and independently timed Tern source proofs."""
import json
import threading
from datetime import UTC, datetime, timedelta

from roxabi_sense import daemon_loop
from roxabi_sense.collectors.focus import FocusCollector
from roxabi_sense.collectors.focus.ownership import acquire_focus_owner, release_focus_owner
from roxabi_sense.collectors.tern import observation
from roxabi_sense.collectors.tern.collector import TernCollector
from roxabi_sense.config import SenseConfig
from roxabi_sense.daemon import collect_once
from roxabi_sense.store import Store


def _window():
    return {"endpoint_ref": "endpoint", "window_key": "B", "conn": 1, "since": 2,
            "registered": True, "shown_pane": "pane", "panes": [{
                "pane_ref": "pane", "selected": True, "pane_working": True,
                "omp_foreground": True, "omp_working": True,
            }]}


def _meta(store):
    return json.loads(store.get_meta("tern_observation") or "{}")


def _seed(store):
    writer = observation.ObservationWriter()
    writer.publish(store, [_window()])
    return _meta(store)


def _assert_sealed(store, before):
    snapshot = store.last_by_kind("tern_snapshot")
    assert snapshot is not None and snapshot.payload["state"] == "unknown"
    closed = {item["proof_ref"]: item for item in snapshot.payload["closed"]}
    for proof in before["scopes"].values():
        assert closed[proof["proof_ref"]]["started_at"] == proof["started_at"]
        assert closed[proof["proof_ref"]]["confirmed_through"] == proof["last_observed_at"]
    assert _meta(store)["run_ref"] != before["run_ref"]
    assert not _meta(store)["healthy"]


def test_startup_fences_before_blocked_worker(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    source = TernCollector()
    def blocked(_cancel):
        entered.set()
        assert release.wait(5)
        return None
    monkeypatch.setattr(source, "collect", blocked)
    with Store(tmp_path / "db") as store:
        before = _seed(store)
        def boot(*, tern_worker, **_kwargs):
            try:
                assert entered.wait(5)
                _assert_sealed(store, before)
                tern_worker.poll(store)
                _assert_sealed(store, before)
            finally:
                release.set()
        monkeypatch.setattr(daemon_loop, "_run_main_loop", boot)
        daemon_loop.run_main_loop(store=store, focus=object(), tern_collector=source)


def test_owner_once_fences_before_acquisition_and_foreign_once_is_untouched(tmp_path, monkeypatch):
    path = tmp_path / "db"
    with Store(path) as store:
        before = _seed(store)
    source = TernCollector()
    def collect(_cancel):
        with Store(path) as store:
            _assert_sealed(store, before)
        return None
    monkeypatch.setattr(source, "collect", collect)
    focus = FocusCollector()
    monkeypatch.setattr(focus, "tick", lambda _store: 0)
    def select_initial(_self, store):
        _assert_sealed(store, before)
    monkeypatch.setattr("roxabi_sense.collectors.focus.runtime.FocusRuntime.select_initial",
                        select_initial)
    monkeypatch.setattr("roxabi_sense.daemon.build_collectors", lambda cfg: [source, focus])
    monkeypatch.setattr("roxabi_sense.daemon.build_poll_collectors", lambda cfg, logind_idle: [])
    owner = acquire_focus_owner(path)
    assert owner is not None
    try:
        collect_once(SenseConfig(db_path=path, focus=True, idle=False))
        with Store(path) as store:
            assert _meta(store) == before
    finally:
        release_focus_owner(owner)
    collect_once(SenseConfig(db_path=path, focus=True, idle=False))


def test_process_failure_preserves_structural_proofs_and_closes_process(tmp_path, monkeypatch):
    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    clock = [100.0]
    class FakeDateTime:
        @staticmethod
        def now(_tz):
            return now[0]
    monkeypatch.setattr(observation, "datetime", FakeDateTime)
    monkeypatch.setattr(observation, "_clock", lambda: clock[0])
    writer = observation.ObservationWriter()
    with Store(tmp_path / "db") as store:
        writer.publish(store, [_window()])
        first = _meta(store)
        snapshot = store.last_by_kind("tern_snapshot")
        assert snapshot is not None
        pane = snapshot.payload["windows"][0]["panes"][0]
        pane_ref, process_ref = pane["pane_work_proof_ref"], pane["process_proof_ref"]
        clock[0] += 5
        now[0] += timedelta(seconds=5)
        failed = _window()
        failed["panes"][0].update(omp_foreground=None, omp_working=None,
                                  _process_sample=None)
        writer.publish(store, [failed])
        after = _meta(store)
        snapshot = store.last_by_kind("tern_snapshot")
        assert snapshot is not None
        pane = snapshot.payload["windows"][0]["panes"][0]
        assert pane["pane_work_proof_ref"] == pane_ref
        assert "process_proof_ref" not in pane
        ongoing = {proof["proof_ref"]: proof for proof in after["scopes"].values()}
        assert ongoing[pane_ref]["last_observed_at"] != ongoing[pane_ref]["started_at"]
        closed = {proof["proof_ref"]: proof for proof in snapshot.payload["closed"]}
        assert closed[process_ref]["confirmed_through"] == next(
            proof["last_observed_at"] for proof in first["scopes"].values()
            if proof["proof_ref"] == process_ref)
        assert len(closed) == 1
