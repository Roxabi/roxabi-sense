"""Producer-to-report regressions for Tern evidence and native focus."""
import json
from datetime import timedelta

import pytest
from test_tern_consumer_r1 import APP, DAY, TOKEN, Harness

from roxabi_sense.collectors.focus.collector import FocusCollector
from roxabi_sense.collectors.focus.protocol import FocusWindow
from roxabi_sense.collectors.tern.collector import TernCollector
from roxabi_sense.query import SenseQuery
from roxabi_sense.report.care import compile_care_brief
from roxabi_sense.report.day import compile_day_recap
from roxabi_sense.report.dwell.coverage import native_confirmation
from roxabi_sense.store import Store
from roxabi_sense.util.time import parse_ts, to_z


@pytest.fixture
def harness(tmp_path, monkeypatch):
    with Store(tmp_path / "sense.db") as store:
        start, _ = store.day_bounds(DAY)
        yield Harness(store, monkeypatch, parse_ts(start) + timedelta(hours=12))


def test_live_proof_real_title_event_keeps_original_occurrence(harness):
    h = harness
    h.poll()
    beginning = h.now
    h.healthy_for(2400)
    confirmed = h.now
    h.advance(2)
    h.title = "renamed shell"
    h.focus()
    h.advance(1)
    recap, brief = h.recap()
    assert len(h.events("focus")) == 2
    assert recap.current_attention.context_started_at == to_z(beginning)
    assert parse_ts(recap.current_attention.confirmed_through) == confirmed
    assert brief["current_stretch"]["minutes"] == 40
    assert sum(s.duration_s for s in recap.focus_segments if s.context_ref) == 2400


def test_real_short_pane_hop_still_starts_new_occurrence(harness):
    h = harness
    h.poll()
    h.healthy_for(2400)
    h.advance(1)
    h.pane = "pane-b"
    h.poll()
    h.advance(1)
    h.pane = "pane-a"
    restored = h.now
    h.poll()
    h.healthy_for(5)
    recap, brief = h.recap()
    assert recap.current_attention.context_started_at == to_z(restored)
    assert brief["current_stretch"]["minutes"] == 0.08
    assert recap.current_attention.stretch.duration_s == 5


def test_first_enrichment_is_not_a_switch_or_backfilled_occurrence(harness):
    h = harness
    h.focus()
    native_start = h.now
    h.advance(5)
    acquired = h.now
    h.poll()
    h.healthy_for(10)
    recap, _ = h.recap()
    assert len(h.events("focus")) == 2
    assert recap.focus_switches == 0
    assert recap.focus_switches_context == 0
    assert recap.current_attention.context_started_at == to_z(acquired)
    assert recap.current_attention.context_started_at != to_z(native_start)
    assert sum(s.duration_s for s in recap.focus_segments if s.context_ref) == 10


@pytest.mark.parametrize("change", ["title", "window"])
def test_first_enrichment_does_not_hide_real_native_change(harness, change):
    h = harness
    h.focus()
    h.advance(5)
    h.poll()
    h.healthy_for(5)
    h.advance(1)
    if change == "title":
        h.title = "different document"
        h.focus()
    else:
        h.probe.observed_at = h.now
        native = FocusWindow(APP, f"[sense:t1:{TOKEN}] {h.title}", True,
                             window_id="epoch:other-window")
        h.collector.apply(h.store, [native], mode="focus", source="cosmic")
    h.healthy_for(5)
    recap, _ = h.recap()
    assert recap.focus_switches >= 1


def test_duplicate_views_union_work_and_public_outputs_hide_private_refs(harness, tmp_path):
    h = harness
    window = h.window()
    duplicate = {**window, "window_key": "duplicate-route", "registered": False}
    h.writer.publish(h.store, [window, duplicate])
    h.focus()
    for _ in range(3):
        h.advance(5)
        h.writer.publish(h.store, [h.window(), duplicate])
        h.focus()
    recap, _ = h.recap()
    assert recap.tern_work.pane_working_s == 15
    assert recap.tern_work.omp_working_s == 15
    assert sum(row.working_s for row in recap.agent_time_by_repo) == 15
    query = SenseQuery(db_path=tmp_path / "sense.db", offline_threshold_s=120,
                       idle_threshold_s=300)
    public = json.dumps([recap.to_dict(), query.what_was_i_doing(DAY)])
    for private in ("pane_ref", "repo_ref", "context_ref", "work_proof_ref",
                    "pane_work_proof_ref", "process_proof_ref", "agent_proof_ref", "proof_ref",
                    "semantic_ref", "duplicate-route", "pane-a"):
        assert private not in public


