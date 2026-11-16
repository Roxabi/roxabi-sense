"""Regression coverage for Tern source isolation and evidence lifetimes."""
import json
import queue
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from roxabi_sense import daemon_loop
from roxabi_sense.collectors.focus.collector import FocusCollector
from roxabi_sense.collectors.focus.protocol import FocusWindow
from roxabi_sense.collectors.tern import collector as tern_collector
from roxabi_sense.collectors.tern import observation, runner
from roxabi_sense.config import SenseConfig
from roxabi_sense.store import Store
from roxabi_sense.util.time import to_z


def _raw(key="B"):
    return {
        "clients": [{"kind": "window", "window": key, "conn": 1, "since": 2}],
        "sessions": [{"shown": True, "tabs": [{"shown": True, "blocks": [
            {"id": 4, "live": True, "focused": True, "exited": None},
        ]}]}],
        "panes": [{"pane": 4, "identity": {"hops": []}, "cwd": "/repo",
                   "omp_session_file": "/private/session", "working": True,
                   "exited": None}],
    }


def _registration(source, *, expires=115.0):
    return {"endpoint_ref": source.endpoint, "window_key": "B", "conn": 1,
            "since": 2, "token": "a" * 16, "boot_id": "boot",
            "expires_boottime": expires}


def _meta(store):
    return json.loads(store.get_meta("tern_observation") or "{}")


def test_nonempty_registered_route_remains_collectable_after_empty_window_closes(
    tmp_path, monkeypatch
):
    source = tern_collector.TernCollector()
    monkeypatch.setattr(observation, "_clock", lambda: 100.0)
    monkeypatch.setattr(tern_collector, "_stamp", lambda: (to_z(datetime.now(UTC)), 100.0))
    monkeypatch.setattr(tern_collector, "boot_identity", lambda: ("boot", 100.0))
    monkeypatch.setattr(tern_collector, "read_registrations", lambda: [_registration(source)])
    routes = []

    def inspect(key, **_kwargs):
        routes.append(key)
        if key != "B":
            raise runner.TernUnavailable("window no longer exists")
        return _raw()

    monkeypatch.setattr(runner, "inspect", inspect)
    monkeypatch.setattr(runner, "process", lambda key, pane, **_kwargs: {
        "pane": pane, "foreground": {"name": "omp"}})
    with Store(tmp_path / "sense.db") as store:
        source.tick(store)
        event = store.last_by_kind("tern_snapshot")
        assert event is not None and event.payload["state"] == "observed"
        window = event.payload["windows"][0]
        assert window["window_key"] == "B" and window["registered"] is True
        assert window["panes"][0]["omp_foreground"] is True
        assert _meta(store)["scopes"]
        assert "B" in routes


def test_unavailable_registered_route_invalidates_old_proof(tmp_path, monkeypatch):
    source = tern_collector.TernCollector()
    clock = [100.0]
    monkeypatch.setattr(observation, "_clock", lambda: clock[0])
    monkeypatch.setattr(tern_collector, "_stamp", lambda: (to_z(datetime.now(UTC)), clock[0]))
    monkeypatch.setattr(tern_collector, "boot_identity", lambda: ("boot", clock[0]))
    monkeypatch.setattr(tern_collector, "read_registrations", lambda: [_registration(source)])
    monkeypatch.setattr(runner, "inspect", lambda key, **_kwargs: _raw(key))
    monkeypatch.setattr(runner, "process", lambda key, pane, **_kwargs: {
        "pane": pane, "foreground": {"name": "omp"}})
    with Store(tmp_path / "sense.db") as store:
        source.tick(store)
        before = _meta(store)
        assert before["healthy"] and before["scopes"]
        clock[0] += 1

        def unavailable(_key, **_kwargs):
            raise runner.TernUnavailable("stale route")

        monkeypatch.setattr(runner, "inspect", unavailable)
        source.tick(store)
        event = store.last_by_kind("tern_snapshot")
        assert event is not None and event.payload["state"] == "unknown"
        assert not _meta(store)["healthy"] and not _meta(store)["scopes"]
        assert {item["proof_ref"] for item in event.payload["closed"]} == {
            item["proof_ref"] for item in before["scopes"].values()}


