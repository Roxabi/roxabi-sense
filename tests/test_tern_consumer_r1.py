"""Tern proof publication through native focus into shared attention consumers."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from roxabi_sense.collectors.focus.collector import FocusCollector
from roxabi_sense.collectors.focus.ownership import acquire_focus_owner, release_focus_owner
from roxabi_sense.collectors.focus.probes.cosmic import CosmicFocusProbe
from roxabi_sense.collectors.focus.protocol import FocusWindow
from roxabi_sense.collectors.tern import observation
from roxabi_sense.collectors.tern.collector import TernCollector
from roxabi_sense.collectors.tern.metadata import pane_work_semantic
from roxabi_sense.config import SenseConfig
from roxabi_sense.daemon import collect_once
from roxabi_sense.report.care import compile_care_brief
from roxabi_sense.report.day import compile_day_recap
from roxabi_sense.store import Store
from roxabi_sense.util.time import parse_ts, to_z

DAY = "2026-07-30"
TOKEN = "a" * 16
APP = "so.stencil.tern"


class Harness:
    def __init__(self, store, monkeypatch, start):
        self.store = store
        self.now = start
        self.boot = 100.0
        self.writer = observation.ObservationWriter(freshness_s=10)
        self.probe = CosmicFocusProbe(env={})
        monkeypatch.setattr(self.probe, "wire", SimpleNamespace(
            ready=True, dirty=False, epoch="epoch",
            handles={1: {"committed": {"identifier": "window", "active": True}}},
            commit_at=1.0, ack_at=1.0, ack_observed_at=None,
        ))
        self.probe.reason = "observed"
        self.collector = FocusCollector(focus_probe=self.probe, sessions_loader=lambda: [])
        self.pane = "pane-a"
        self.title = "shell"
        self.background = False
        owner = self

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return owner.now if tz is not None else owner.now.replace(tzinfo=None)

        monkeypatch.setattr(observation, "datetime", Clock)
        monkeypatch.setattr(observation, "_clock", lambda: self.boot)
        monkeypatch.setattr("roxabi_sense.collectors.focus.tern.datetime", Clock)
        monkeypatch.setattr(
            "roxabi_sense.collectors.focus.collector._observed_at",
            lambda: self.now.isoformat().replace("+00:00", "Z"),
        )
        monkeypatch.setattr("roxabi_sense.store.db.utc_now_z", lambda: to_z(self.now))
        monkeypatch.setattr(
            "roxabi_sense.collectors.focus.coverage.utc_now_z", lambda: to_z(self.now)
        )
        monkeypatch.setattr(
            "roxabi_sense.collectors.focus.tern.boot_identity", lambda: ("boot", self.boot)
        )
        monkeypatch.setattr("roxabi_sense.collectors.focus.tern.read_registrations", lambda: [{
            "token": TOKEN, "boot_id": "boot", "expires_boottime": self.boot + 100,
            "endpoint_ref": "endpoint", "window_key": "", "conn": 1, "since": 2,
        }])
        monkeypatch.setattr(
            "roxabi_sense.collectors.focus.collector.resolve_app_name", lambda app, pid: app
        )
        store.append("idle", {"idle": False})

    def window(self):
        panes = [{"pane_ref": self.pane, "host_ref": "host", "cwd": "/repo",
                  "repo_ref": "repo", "session_context_ref": self.pane,
                  "selected": True, "pane_working": True, "omp_foreground": True,
                  "omp_working": True}]
        if self.background:
            panes.append({"pane_ref": "background", "selected": False,
                          "pane_working": True, "omp_working": False})
        return {"endpoint_ref": "endpoint", "window_key": "", "conn": 1, "since": 2,
                "registered": True, "shown_pane": self.pane, "panes": panes}

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)
        self.boot += seconds

    def focus(self):
        self.probe.observed_at = self.now
        assert self.probe.wire is not None
        self.probe.wire.ack_observed_at = self.now
        native = FocusWindow(APP, f"[sense:t1:{TOKEN}] {self.title}", True,
                             window_id="epoch:window")
        self.collector.apply(self.store, [native], mode="focus", source="cosmic")

    def poll(self):
        self.writer.publish(self.store, [self.window()])
        self.focus()

    def healthy_for(self, seconds):
        for _ in range(seconds // 5):
            self.advance(5)
            self.poll()

    def recap(self):
        recap = compile_day_recap(self.store, DAY, now=self.now)
        return recap, compile_care_brief(recap, {"state": "active"}, now=self.now)

    def events(self, kind):
        return self.store.events_for_day(DAY, kinds=(kind,), limit=1000)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    with Store(tmp_path / "sense.db") as store:
        start, _ = store.day_bounds(DAY)
        yield Harness(store, monkeypatch, parse_ts(start) + timedelta(hours=12))


@pytest.mark.parametrize("minutes", [20, 40])
def test_healthy_between_polls_keeps_confirmed_current_stretch(setup, minutes):
    h = setup
    h.poll()
    h.healthy_for(minutes * 60)
    h.advance(1)
    h.focus()  # Native observation continues; Tern lease remains live between polls.
    recap, brief = h.recap()
    assert recap.current_attention is not None
    assert brief["current_stretch"] is not None
    assert brief["current_stretch"]["minutes"] == minutes
    assert len(h.events("focus")) == 1
    assert len(h.events("tern_snapshot")) == 1
    assert sum(s.duration_s for s in recap.focus_segments if s.context_ref) == minutes * 60


def test_background_snapshot_does_not_reset_foreground_attention(setup):
    h = setup
    h.poll()
    h.healthy_for(600)
    h.background = True
    h.advance(5)
    h.poll()
    h.healthy_for(595)
    h.advance(1)
    h.focus()
    recap, brief = h.recap()
    assert recap.current_attention is not None
    assert brief["current_stretch"] is not None
    assert brief["current_stretch"]["minutes"] == 20
    assert len(h.events("focus")) == 1
    assert len(h.events("tern_snapshot")) == 2
    assert recap.focus_switches_context == 0


def test_crash_with_open_meta_clips_context_but_keeps_native_focus(setup):
    h = setup
    h.poll()
    h.healthy_for(60)
    last = h.now
    h.advance(15)
    h.focus()
    recap, _ = h.recap()
    contextual = [s for s in recap.focus_segments if s.context_ref]
    assert contextual and contextual[-1].end == to_z(last)
    assert recap.focus_segments[-1].app == APP
    assert recap.focus_segments[-1].context_ref is None
    assert recap.focus_switches_context == 0


@pytest.mark.parametrize("failure", ["source", "gap", "restart"])
def test_loss_and_recovery_start_new_occurrence_without_human_switch(setup, failure):
    h = setup
    h.poll()
    h.healthy_for(60)
    last = h.now
    h.advance(5)
    if failure == "source":
        h.writer.publish(h.store, None)
        h.focus()
        h.advance(5)
    elif failure == "gap":
        h.advance(7)  # suspend-aware clock exceeds the proof lease
    else:
        h.writer = observation.ObservationWriter(freshness_s=10)
    recovery = h.now
    h.poll()
    h.healthy_for(5)
    recap, brief = h.recap()
    contextual = [s for s in recap.focus_segments if s.context_ref]
    assert contextual[0].end == to_z(last)
    assert contextual[-1].start == to_z(recovery)
    assert recap.current_attention is not None
    assert recap.current_attention.context_started_at == to_z(recovery)
    assert brief["current_stretch"] is not None
    assert brief["current_stretch"]["minutes"] < 1
    assert recap.focus_switches_context == 0
    assert all(s.app == APP for s in recap.focus_segments)


def test_one_second_other_pane_breaks_occurrence(setup):
    h = setup
    h.poll()
    h.healthy_for(2400)
    h.advance(1)
    h.pane = "pane-b"
    h.poll()
    h.advance(1)
    h.pane = "pane-a"
    recovery = h.now
    h.poll()
    h.advance(3)
    h.poll()
    recap, brief = h.recap()
    assert recap.current_attention is not None
    assert recap.current_attention.context_started_at == to_z(recovery)
    assert brief["current_stretch"] is not None
    assert brief["current_stretch"]["minutes"] == 0.05
    assert "long_focus" not in brief["signals"]


def test_carry_in_context_starts_at_day_boundary(tmp_path, monkeypatch):
    with Store(tmp_path / "sense.db") as store:
        start, _ = store.day_bounds(DAY)
        boundary = parse_ts(start)
        h = Harness(store, monkeypatch, boundary - timedelta(seconds=5))
        h.poll()
        h.healthy_for(65)
        recap, brief = h.recap()
        assert recap.current_attention is not None
        assert recap.current_attention.context_started_at == to_z(boundary)
        assert brief["current_stretch"] is not None
        assert brief["current_stretch"]["minutes"] == 1


def test_shared_idle_pane_collects_both_views_with_selected_foreground(tmp_path, monkeypatch):
    clients = [
        {"kind": "window", "window": "", "conn": 1, "since": 2},
        {"kind": "window", "window": "other", "conn": 3, "since": 4},
    ]
    selected = {
        "clients": clients,
        "sessions": [{"shown": True, "tabs": [{"shown": True, "blocks": [
            {"id": 4, "live": True, "focused": True, "exited": None},
        ]}]}],
        "panes": [{"pane": 4, "identity": {"hops": []}, "cwd": "/repo",
                   "working": False, "exited": None}],
    }
    background = deepcopy(selected)
    background["sessions"][0]["tabs"][0]["blocks"][0]["focused"] = False
    monkeypatch.setattr(
        "roxabi_sense.collectors.tern.runner.inspect",
        lambda key, **kwargs: deepcopy(selected if key == "" else background),
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.tern.runner.process",
        lambda key, pane, **kwargs: {"pane": pane, "foreground": {"name": "omp"}},
    )
    collector = TernCollector()
    registrations = [
        {"endpoint_ref": collector.endpoint, "window_key": key, "conn": conn, "since": since,
         "token": token, "boot_id": "boot", "expires_boottime": 1000}
        for key, conn, since, token in [("", 1, 2, "a" * 16), ("other", 3, 4, "b" * 16)]
    ]
    monkeypatch.setattr(
        "roxabi_sense.collectors.tern.collector.read_registrations", lambda: registrations
    )
    monkeypatch.setattr("roxabi_sense.collectors.tern.collector.boot_identity", lambda: ("boot", 1))
    with Store(tmp_path / "sense.db") as store:
        collector.tick(store)
        snapshot = store.last_by_kind("tern_snapshot")
    assert snapshot is not None
    assert snapshot.payload["state"] == "observed"
    windows = {w["window_key"]: w for w in snapshot.payload["windows"]}
    a, b = windows[""]["panes"][0], windows["other"]["panes"][0]
    assert a["pane_ref"] == b["pane_ref"]
    assert a["selected"] is True and b["selected"] is False
    assert a["omp_foreground"] is b["omp_foreground"] is True
    assert pane_work_semantic(a) == pane_work_semantic(b)
    assert a["pane_working"] is b["pane_working"] is False
    assert a["omp_working"] is b["omp_working"] is False
    assert a["process_proof_ref"] == b["process_proof_ref"]


def test_concurrent_once_preserves_owned_tern_proof(setup, monkeypatch):
    h = setup
    owner = acquire_focus_owner(h.store.path)
    assert owner is not None
    try:
        h.poll()
        h.healthy_for(1200)
        before = h.store.get_meta("tern_observation")
        snapshot = h.store.last_by_kind("tern_snapshot")
        monkeypatch.setattr(
            "roxabi_sense.daemon.build_poll_collectors", lambda cfg, logind_idle: []
        )
        collect_once(SenseConfig(db_path=h.store.path, focus=True, idle=False))
        assert h.store.get_meta("tern_observation") == before
        assert h.store.last_by_kind("tern_snapshot") == snapshot
        h.advance(5)
        h.poll()
        recap, brief = h.recap()
        assert recap.current_attention is not None
        assert brief["current_stretch"] is not None
        assert brief["current_stretch"]["minutes"] >= 20
        assert len(h.events("focus")) == 1
    finally:
        release_focus_owner(owner)


def test_real_title_change_on_recovery_is_not_suppressed(setup):
    h = setup
    h.poll()
    h.healthy_for(60)
    h.advance(5)
    h.writer.publish(h.store, None)
    h.focus()
    h.advance(5)
    h.title = "different document"
    h.poll()
    h.healthy_for(5)
    recap, _ = h.recap()
    assert recap.focus_switches == 1
    assert recap.focus_switches_context == 0