def _fractional_clock(h, monkeypatch):
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus.collector._observed_at",
        lambda: h.now.isoformat().replace("+00:00", "Z"),
    )
    return h.now, h.boot


def _fractional_publish(h, base, boot, start, end, publication):
    h.now = base + timedelta(seconds=publication)
    h.boot = boot + publication
    sample = {
        "start_wall": (base + timedelta(seconds=start)).isoformat(),
        "start_boot": boot + start,
        "end_wall": (base + timedelta(seconds=end)).isoformat(),
        "end_boot": boot + end,
    }
    window = h.window()
    pane = window["panes"][0]
    for field in ("_pane_work_sample", "_process_sample", "_selected_sample"):
        pane[field] = sample
    h.writer.publish(h.store, [window])
    h.focus()


def test_fractional_first_point_renewal_and_closed_restart(harness, tmp_path, monkeypatch):
    h = harness
    base, boot = _fractional_clock(h, monkeypatch)

    _fractional_publish(h, base, boot, 0.1, 0.8, 0.9)
    first, _ = h.recap()
    assert first.tern_work.pane_working_s == 0
    assert first.tern_work.omp_working_s == 0
    assert sum(s.duration_s for s in first.focus_segments if s.context_ref) == 0

    _fractional_publish(h, base, boot, 5.1, 5.8, 5.9)
    renewed, _ = h.recap()
    assert renewed.tern_work.pane_working_s == pytest.approx(4.3)
    assert renewed.tern_work.omp_working_s == pytest.approx(4.3)
    assert sum(row.working_s for row in renewed.agent_time_by_repo) == pytest.approx(4.3)
    assert parse_ts(json.loads(h.store.get_meta("focus_observation"))["last_observed_at"]) == (
        base + timedelta(seconds=5)
    )
    contextual = [s for s in renewed.focus_segments if s.context_ref]
    assert sum(s.duration_s for s in contextual) == pytest.approx(4.1)
    assert parse_ts(contextual[0].start) == base + timedelta(seconds=0.9)
    assert parse_ts(contextual[-1].end) == base + timedelta(seconds=5)

    h.writer.shutdown(h.store)
    closed, _ = h.recap()
    assert closed.tern_work.pane_working_s == pytest.approx(4.3)
    assert closed.tern_work.omp_working_s == pytest.approx(4.3)
    with Store(tmp_path / "sense.db") as reopened:
        from roxabi_sense.report.day import compile_day_recap

        after_restart = compile_day_recap(reopened, DAY, now=h.now)
    assert after_restart.tern_work is not None
    assert after_restart.tern_work.pane_working_s == pytest.approx(4.3)
    assert after_restart.tern_work.omp_working_s == pytest.approx(4.3)
    contextual = [s for s in after_restart.focus_segments if s.context_ref]
    assert sum(s.duration_s for s in contextual) == pytest.approx(4.1)
    assert parse_ts(contextual[0].start) == base + timedelta(seconds=0.9)
    assert parse_ts(contextual[-1].end) == base + timedelta(seconds=5)


def test_real_title_event_extends_native_confirmation_not_source_proof(harness, monkeypatch):
    h = harness
    base, boot = _fractional_clock(h, monkeypatch)
    _fractional_publish(h, base, boot, 0.1, 0.8, 0.9)
    h.now = base + timedelta(seconds=5.2)
    h.boot = boot + 5.2
    h.title = "renamed shell"
    h.focus()
    _fractional_publish(h, base, boot, 5.1, 5.8, 5.9)

    events = h.events("focus")
    assert len(events) == 2
    assert parse_ts(events[-1].payload["_observed_at"]) == base + timedelta(seconds=5.2)
    recap, _ = h.recap()
    assert recap.tern_work.pane_working_s == pytest.approx(4.3)
    assert recap.tern_work.omp_working_s == pytest.approx(4.3)
    contextual = [s for s in recap.focus_segments if s.context_ref]
    assert sum(s.duration_s for s in contextual) == pytest.approx(4.2)
    assert parse_ts(contextual[0].start) == base + timedelta(seconds=0.9)
    assert parse_ts(contextual[-1].end) == base + timedelta(seconds=5.1)
    assert parse_ts(recap.current_attention.confirmed_through) == base + timedelta(seconds=5.1)
    assert parse_ts(recap.current_attention.context_started_at) == base + timedelta(seconds=0.9)