def test_final_process_sample_overrides_earlier_structural_observation(
    tmp_path, monkeypatch
):
    source = tern_collector.TernCollector()
    monkeypatch.setattr(observation, "_clock", lambda: 100.0)
    monkeypatch.setattr(tern_collector, "_stamp", lambda: (to_z(datetime.now(UTC)), 100.0))
    monkeypatch.setattr(tern_collector, "boot_identity", lambda: ("boot", 100.0))
    monkeypatch.setattr(tern_collector, "read_registrations", lambda: [_registration(source)])
    inspections = []
    samples = []

    def inspect(key, **_kwargs):
        inspections.append(key)
        return _raw(key)

    monkeypatch.setattr(runner, "inspect", inspect)

    def process(key, pane, **_kwargs):
        samples.append((key, pane, len(inspections)))
        return {"pane": pane, "foreground": {"name": "bash"}}

    monkeypatch.setattr(runner, "process", process)
    with Store(tmp_path / "sense.db") as store:
        source.tick(store)
        event = store.last_by_kind("tern_snapshot")
        assert samples and all(key == "B" and pane == 4 and count >= 3
                               for key, pane, count in samples)
        assert event is not None and event.payload["state"] == "observed"
        pane = event.payload["windows"][0]["panes"][0]
        assert pane["omp_foreground"] is not True
        assert pane["omp_working"] is not True


def test_daemon_native_poll_continues_while_single_tern_job_is_slow_and_fails(
    tmp_path, monkeypatch
):
    now = [0.0]
    entered = threading.Event()
    release = threading.Event()
    calls = []
    focus_times = []

    def collect(_self, _cancel):
        calls.append(now[0])
        entered.set()
        assert release.wait(5), "test did not release blocked collection"
        raise runner.TernUnavailable("source failed")

    monkeypatch.setattr(tern_collector.TernCollector, "collect", collect)
    monkeypatch.setattr(daemon_loop, "time", SimpleNamespace(monotonic=lambda: now[0]))

    def native():
        focus_times.append(now[0])
        return [FocusWindow("so.stencil.tern", "shell", True, window_id="native:1")]

    class ClockQueue:
        def get(self, timeout):
            assert entered.wait(5), "daemon did not start the Tern worker"
            now[0] += 1
            if now[0] >= 13:
                release.set()
            raise queue.Empty

    focus = FocusCollector(probe=native, source="cosmic")
    with Store(tmp_path / "sense.db") as store:
        try:
            daemon_loop.run_main_loop(
                cfg=SenseConfig(focus_events=False, poll_seconds=100),
                store=store, focus=focus,
                focus_rt=SimpleNamespace(active=SimpleNamespace(source="cosmic")),
                poll_collectors=[], use_wl=False, atspi_q=ClockQueue(),
                idle_q=queue.Queue(), agent=None, idle_watch=None,
                start_atspi=lambda: None, start_idle_watch=lambda: None,
                sync_focus_mode=lambda _agent: (False, False),
                stop_flag=lambda: now[0] >= 13, trace=None,
            )
            event = store.last_by_kind("focus")
            assert event is not None
            assert event.payload["app"] == "so.stencil.tern"
            assert event.payload["window_id"] == "native:1"
            assert focus_times[0] == 0 and focus_times[-1] >= 12
            assert max(b - a for a, b in zip(focus_times, focus_times[1:], strict=False)) <= 1
            assert len(calls) == 1
        finally:
            release.set()


def test_late_batch_completion_does_not_renew_exact_process_observation(
    tmp_path, monkeypatch
):
    from roxabi_sense.collectors.tern.metadata import opaque
    from roxabi_sense.util.time import parse_ts

    source = tern_collector.TernCollector()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    clock = [100.0]
    last_process: list[datetime | None] = [None]
    registration_reads = [0]

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, _tz):
            return base + timedelta(seconds=clock[0] - 100)

    monkeypatch.setattr(observation, "datetime", FakeDateTime)
    monkeypatch.setattr(observation, "_clock", lambda: clock[0])
    monkeypatch.setattr(tern_collector, "_stamp", lambda: (
        to_z(base + timedelta(seconds=clock[0] - 100)), clock[0]))
    monkeypatch.setattr(tern_collector, "boot_identity", lambda: ("boot", clock[0]))

    def registrations():
        registration_reads[0] += 1
        if registration_reads[0] == 4:
            clock[0] += 4
        return [_registration(source, expires=130)]

    def process(_key, pane, **_kwargs):
        last_process[0] = base + timedelta(seconds=clock[0] - 100)
        return {"pane": pane, "foreground": {"name": "omp"}}

    monkeypatch.setattr(tern_collector, "read_registrations", registrations)
    monkeypatch.setattr(runner, "inspect", lambda key, **_kwargs: _raw(key))
    monkeypatch.setattr(runner, "process", process)
    scope = opaque("selected-scope", [source.endpoint, "B", 1, 2])
    with Store(tmp_path / "sense.db") as store:
        source.tick(store)
        assert scope in _meta(store)["scopes"]
        clock[0] += 1
        source.tick(store)
        proof = _meta(store)["scopes"].get(scope)
        assert last_process[0] is not None
        assert clock[0] >= 105
        assert proof is None or parse_ts(proof["last_observed_at"]) <= last_process[0]
        if proof is not None:
            assert parse_ts(proof["fresh_until"]) <= last_process[0] + timedelta(seconds=10)


