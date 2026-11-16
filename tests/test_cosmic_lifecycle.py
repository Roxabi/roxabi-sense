"""COSMIC socket batch, health, disconnect and bounded dispatch behavior."""
from __future__ import annotations

import select
import socket
import struct
import time

import pytest

from roxabi_sense.collectors.focus.probes import cosmic_wire
from roxabi_sense.collectors.focus.probes.cosmic import CosmicFocusProbe
from roxabi_sense.collectors.focus.probes.cosmic_wire import CosmicWire, _string, _word


def frame(obj: int, opcode: int, payload: bytes = b"") -> bytes:
    return struct.pack("=II", obj, (len(payload) + 8) << 16 | opcode) + payload


def test_partial_frames_and_two_window_atomic_switch() -> None:
    client, server = socket.socketpair()
    try:
        wire = CosmicWire(client)
        wire.deadline = time.monotonic() + 5
        wire.manager = wire._new("manager")
        wire.foreign = wire._new("foreign")
        first, second = 0xff000000, 0xff000001
        event = frame(wire.foreign, 0, _word(first))
        server.sendall(event[:5])
        wire.receive(0.1)
        assert not wire.handles
        server.sendall(event[5:] + frame(wire.foreign, 0, _word(second)))
        wire.receive(0.1)
        for number, handle in enumerate((first, second)):
            extension = wire.handles[handle]["extension"]
            server.sendall(
                frame(handle, 3, _string(f"app{number}"))
                + frame(handle, 4, _string(f"window{number}"))
                + frame(handle, 1)
                + frame(extension, 8, _word(4) + _word(2 if number == 0 else 1))
            )
        server.sendall(frame(wire.manager, 2))
        wire.receive(0.1)
        assert wire.handles[first]["committed"]["active"]
        server.sendall(
            frame(wire.handles[first]["extension"], 8, _word(4) + _word(1))
            + frame(wire.handles[second]["extension"], 8, _word(4) + _word(2))
        )
        wire.receive(0.1)
        assert wire.dirty and wire.handles[first]["committed"]["active"]
        server.sendall(frame(wire.manager, 2))
        wire.receive(0.1)
        assert not wire.handles[first]["committed"]["active"]
        assert wire.handles[second]["committed"]["active"]
        server.sendall(frame(second, 0))
        wire.receive(0.1)
        assert not wire.ready and wire.dirty
        assert second not in wire.handles
    finally:
        client.close()
        server.close()


def test_ack_before_completed_batch_is_healthy_but_partial_batch_is_not() -> None:
    client, server = socket.socketpair()
    try:
        wire = CosmicWire(client)
        wire.ready = True
        wire.ack_at = time.monotonic() - 0.1
        wire.commit_at = time.monotonic()
        wire.handles = {1: {"committed": {"active": True, "identifier": "one"}}}
        probe = CosmicFocusProbe(env={})
        probe.wire = wire
        assert probe.get_active()[0].window_id == f"{wire.epoch}:one"
        wire.dirty = True
        assert probe.get_active() == []
        assert probe.reason == "pending_batch"
    finally:
        client.close()
        server.close()


def test_global_removal_and_disconnect_are_fail_closed() -> None:
    client, server = socket.socketpair()
    try:
        wire = CosmicWire(client)
        wire.global_names.add(123)
        server.sendall(frame(wire.registry, 1, _word(123)))
        with pytest.raises(ConnectionError, match="removed"):
            wire.receive(0.1)
        server.close()
        probe = CosmicFocusProbe(env={})
        probe.wire = wire
        assert probe.get_active() == []
        assert probe.reason == "connection_failure"
    finally:
        client.close()
        server.close()


def test_nested_dispatch_write_uses_same_absolute_budget(monkeypatch) -> None:
    client, server = socket.socketpair()
    try:
        wire = CosmicWire(client)
        wire.manager = wire._new("manager")
        wire.foreign = wire._new("foreign")
        server.sendall(frame(wire.foreign, 0, _word(0xff000000)))
        original = select.select
        clock = [100.0]
        waits: list[float] = []

        def fake_select(readers, writers, errors, timeout):
            if writers:
                waits.append(timeout)
                clock[0] += timeout
                return [], [], []
            return original(readers, writers, errors, 0)

        monkeypatch.setattr(cosmic_wire.time, "monotonic", lambda: clock[0])
        monkeypatch.setattr(cosmic_wire.select, "select", fake_select)
        wire.deadline = 100.05
        with pytest.raises(TimeoutError, match="write timeout"):
            wire.receive(0.1)
        assert len(waits) == 1 and 0 < waits[0] <= 0.051
    finally:
        client.close()
        server.close()


def test_probe_reconnect_uses_new_epoch(monkeypatch) -> None:
    first_client, first_server = socket.socketpair()
    next_client, next_server = socket.socketpair()
    try:
        first = CosmicWire(first_client)
        second = CosmicWire(next_client)
        for wire in (first, second):
            wire.ready = True
            wire.ack_at = time.monotonic()
            wire.handles = {1: {"committed": {"active": True, "identifier": "same"}}}
        probe = CosmicFocusProbe(env={})
        probe.wire = first
        before = probe.get_active()[0].window_id
        first_server.close()
        assert probe.get_active() == []
        assert probe.reason == "connection_failure"
        monkeypatch.setattr(CosmicWire, "connect", classmethod(lambda cls, env: second))
        probe._retry_at = 0
        after = probe.get_active()[0].window_id
        assert before != after
        assert before is not None and before.endswith(":same")
        assert after is not None and after.endswith(":same")
    finally:
        first_client.close()
        first_server.close()
        next_client.close()
        next_server.close()