def test_new_title_publication_with_stale_native_ack_cannot_extend_confirmation(
    harness, monkeypatch,
):
    h = harness
    base, boot = _fractional_clock(h, monkeypatch)
    _fractional_publish(h, base, boot, 0.1, 0.8, 0.9)
    h.now = base + timedelta(seconds=5.2)
    h.boot = boot + 5.2
    h.title = "renamed shell"
    native = FocusWindow(APP, f"[sense:t1:{TOKEN}] {h.title}", True,
                         window_id="epoch:window")
    # Apply a new title with the old probe acknowledgement, not a new native ACK.
    h.collector.apply(h.store, [native], mode="focus", source="cosmic")
    _fractional_publish(h, base, boot, 5.1, 5.8, 5.9)

    events = h.events("focus")
    assert len(events) == 2
    assert parse_ts(events[-1].payload["_observed_at"]) == base + timedelta(seconds=5.2)
    assert parse_ts(events[-1].payload["_native_observed_at"]) == base + timedelta(seconds=0.9)
    recap, _ = h.recap()
    contextual = [s for s in recap.focus_segments if s.context_ref]
    assert sum(s.duration_s for s in contextual) == pytest.approx(4.1)
    assert parse_ts(contextual[-1].end) == base + timedelta(seconds=5)


def test_new_context_does_not_borrow_preceding_confirmed_stretch(harness, monkeypatch):
    h = harness
    base, boot = _fractional_clock(h, monkeypatch)
    _fractional_publish(h, base, boot, 0.1, 0.8, 0.9)
    _fractional_publish(h, base, boot, 5.1, 5.15, 5.2)
    h.pane = "pane-b"
    h.writer.publish(h.store, [h.window()])
    h.focus()
    recap, brief = h.recap()
    assert len(h.events("focus")) == 2
    assert parse_ts(recap.current_attention.context_started_at) == base + timedelta(seconds=5.2)
    assert recap.current_attention.stretch is None
    assert brief["current_stretch"] is None


def test_same_window_new_epoch_does_not_borrow_previous_bucket_confirmation(
    harness, monkeypatch,
):
    h = harness
    base, boot = _fractional_clock(h, monkeypatch)
    _fractional_publish(h, base, boot, 0.1, 0.8, 0.9)
    _fractional_publish(h, base, boot, 5.1, 5.15, 5.2)
    h.title = "prior epoch"
    h.focus()
    h.advance(0.1)
    h.probe.reason = "disconnected"
    h.focus()
    h.advance(0.1)
    h.probe.reason = "observed"
    h.focus()

    events = h.events("focus")
    assert any(parse_ts(e.payload["_observed_at"]) == base + timedelta(seconds=5.2)
               for e in events)
    assert parse_ts(events[-1].payload["_observed_at"]) == base + timedelta(seconds=5.4)
    coverage = h.events("focus_coverage")
    assert [event.payload["state"] for event in coverage[-3:]] == [
        "observed", "unknown", "observed",
    ]
    recap, brief = h.recap()
    assert recap.current_attention.stretch is None
    assert brief["current_stretch"] is None

    h.healthy_for(10)
    later_events = h.events("focus")
    old = next(e for e in later_events
               if parse_ts(e.payload["_observed_at"]) == base + timedelta(seconds=5.2))
    new = next(e for e in later_events
               if parse_ts(e.payload["_observed_at"]) == base + timedelta(seconds=5.4))
    assert old.id != new.id
    assert old.payload["window_id"] == new.payload["window_id"] == "epoch:window"
    assert old.payload["title"] == new.payload["title"] == "prior epoch"
    assert old.payload["_native_coverage_id"] != new.payload["_native_coverage_id"]
    assert coverage[-3].id < coverage[-2].id < coverage[-1].id
    assert old.payload["_native_coverage_id"] == coverage[-3].id
    assert new.payload["_native_coverage_id"] == coverage[-1].id
    native = native_confirmation(
        coverage, later_events, h.store.get_meta("focus_observation"), coverage[-1].id, h.now
    )
    assert any(
        exclusion.reason == "hard"
        and exclusion.start == base + timedelta(seconds=5.2)
        and exclusion.end == base + timedelta(seconds=5.4)
        for exclusion in native.exclusions
    )
    recovered = next(span for span in native.spans if span.event.id == coverage[-1].id)
    assert recovered.start == base + timedelta(seconds=5.4)
    assert len(later_events) == len(events)
    renewed, _ = h.recap()
    current = renewed.current_attention
    assert current is not None and current.stretch is not None
    assert parse_ts(current.context_started_at) == base + timedelta(seconds=5.4)
    assert parse_ts(current.stretch.start) == base + timedelta(seconds=5.4)
    assert parse_ts(current.stretch.end) <= base + timedelta(seconds=15.4)
    assert current.stretch.duration_s <= 10


