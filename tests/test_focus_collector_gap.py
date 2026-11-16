"""Coverage boundaries emitted by the collector reach the day recap."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from roxabi_sense.collectors.focus.collector import FocusCollector
from roxabi_sense.collectors.focus.probes.cosmic import CosmicFocusProbe
from roxabi_sense.collectors.focus.protocol import FocusWindow
from roxabi_sense.report.care import compile_care_brief
from roxabi_sense.report.day import compile_day_recap
from roxabi_sense.store import Store
from roxabi_sense.util.time import parse_ts, to_z


def test_collector_loss_and_same_window_recovery_clip_recap(tmp_path: Path, monkeypatch) -> None:
    day = "2026-07-30"
    with Store(tmp_path / "sense.db") as store:
        day_start, _ = store.day_bounds(day)
        start = parse_ts(day_start) + timedelta(hours=12)
        clock = [start]
        def now_z() -> str:
            return to_z(clock[0])

        monkeypatch.setattr("roxabi_sense.store.db.utc_now_z", now_z)
        monkeypatch.setattr("roxabi_sense.collectors.focus.coverage.utc_now_z", now_z)
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
        window = FocusWindow(app="editor", title="document", active=True, window_id="epoch:one")
        probe = CosmicFocusProbe(env={})
        windows = [window]
        monkeypatch.setattr(probe, "get_active", lambda: windows)
        collector = FocusCollector(focus_probe=probe, sessions_loader=lambda: [])
        store.append("idle", {"idle": False, "source": "wayland-idle"})

        probe.reason = "observed"
        probe.observed_at = clock[0]
        collector.tick_focus(store)
        clock[0] = start + timedelta(minutes=1)
        probe.observed_at = clock[0]
        collector.tick_focus(store)

        clock[0] = start + timedelta(seconds=90)
        probe.reason = "connection_failure"
        windows.clear()
        collector.tick_focus(store)

        clock[0] = start + timedelta(minutes=2)
        probe.reason = "observed"
        probe.observed_at = clock[0]
        windows.append(window)
        collector.tick_focus(store)
        clock[0] = start + timedelta(minutes=3)
        probe.observed_at = clock[0]
        collector.tick_focus(store)

        events = store.events_for_day(day, kinds=("focus_coverage",), limit=100)
        assert [(e.payload["state"], e.ts) for e in events] == [
            ("observed", to_z(start)),
            ("unknown", to_z(start + timedelta(minutes=1))),
            ("observed", to_z(start + timedelta(minutes=2))),
        ]
        focus = store.events_for_day(day, kinds=("focus",), limit=100)
        assert [e.ts for e in focus] == [to_z(start), to_z(start + timedelta(minutes=2))]
        meta = json.loads(store.get_meta("focus_observation") or "{}")
        assert meta["last_observed_at"] == to_z(clock[0])
        recap = compile_day_recap(store, day, now=clock[0])

    assert dict(recap.time_by_app)["editor"] == 120
    assert [(segment.start, segment.end) for segment in recap.focus_segments] == [
        (to_z(start), to_z(start + timedelta(minutes=1))),
        (to_z(start + timedelta(minutes=2)), to_z(start + timedelta(minutes=3))),
    ]
    assert recap.focus_switches == 0
    assert recap.focus_switches_app == 0
    assert recap.focus_switches_context == 0


@pytest.mark.parametrize(
    ("app", "window_id", "repeated"),
    [("browser", "epoch:b", False), ("editor", "new-epoch:a", False),
     ("browser", "epoch:b", True)],
)
def test_short_recovery_does_not_resurrect_previous_stretch(
    tmp_path: Path, app: str, window_id: str, repeated: bool
) -> None:
    day = "2026-07-30"
    with Store(tmp_path / "sense.db") as store:
        day_start, _ = store.day_bounds(day)
        start = parse_ts(day_start) + timedelta(hours=12)
        recovery = start + timedelta(minutes=40, seconds=10)
        horizon = recovery + timedelta(seconds=2 if repeated else 1)
        store.append("idle", {"idle": False}, ts=to_z(start))
        store.append("focus_coverage", {
            "state": "observed", "source": "cosmic", "window_id": "old-epoch:a",
        }, ts=to_z(start))
        store.append("focus", {
            "app": "editor", "source": "cosmic", "window_id": "old-epoch:a",
        }, ts=to_z(start))
        store.append("focus_coverage", {
            "state": "unknown", "source": "cosmic", "reason": "lost",
        }, ts=to_z(start + timedelta(minutes=40)))
        coverage_id = store.append("focus_coverage", {
            "state": "observed", "source": "cosmic", "window_id": window_id,
        }, ts=to_z(recovery))
        store.append("focus", {
            "app": app, "source": "cosmic", "window_id": window_id,
        }, ts=to_z(recovery))
        if repeated:
            store.append("focus", {
                "app": app, "source": "cosmic", "window_id": window_id, "title": "changed",
            }, ts=to_z(recovery + timedelta(seconds=1)))
        store.set_meta("focus_observation", json.dumps({
            "coverage_id": coverage_id, "source": "cosmic", "window_id": window_id,
            "last_observed_at": to_z(horizon),
            "fresh_until": to_z(horizon + timedelta(seconds=8)), "healthy": True,
        }))
        recap = compile_day_recap(store, day, now=horizon)
        brief = compile_care_brief(recap, {"state": "active"}, now=horizon)
    assert dict(recap.time_by_app)["editor"] >= 2400
    assert brief["current_stretch"] is None
    assert "long_focus" not in brief["signals"]


def test_session_shape_does_not_count_coverage_gaps_as_switches(tmp_path: Path) -> None:
    day = "2026-07-30"
    with Store(tmp_path / "sense.db") as store:
        day_start, _ = store.day_bounds(day)
        start = parse_ts(day_start) + timedelta(hours=12)
        store.append("idle", {"idle": False}, ts=to_z(start))
        for i in range(6):
            begin = start + timedelta(seconds=i * 190)
            coverage_id = store.append("focus_coverage", {
                "state": "observed", "source": "cosmic", "window_id": "epoch:one",
            }, ts=to_z(begin))
            store.append("focus", {
                "app": "editor", "source": "cosmic", "window_id": "epoch:one",
            }, ts=to_z(begin))
            if i < 5:
                store.append("focus_coverage", {
                    "state": "unknown", "source": "cosmic", "reason": "lost",
                }, ts=to_z(begin + timedelta(minutes=3)))
        horizon = start + timedelta(seconds=5 * 190 + 180)
        store.set_meta("focus_observation", json.dumps({
            "coverage_id": coverage_id, "source": "cosmic", "window_id": "epoch:one",
            "last_observed_at": to_z(horizon),
            "fresh_until": to_z(horizon + timedelta(seconds=8)), "healthy": True,
        }))
        recap = compile_day_recap(store, day, now=horizon)
        brief = compile_care_brief(recap, {"state": "active"}, now=horizon)
    assert len(recap.attention_segments) == 6
    assert recap.focus_switches_context == 0
    assert recap.session_shape == "steady"
    assert "high_switch_rate" not in brief["signals"]