def test_discovery_without_registration_uses_explicit_route_for_work(tmp_path, monkeypatch):
    source = tern_collector.TernCollector()
    monkeypatch.setattr(observation, "_clock", lambda: 100.0)
    monkeypatch.setattr(tern_collector, "_stamp", lambda: (to_z(datetime.now(UTC)), 100.0))
    monkeypatch.setattr(tern_collector, "boot_identity", lambda: ("boot", 100.0))
    monkeypatch.setattr(tern_collector, "read_registrations", lambda: [])
    routes = []

    def inspect(key, **_kwargs):
        routes.append(key)
        result = _raw()
        if key is None:
            result["panes"][0]["cwd"] = "/discovery-must-not-be-used"
        return result

    monkeypatch.setattr(runner, "inspect", inspect)
    monkeypatch.setattr(runner, "process", lambda key, pane, **_kwargs: {
        "pane": pane, "foreground": {"name": "omp"}})
    with Store(tmp_path / "sense.db") as store:
        source.tick(store)
        event = store.last_by_kind("tern_snapshot")
        assert event is not None and event.payload["state"] == "observed"
        window = event.payload["windows"][0]
        assert routes[0] is None and "B" in routes[1:]
        assert window["registered"] is False
        assert window["panes"][0]["cwd"] == "/repo"
        assert window["panes"][0]["omp_working"] is True
        assert len(_meta(store)["scopes"]) == 1


def test_late_worker_completion_cannot_reopen_expired_proof(tmp_path, monkeypatch):
    from roxabi_sense.collectors.tern import worker

    source = tern_collector.TernCollector()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    clock = [100.0]
    entered = threading.Event()
    release = threading.Event()

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, _tz):
            return base + timedelta(seconds=clock[0] - 100)

    monkeypatch.setattr(observation, "datetime", FakeDateTime)
    monkeypatch.setattr(observation, "_clock", lambda: clock[0])
    monkeypatch.setattr(tern_collector, "_stamp", lambda: (
        to_z(base + timedelta(seconds=clock[0] - 100)), clock[0]))
    monkeypatch.setattr(tern_collector, "boot_identity", lambda: ("boot", clock[0]))
    monkeypatch.setattr(worker, "boot_identity", lambda: ("boot", clock[0]))
    monkeypatch.setattr(tern_collector, "read_registrations",
                        lambda: [_registration(source, expires=130)])
    monkeypatch.setattr(runner, "inspect", lambda key, **_kwargs: _raw(key))
    monkeypatch.setattr(runner, "process", lambda key, pane, **_kwargs: {
        "pane": pane, "foreground": {"name": "omp"}})
    acquire = source.collect
    calls = []

    def delayed(cancel):
        calls.append(1)
        if len(calls) > 1:
            return None
        sample = acquire(cancel)
        entered.set()
        assert release.wait(5), "test did not release acquired sample"
        return sample

    with Store(tmp_path / "sense.db") as store:
        source.tick(store)
        before = _meta(store)
        old_refs = {proof["proof_ref"] for proof in before["scopes"].values()}
        assert old_refs
        monkeypatch.setattr(source, "collect", delayed)
        background = worker.TernWorker(source)
        background.start()
        try:
            assert entered.wait(5)
            clock[0] = 111.0
            background.poll(store)
            assert not _meta(store)["scopes"]
            release.set()
            assert background._thread is not None
            background._thread.join(timeout=5)
            assert not background._thread.is_alive()
            background.poll(store)
            assert not _meta(store)["scopes"]
            event = store.last_by_kind("tern_snapshot")
            assert event is not None
            assert {entry["proof_ref"] for entry in event.payload["closed"]} == old_refs
        finally:
            release.set()
            background.close(store)


def test_failed_source_does_not_erase_native_focus(tmp_path, monkeypatch):
    source = tern_collector.TernCollector()
    monkeypatch.setattr(tern_collector, "read_registrations", lambda: [])

    def unavailable(_key, **_kwargs):
        raise runner.TernUnavailable("Tern unavailable")

    monkeypatch.setattr(runner, "inspect", unavailable)
    native = [FocusWindow("so.stencil.tern", "shell", True, window_id="native:1")]
    focus = FocusCollector(probe=lambda: native, source="cosmic")
    with Store(tmp_path / "sense.db") as store:
        focus.tick(store)
        before = store.last_by_kind("focus")
        assert before is not None
        source.tick(store)
        focus.tick(store)
        failed = store.last_by_kind("tern_snapshot")
        after = store.last_by_kind("focus")
        assert failed is not None and failed.payload["state"] == "unknown"
        assert after is not None and after.id == before.id
        assert after.payload["app"] == "so.stencil.tern"
        assert after.payload["window_id"] == "native:1"