def test_process_failure_does_not_cut_independent_pane_work(harness):
    h = harness
    h.poll()
    h.healthy_for(10)
    h.advance(5)
    window = h.window()
    window["panes"][0]["omp_working"] = None
    window["panes"][0]["omp_foreground"] = None
    h.writer.publish(h.store, [window])
    h.focus()
    h.advance(5)
    h.poll()
    recap, _ = h.recap()
    assert recap.tern_work.pane_working_s == 20
    assert recap.tern_work.omp_working_s == 10


def test_restart_fences_old_live_proof_before_native_focus(harness):
    h = harness
    h.poll()
    h.healthy_for(5)
    h.advance(2)
    source = TernCollector()
    h.collector = FocusCollector(focus_probe=h.probe, sessions_loader=lambda: [])
    source.start(h.store)
    h.focus()
    newest = h.events("focus")[-1]
    assert "context" not in newest.payload
    assert "agent" not in newest.payload
    recap, _ = h.recap()
    assert sum(s.duration_s for s in recap.focus_segments if s.context_ref) == 5


@pytest.mark.parametrize(
    ("first_minutes", "second_seconds", "expected"), [(30, 900, 2699.6), (41, 2, 2459.8)]
)
def test_fractional_same_session_window_change_keeps_confirmed_current_run(
    tmp_path, first_minutes, second_seconds, expected,
):
    with Store(tmp_path / "sense.db") as store:
        start = parse_ts(store.day_bounds(DAY)[0]) + timedelta(hours=12)
        switch = start + timedelta(minutes=first_minutes)
        confirmed = switch + timedelta(seconds=second_seconds)
        now = confirmed + timedelta(seconds=1)
        store.append("idle", {"idle": False}, ts=to_z(start))
        agent = {"agent": "agent", "session_id": "session-s"}
        coverage_id = 0
        for stamp, window in ((start, "epoch:a"), (switch, "epoch:b")):
            coverage_id = store.append("focus_coverage", {
                "state": "observed", "source": "cosmic", "window_id": window,
            }, ts=to_z(stamp))
            observed = (stamp + timedelta(milliseconds=200)).isoformat()
            store.append("focus", {
                "app": "editor", "title": "shell", "source": "cosmic",
                "window_id": window, "agent": agent, "_observed_at": observed,
                "_native_observed_at": observed, "_native_coverage_id": coverage_id,
            }, ts=to_z(stamp))
        store.set_meta("focus_observation", json.dumps({
            "coverage_id": coverage_id, "source": "cosmic", "window_id": "epoch:b",
            "last_observed_at": to_z(confirmed),
            "fresh_until": to_z(confirmed + timedelta(seconds=8)), "healthy": True,
        }))
        recap = compile_day_recap(store, DAY, now=now)
        brief = compile_care_brief(recap, {"state": "active"}, now=now)

    current = recap.current_attention
    assert current is not None and current.quality == "observed"
    assert current.window_id == "epoch:b"
    assert current.stretch is not None
    assert current.context_started_at is not None
    assert parse_ts(current.context_started_at) == start + timedelta(milliseconds=200)
    assert parse_ts(current.stretch.start) == start + timedelta(milliseconds=200)
    assert current.stretch.duration_s == pytest.approx(expected)
    assert brief["current_stretch"] is not None
    assert brief["current_stretch"]["minutes"] == round(expected / 60, 2)
    assert "long_focus" in brief["signals"]


