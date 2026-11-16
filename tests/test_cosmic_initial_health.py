"""Initial Wayland sync health survives a standalone collection and source loss."""
from __future__ import annotations

import json
import socket
import struct
from datetime import timedelta
from pathlib import Path

from roxabi_sense.collectors.focus.collector import FocusCollector
from roxabi_sense.collectors.focus.coverage import CoverageWriter
from roxabi_sense.collectors.focus.probes.cosmic import CosmicFocusProbe
from roxabi_sense.collectors.focus.probes.cosmic_wire import CosmicWire
from roxabi_sense.collectors.focus.protocol import FocusWindow
from roxabi_sense.config import SenseConfig
from roxabi_sense.daemon import collect_once
from roxabi_sense.query import SenseQuery
from roxabi_sense.report.day import compile_day_recap
from roxabi_sense.store import Store
from roxabi_sense.util.time import parse_ts, to_z


def test_once_keeps_initial_ack_and_loss_ends_at_that_ack(tmp_path: Path, monkeypatch) -> None:
    client, server = socket.socketpair()
    wire = CosmicWire(client)
    try:
        wire.ping()
        callback = wire.pending_sync
        assert callback is not None
        server.sendall(struct.pack("=III", callback, (12 << 16), 0))
        wire.receive(0.1)
        observed = wire.ack_observed_at
        assert observed is not None
        wire.ready = True
        wire.handles = {1: {"committed": {
            "active": True, "identifier": "one", "app": "editor", "title": "document",
        }}}
        monkeypatch.setattr(CosmicWire, "connect", classmethod(lambda cls, env: wire))
        monkeypatch.setattr("roxabi_sense.collectors.focus.collector.children_map", lambda: {})
        monkeypatch.setattr(
            "roxabi_sense.collectors.focus.collector.list_mux_agent_panes", lambda: []
        )
        monkeypatch.setattr(
            "roxabi_sense.collectors.focus.collector.resolve_app_name", lambda app, pid: app
        )
        monkeypatch.setattr(
            "roxabi_sense.collectors.focus.collector.find_agent_link", lambda *a, **kw: None
        )
        probe = CosmicFocusProbe(env={})
        collector = FocusCollector(focus_probe=probe, sessions_loader=lambda: [])
        monkeypatch.setattr("roxabi_sense.daemon.build_collectors", lambda cfg: [collector])
        monkeypatch.setattr(
            "roxabi_sense.collectors.focus.runtime.FocusRuntime.select_initial",
            lambda self, store: probe if probe.probe() else None,
        )
        db = tmp_path / "sense.db"
        assert collect_once(SenseConfig(db_path=db, focus=True)) >= 1
        result = SenseQuery(db_path=db, offline_threshold_s=120, idle_threshold_s=300).active_now()
        assert result["focus_quality"] == "observed"
        assert result["focus"]["app"] == "editor"
        with Store(db) as store:
            meta = json.loads(store.get_meta("focus_observation") or "{}")
            assert meta["last_observed_at"] == to_z(observed)
            initial = store.last_by_kind("focus_coverage")
            assert initial is not None
            later = observed + timedelta(seconds=9)
            monkeypatch.setattr(
                "roxabi_sense.collectors.focus.coverage.utc_now_z", lambda: to_z(later)
            )
            monkeypatch.setattr("roxabi_sense.store.db.utc_now_z", lambda: to_z(later))
            probe.reason = "connection_failure"
            collector.apply(store, [], mode="focus")
            loss = store.last_by_kind("focus_coverage")
            assert loss is not None and loss.payload["state"] == "unknown"
            assert loss.ts == max(initial.ts, to_z(observed))
            assert parse_ts(loss.ts) < later
            day = observed.astimezone().date().isoformat()
            recap = compile_day_recap(store, day, now=later)
            assert sum(seconds for _, seconds in recap.time_by_app) == 0
    finally:
        client.close()
        server.close()


def test_unacknowledged_window_cannot_start_certified_coverage(tmp_path: Path) -> None:
    probe = CosmicFocusProbe(env={})
    probe.reason = "observed"
    window = FocusWindow(app="editor", title="", active=True, window_id="epoch:one")
    with Store(tmp_path / "sense.db") as store:
        writer = CoverageWriter()
        writer.initialize(store)
        writer.update(store, probe, [window])
        fact = store.last_by_kind("focus_coverage")
        assert fact is not None and fact.payload["state"] == "unknown"
        assert fact.payload["reason"] == "pending_health"
