"""Producer-to-report regressions for Tern evidence and native focus."""
import json
from datetime import timedelta

import pytest
from test_tern_consumer_r1 import APP, DAY, TOKEN, Harness

from roxabi_sense.collectors.focus.collector import FocusCollector
from roxabi_sense.collectors.focus.protocol import FocusWindow
from roxabi_sense.collectors.tern.collector import TernCollector
from roxabi_sense.query import SenseQuery
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