@pytest.mark.parametrize("proof_gap", [False, True, "expired"])
def test_tern_shared_pane_handoff_retired_route_keeps_proven_current_run(
    harness, monkeypatch, proof_gap,
):
    h = harness
    base = h.now
    base_boot = h.boot
    markers = ("a" * 16, "b" * 16)
    routes = ("route-a", "route-b")
    retired = False
    selected = 0
    wire = h.probe.wire
    assert wire is not None

    def registrations():
        return [
            {"token": markers[i], "boot_id": "boot",
             "expires_boottime": base_boot + 24 if proof_gap == "expired" and i == 0
             else h.boot + 100,
             "endpoint_ref": "endpoint", "window_key": routes[i], "conn": i + 1, "since": 2}
            for i in range(2) if not (retired and i == 0)
        ]

    monkeypatch.setattr("roxabi_sense.collectors.focus.tern.read_registrations", registrations)

    def windows():
        result = []
        for i in range(2):
            if retired and i == 0:
                continue
            window = {**h.window(), "window_key": routes[i], "conn": i + 1}
            if proof_gap == "expired" and i == 0:
                window["_registration_expires_boot"] = base_boot + 24
            result.append(window)
        return result

    def observe():
        indices = [i for i in range(2) if not (retired and i == 0)]
        wire.handles = {
            i + 1: {"committed": {"identifier": routes[i], "active": i == selected}}
            for i in indices
        }
        wire.ack_observed_at = h.now
        h.probe.observed_at = h.now
        inventory = [
            FocusWindow(APP, f"[sense:t1:{markers[i]}] shell", i == selected,
                        window_id=f"epoch:{routes[i]}")
            for i in indices
        ]
        h.collector.apply(h.store, inventory, mode="focus", source="cosmic")

    def poll():
        h.writer.publish(h.store, windows())
        observe()

    h.advance(0.2)
    poll()
    if proof_gap == "expired":
        for _ in range(3):
            h.advance(5)
            poll()
        h.advance(4.9)
        poll()
        old_focus = h.events("focus")[-1]
        old_ref = old_focus.payload["context"]["proof_ref"]
        h.advance(5.3)
        selected = 1
        observe()
        new_focus = h.events("focus")[-1]
        assert new_focus.payload["context"]["ref"] == old_focus.payload["context"]["ref"]
        assert new_focus.payload["context"]["proof_ref"] != old_ref
        assert parse_ts(new_focus.payload["_observed_at"]) == base + timedelta(seconds=25.4)
        retired = True
        h.advance(2.5)
        poll()
        closed = [item for event in h.events("tern_snapshot")
                  for item in event.payload["closed"] if item["proof_ref"] == old_ref]
        assert len(closed) == 1
        assert parse_ts(closed[0]["confirmed_through"]) == base + timedelta(seconds=20.1)
        assert parse_ts(closed[0]["eligible_until"]) == base + timedelta(seconds=24)
        h.advance(5)
        poll()
        recap, _ = h.recap()
        current = recap.current_attention
        assert current is not None and current.stretch is not None
        assert parse_ts(current.context_started_at) == base + timedelta(seconds=25.4)
        assert parse_ts(current.stretch.start) == base + timedelta(seconds=25.4)
        assert current.stretch.duration_s == pytest.approx(6.6)
        return
    for _ in range(359):
        h.advance(5)
        poll()
    h.advance(4.8)
    h.writer.publish(h.store, windows())
    h.advance(0.2)
    selected = 1
    observe()
    focus = h.events("focus")
    assert len(focus) == 2
    first_context = focus[0].payload["context"]
    second_context = focus[1].payload["context"]
    assert first_context["ref"] == second_context["ref"]
    assert first_context["proof_ref"] != second_context["proof_ref"]

    retired = True
    h.advance(5)
    poll()
    closures = [closed for event in h.events("tern_snapshot")
                for closed in event.payload["closed"]]
    assert any(item["proof_ref"] == first_context["proof_ref"] for item in closures)
    recovery = None
    for step in range(179):
        h.advance(5)
        if proof_gap and step == 90:
            h.writer.publish(h.store, None)
            observe()
            h.advance(5)
            recovery = h.now
        poll()
    recap, brief = h.recap()
    current = recap.current_attention
    assert current is not None and current.stretch is not None
    assert current.window_id == "epoch:route-b"
    if proof_gap:
        assert recovery is not None
        assert parse_ts(current.context_started_at) == recovery
        assert parse_ts(current.stretch.start) == recovery
        assert current.stretch.duration_s == pytest.approx(439.8)
        assert "long_focus" not in brief["signals"]
    else:
        assert parse_ts(current.context_started_at) == base + timedelta(seconds=0.2)
        assert parse_ts(current.stretch.start) == base + timedelta(seconds=0.2)
        assert current.stretch.duration_s == pytest.approx(2699.6)
        assert brief["current_stretch"]["minutes"] == 44.99
        assert "long_focus" in brief["signals"]


