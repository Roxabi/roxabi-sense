"""Wire-level COSMIC focus observer regressions using an isolated socket pair."""
from __future__ import annotations

import json
import socket
import struct
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from roxabi_sense.collectors.focus.coverage import CoverageWriter
from roxabi_sense.collectors.focus.probes.cosmic import CosmicFocusProbe
from roxabi_sense.collectors.focus.probes.cosmic_wire import CosmicWire, _string, _word
from roxabi_sense.collectors.focus.protocol import FocusWindow
from roxabi_sense.query import SenseQuery
from roxabi_sense.store import Store


def _event(sock: socket.socket, obj: int, opcode: int, payload: bytes = b"") -> None:
    sock.sendall(struct.pack("=II", obj, ((len(payload) + 8) << 16) | opcode) + payload)


def test_decoder_commits_focus_only_at_global_done() -> None:
    client, server = socket.socketpair()
    try:
        wire = CosmicWire(client)
        wire.manager = wire._new("manager")
        wire.foreign = wire._new("foreign")
        _event(server, wire.foreign, 0, _word(0xff000000))
        wire.receive(0.1)
        extension = wire.handles[0xff000000]["extension"]
        _event(server, 0xff000000, 2, _string("Editor"))
        _event(server, 0xff000000, 3, _string("editor"))
        _event(server, 0xff000000, 4, _string("stable-window"))
        _event(server, 0xff000000, 1)
        _event(server, extension, 8, _word(4) + _word(2))
        wire.receive(0.1)
        assert wire.dirty and not wire.ready
        _event(server, wire.manager, 2)
        wire.receive(0.1)
        assert wire.ready and not wire.dirty
        assert wire.handles[0xff000000]["committed"]["active"] is True
        assert wire.handles[0xff000000]["committed"]["identifier"] == "stable-window"
        _event(server, extension, 8, _word(4) + _word(1))
        wire.receive(0.1)
        assert wire.dirty
        assert wire.handles[0xff000000]["committed"]["active"] is True
        _event(server, wire.manager, 2)
        wire.receive(0.1)
        assert wire.handles[0xff000000]["committed"]["active"] is False
    finally:
        client.close()
        server.close()


def test_probe_fails_closed_on_ambiguous_or_unresponsive_source() -> None:
    client, server = socket.socketpair()
    try:
        wire = CosmicWire(client)
        wire.ready = True
        wire.ack_at = time.monotonic()
        wire.handles = {
            10: {"committed": {"active": True, "identifier": "a"}},
            11: {"committed": {"active": True, "identifier": "b"}},
        }
        probe = CosmicFocusProbe(env={})
        probe.wire = wire
        assert probe.get_active() == []
        assert probe.reason == "ambiguous"
        wire.handles[11]["committed"]["active"] = False
        wire.handles[10]["committed"]["active"] = False
        assert probe.get_active() == []
        assert probe.reason == "no_active"
        wire.handles[10]["committed"]["active"] = True
        windows = probe.get_active()
        assert len(windows) == 2
        assert next(w.window_id for w in windows if w.active) == f"{wire.epoch}:a"
        probe._ping_at = time.monotonic() - 9
        assert probe.get_active() == []
        assert probe.reason == "connection_failure"
        assert probe.wire is None
    finally:
        client.close()
        server.close()


def test_coverage_updates_health_without_heartbeat_events(tmp_path: Path) -> None:
    probe = CosmicFocusProbe(env={})
    probe.reason = "observed"
    probe.observed_at = datetime.now(UTC)
    writer = CoverageWriter()
    windows = [FocusWindow(app="editor", title="", active=True, window_id="epoch:window")]
    with Store(tmp_path / "coverage.db") as store:
        writer.initialize(store)
        assert writer.update(store, probe, windows) == (1, True)
        first = store.last_by_kind("focus_coverage")
        assert first is not None and first.payload["state"] == "observed"
        probe.observed_at += timedelta(seconds=1)
        assert writer.update(store, probe, windows) == (0, False)
        assert store.last_by_kind("focus_coverage") == first
        meta = json.loads(store.get_meta("focus_observation") or "{}")
        assert meta["coverage_id"] == first.id
        probe.reason = "no_active"
        assert writer.update(store, probe, []) == (1, True)
        unknown = store.last_by_kind("focus_coverage")
        assert unknown is not None and unknown.payload["state"] == "unknown"
        probe.reason = "observed"
        assert writer.update(store, probe, windows) == (1, True)


def test_connection_epochs_are_distinct_and_invalid_frame_fails_closed() -> None:
    first_client, first_server = socket.socketpair()
    second_client, second_server = socket.socketpair()
    try:
        first = CosmicWire(first_client)
        second = CosmicWire(second_client)
        assert first.epoch != second.epoch
        first_server.sendall(struct.pack("=II", 1, (7 << 16)))
        with pytest.raises(ValueError, match="message size"):
            first.receive(0.1)
    finally:
        first_client.close()
        first_server.close()
        second_client.close()
        second_server.close()


def test_active_now_drops_expired_coverage(tmp_path: Path) -> None:
    db = tmp_path / "status.db"
    with Store(db) as store:
        coverage_id = store.append("focus_coverage", {
            "state": "observed", "source": "cosmic", "window_id": "epoch:one",
        }, ts="2000-01-01T00:00:00Z")
        store.append("focus", {
            "app": "editor", "source": "cosmic", "window_id": "epoch:one",
        }, ts="2000-01-01T00:00:00Z")
        store.set_meta("focus_observation", json.dumps({
            "coverage_id": coverage_id, "source": "cosmic", "window_id": "epoch:one",
            "healthy": True, "last_observed_at": "2000-01-01T00:00:00Z",
            "fresh_until": "2000-01-01T00:00:08Z",
        }))
    query = SenseQuery(db_path=db, offline_threshold_s=120, idle_threshold_s=300)
    result = query.active_now()
    assert result["focus"] is None
    assert result["focus_quality"] == "unknown"


def test_active_now_requires_matching_live_observation(tmp_path: Path) -> None:
    from roxabi_sense.util.time import to_z

    db = tmp_path / "status.db"
    now = datetime.now(UTC)
    with Store(db) as store:
        coverage_id = store.append("focus_coverage", {
            "state": "observed", "source": "cosmic", "window_id": "epoch:one",
        }, ts=to_z(now))
        store.append("focus", {
            "app": "editor", "source": "cosmic", "window_id": "epoch:one",
        }, ts=to_z(now))
        store.set_meta("focus_observation", json.dumps({
            "coverage_id": coverage_id, "source": "cosmic", "window_id": "epoch:one",
            "healthy": True, "last_observed_at": to_z(now),
            "fresh_until": to_z(now + timedelta(seconds=30)),
        }))
    query = SenseQuery(db_path=db, offline_threshold_s=120, idle_threshold_s=300)
    assert query.active_now()["focus_quality"] == "observed"
    with Store(db) as store:
        store.append("focus_coverage", {
            "state": "unknown", "source": "cosmic", "reason": "connection_failure",
        })
    assert query.active_now()["focus"] is None