def test_empty_native_gap_after_last_proof_cannot_resurrect_old_stretch(harness):
    h = harness
    base = h.now
    h.advance(0.1)
    h.poll()
    h.healthy_for(41 * 60)
    assert h.now == base + timedelta(minutes=41, seconds=0.1)
    h.advance(0.9)
    h.focus()
    before = h.events("focus")
    h.advance(0.2)
    h.probe.reason = "disconnected"
    h.collector.apply(h.store, [], mode="focus", source="cosmic")
    assert len(h.events("focus")) == len(before)
    h.advance(0.2)
    h.probe.reason = "observed"
    recovered = h.now
    h.focus()
    after = h.events("focus")
    assert len(after) == len(before) + 1
    assert after[-1].payload["context"]["ref"] == before[-1].payload["context"]["ref"]
    assert after[-1].payload["context"]["proof_ref"] == before[-1].payload["context"]["proof_ref"]
    assert [event.payload["state"] for event in h.events("focus_coverage")[-3:]] == [
        "observed", "unknown", "observed",
    ]
    h.advance(0.6)
    recap, brief = h.recap()
    assert recap.current_attention is not None
    assert recap.current_attention.stretch is None
    assert brief["current_stretch"] is None
    assert "long_focus" not in brief["signals"]

    h.advance(3.4)
    h.poll()
    h.advance(5)
    h.poll()
    renewed, brief = h.recap()
    assert renewed.current_attention is not None
    assert renewed.current_attention.stretch is not None
    assert parse_ts(renewed.current_attention.context_started_at) == base + timedelta(seconds=0.1)
    assert parse_ts(renewed.current_attention.coverage_started_at) == recovered
    assert parse_ts(renewed.current_attention.stretch.start) == recovered
    assert renewed.current_attention.stretch.duration_s == pytest.approx(8.6)
    assert "long_focus" not in brief["signals"]


@pytest.mark.parametrize("post_reentry_seconds", [0, 10])
def test_native_unknown_legacy_native_reentry_excludes_legacy_current_fragments(
    tmp_path, post_reentry_seconds,
):
    with Store(tmp_path / "sense.db") as store:
        start = parse_ts(store.day_bounds(DAY)[0]) + timedelta(hours=12)
        reentry = start + timedelta(seconds=30)
        confirmed = reentry + timedelta(seconds=post_reentry_seconds)
        now = confirmed + timedelta(seconds=1)
        agent = {"agent": "agent", "session_id": "session-s"}
        store.append("idle", {"idle": False}, ts=to_z(start))
        first = store.append("focus_coverage", {
            "state": "observed", "source": "cosmic", "window_id": "epoch:a",
        }, ts=to_z(start))
        store.append("focus", {
            "app": "editor", "title": "shell", "source": "cosmic", "window_id": "epoch:a",
            "agent": agent, "_observed_at": to_z(start), "_native_observed_at": to_z(start),
            "_native_coverage_id": first,
        }, ts=to_z(start))
        store.append("focus_coverage", {"state": "unknown", "source": "cosmic"},
                     ts=to_z(start + timedelta(seconds=10)))
        store.append("focus_legacy", {"source": "atspi"},
                     ts=to_z(start + timedelta(seconds=15)))
        store.append("focus", {
            "app": "editor", "title": "shell", "source": "atspi", "agent": agent,
            "_observed_at": to_z(start + timedelta(seconds=15)),
        }, ts=to_z(start + timedelta(seconds=15)))
        last = store.append("focus_coverage", {
            "state": "observed", "source": "cosmic", "window_id": "epoch:b",
        }, ts=to_z(reentry))
        store.append("focus", {
            "app": "editor", "title": "shell", "source": "cosmic", "window_id": "epoch:b",
            "agent": agent, "_observed_at": to_z(reentry),
            "_native_observed_at": to_z(reentry), "_native_coverage_id": last,
        }, ts=to_z(reentry))
        store.set_meta("focus_observation", json.dumps({
            "coverage_id": last, "source": "cosmic", "window_id": "epoch:b",
            "last_observed_at": to_z(confirmed),
            "fresh_until": to_z(confirmed + timedelta(seconds=8)), "healthy": True,
        }))
        recap = compile_day_recap(store, DAY, now=now)
        brief = compile_care_brief(recap, {"state": "active"}, now=now)

    current = recap.current_attention
    assert current is not None and current.quality == "observed"
    assert current.window_id == "epoch:b"
    if post_reentry_seconds == 0:
        assert current.stretch is None
        assert brief["current_stretch"] is None
    else:
        assert current.stretch is not None
        assert parse_ts(current.stretch.start) == reentry
        assert current.stretch.duration_s == 10
        assert brief["current_stretch"]["minutes"] == 0.17
