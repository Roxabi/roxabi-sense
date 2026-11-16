"""Tern proof censoring is distinct from a human attention transition."""
import json
from datetime import timedelta
from typing import Any

import pytest

from roxabi_sense.report.day import compile_day_recap
from roxabi_sense.report.dwell.tern_proofs import ProofIndex, current_context, project_timeline
from roxabi_sense.report.segments import focus_segments, is_terminal_app
from roxabi_sense.store import Event, Store
from roxabi_sense.util.time import parse_ts, to_z


def _focus(proof: str | None = "p1", ref="context-a", title="Tern", window="epoch:w", *, at=None):
    payload: dict[str, Any] = {"app": "so.stencil.tern", "title": title, "source": "cosmic",
                               "window_id": window}
    if at is not None:
        payload["_observed_at"] = at.isoformat().replace("+00:00", "Z")
    if proof is not None:
        payload["context"] = {"source": "tern", "ref": ref, "repo_ref": "repo",
                              "cwd": "/projects/demo", "proof_ref": proof}
        payload["agent"] = {"agent": "omp", "match": "tern_foreground"}
    return payload


def _meta(store, snapshot, start, observed, proof="p1"):
    store.set_meta("tern_observation", json.dumps({
        "run_ref": "run", "snapshot_id": snapshot, "healthy": True,
        "scopes": {"selected": {"proof_ref": proof, "started_at": to_z(start),
                                 "last_observed_at": to_z(observed),
                                 "fresh_until": to_z(observed + timedelta(seconds=10))}},
    }))


def _coverage(store, start, observed, window="epoch:w"):
    coverage = store.append("focus_coverage", {"state": "observed", "source": "cosmic",
                                               "window_id": window}, ts=to_z(start))
    store.set_meta("focus_observation", json.dumps({
        "coverage_id": coverage, "source": "cosmic", "window_id": window,
        "last_observed_at": to_z(observed), "fresh_until": to_z(observed + timedelta(seconds=8)),
        "healthy": True,
    }))


def test_live_proof_keeps_current_run_but_never_extends_confirmed_history(tmp_path):
    day = "2026-07-30"
    with Store(tmp_path / "sense.db") as store:
        start = parse_ts(store.day_bounds(day)[0]) + timedelta(hours=12)
        observed = start + timedelta(minutes=40)
        _coverage(store, start, observed + timedelta(seconds=1))
        snapshot = store.append("tern_snapshot", {"run_ref": "run", "closed": [],
                                                   "state": "observed", "windows": []},
                                ts=to_z(start))
        store.append("focus", _focus(at=start), ts=to_z(start))
        _meta(store, snapshot, start, observed)
        recap = compile_day_recap(store, day, now=observed + timedelta(seconds=2))
    assert recap.current_attention is not None
    assert recap.current_attention.stretch is not None
    assert recap.current_attention.stretch.start == to_z(start)
    assert recap.current_attention.stretch.end == to_z(observed)
    assert recap.current_attention.stretch.duration_s == 2400
    assert recap.focus_segments[0].end == to_z(observed)
    assert recap.current_attention.confirmed_through == to_z(observed)
    assert recap.focus_switches_context == 0
    assert recap.terminal_stays.visits >= 1
    assert is_terminal_app("so.stencil.tern")


@pytest.mark.parametrize(("title", "ref", "expected_title", "expected_context"), [
    ("Tern", "context-a", 0, 0),
    ("new title", "context-a", 1, 0),
    ("Tern", "context-b", 1, 1),
])
def test_recovery_counts_human_change_but_not_proof_replacement(
    tmp_path, title, ref, expected_title, expected_context
):
    day = "2026-07-30"
    with Store(tmp_path / "sense.db") as store:
        start = parse_ts(store.day_bounds(day)[0]) + timedelta(hours=12)
        end1 = start + timedelta(minutes=5)
        failure = end1 + timedelta(seconds=5)
        recovery = end1 + timedelta(minutes=5)
        observed = recovery + timedelta(minutes=5)
        _coverage(store, start, observed)
        store.append("tern_snapshot", {"run_ref": "run", "closed": [], "windows": []},
                     ts=to_z(start))
        store.append("focus", _focus(at=start), ts=to_z(start))
        store.append("tern_snapshot", {"run_ref": "run", "state": "unknown", "windows": [],
                     "closed": [{"proof_ref": "p1", "started_at": to_z(start),
                                 "confirmed_through": to_z(end1)}]},
                     ts=to_z(failure))
        store.append("focus", _focus(proof=None, at=failure), ts=to_z(failure))
        snapshot = store.append("tern_snapshot", {"run_ref": "run", "state": "observed",
                                                   "closed": [], "windows": []},
                                ts=to_z(recovery))
        store.append("focus", _focus("p2", ref, title, at=recovery), ts=to_z(recovery))
        _meta(store, snapshot, recovery, observed, "p2")
        recap = compile_day_recap(store, day, now=observed + timedelta(seconds=1))
    assert recap.focus_switches == expected_title
    assert recap.focus_switches_context == expected_context
    assert recap.current_attention is not None
    assert recap.current_attention.stretch is not None
    assert recap.current_attention.stretch.start == to_z(recovery)
    assert recap.current_attention.stretch.end == to_z(observed)


def test_recovery_in_another_native_window_is_not_a_proof_cut():
    from roxabi_sense.report.dwell.tern_proofs import switch_metrics

    start = parse_ts("2026-07-30T12:00:00Z")
    end1 = start + timedelta(minutes=5)
    recovery = start + timedelta(minutes=10)
    end2 = start + timedelta(minutes=15)
    first = Event(1, to_z(start), "focus", _focus(at=start))
    restored = Event(2, to_z(recovery), "focus", _focus("p2", window="epoch:other", at=recovery))
    snapshot = Event(3, to_z(recovery), "tern_snapshot", {
        "run_ref": "run", "closed": [
            {"proof_ref": "p1", "started_at": to_z(start), "confirmed_through": to_z(end1)},
            {"proof_ref": "p2", "started_at": to_z(recovery), "confirmed_through": to_z(end2)},
        ],
    })
    projection = project_timeline([first, restored], [snapshot], None, end2)
    assert any(record.at == end1 and not record.context_change for record in projection.records)
    assert any(record.at == recovery and record.context_change for record in projection.records)
    segments = focus_segments(projection, [], horizon=end2)
    assert switch_metrics(projection, segments, segments)[0] == 1


def test_live_proof_keeps_real_title_updates_after_confirmation(tmp_path):
    day = "2026-07-30"
    with Store(tmp_path / "sense.db") as store:
        start = parse_ts(store.day_bounds(day)[0]) + timedelta(hours=12)
        confirmed = start + timedelta(minutes=40)
        now = confirmed + timedelta(seconds=4)
        _coverage(store, start, now)
        snapshot = store.append("tern_snapshot", {"run_ref": "run", "closed": [],
                                                   "state": "observed", "windows": []},
                                ts=to_z(start))
        store.append("focus", _focus(at=start), ts=to_z(start))
        first_update = confirmed + timedelta(seconds=1)
        second_update = confirmed + timedelta(seconds=2)
        store.append("focus", _focus(title="first update", at=first_update), ts=to_z(first_update))
        store.append(
            "focus", _focus(title="second update", at=second_update), ts=to_z(second_update)
        )
        _meta(store, snapshot, start, confirmed)
        recap = compile_day_recap(store, day, now=now)
    assert recap.current_attention is not None
    assert recap.current_attention.stretch is not None
    assert recap.current_attention.stretch.start == to_z(start)
    assert recap.current_attention.stretch.end == to_z(confirmed)
    assert recap.current_attention.confirmed_through == to_z(confirmed)


def test_first_knowledge_gain_is_not_a_human_switch(tmp_path):
    day = "2026-07-30"
    with Store(tmp_path / "sense.db") as store:
        start = parse_ts(store.day_bounds(day)[0]) + timedelta(hours=12)
        gained = start + timedelta(seconds=5)
        confirmed = start + timedelta(seconds=12)
        _coverage(store, start, confirmed)
        store.append("focus", _focus(proof=None, at=start), ts=to_z(start))
        snapshot = store.append("tern_snapshot", {"run_ref": "run", "closed": [],
                                                   "state": "observed", "windows": []},
                                ts=to_z(gained))
        store.append("focus", _focus(at=gained), ts=to_z(gained))
        _meta(store, snapshot, gained, confirmed)
        recap = compile_day_recap(store, day, now=confirmed + timedelta(seconds=1))
    assert recap.focus_switches == recap.focus_switches_context == 0
    assert recap.current_attention is not None
    assert recap.current_attention.stretch is not None
    assert recap.current_attention.stretch.start == to_z(gained)


def test_fractional_proof_and_independent_agent_intervals():
    start = parse_ts("2026-07-30T12:00:00Z")
    structural_start = start + timedelta(microseconds=800000)
    agent_start = start + timedelta(seconds=4, microseconds=500000)
    agent_end = start + timedelta(seconds=8, microseconds=250000)
    end = start + timedelta(seconds=12, microseconds=500000)
    payload = _focus(at=start)
    payload["context"]["agent_proof_ref"] = "agent-proof"
    first = Event(1, to_z(start), "focus", payload)
    snapshot = Event(2, to_z(start), "tern_snapshot", {
        "run_ref": "run", "closed": [
            {"proof_ref": "p1", "started_at": structural_start.isoformat(),
             "confirmed_through": end.isoformat()},
            {"proof_ref": "agent-proof", "started_at": agent_start.isoformat(),
             "confirmed_through": agent_end.isoformat()},
        ],
    })
    projection = project_timeline([first], [snapshot], None, end + timedelta(seconds=1))
    segments = focus_segments(projection, [], horizon=end + timedelta(seconds=1))
    contextual = [s for s in segments if s.context_ref]
    assert contextual[0].start == structural_start.isoformat().replace("+00:00", "Z")
    assert contextual[-1].end == end.isoformat().replace("+00:00", "Z")
    assert all(parse_ts(s.start) >= agent_start and parse_ts(s.end) <= agent_end
               for s in contextual if s.agent)
    assert sum(s.duration_s for s in contextual if s.agent) == 3.75


def test_closed_and_conflicting_proofs_cannot_be_revived_by_live_meta():
    start = parse_ts("2026-07-30T12:00:00Z")
    later = start + timedelta(seconds=10)
    first = Event(1, to_z(start), "focus", _focus(at=start))
    closed = [{"proof_ref": "p1", "started_at": to_z(start),
               "confirmed_through": to_z(later)},
              {"proof_ref": "p1", "started_at": to_z(start + timedelta(seconds=1)),
               "confirmed_through": to_z(later)}]
    snapshot = Event(2, to_z(start), "tern_snapshot", {"run_ref": "run", "closed": closed})
    meta = json.dumps({"run_ref": "run", "snapshot_id": 2, "healthy": True, "scopes": {
        "selected": {"proof_ref": "p1", "started_at": to_z(start),
                     "last_observed_at": to_z(later),
                     "fresh_until": to_z(later + timedelta(seconds=10))}}})
    index = ProofIndex([snapshot], meta)
    assert "p1" not in index.bounds
    assert "context" not in current_context(first, index, later).payload


def test_first_fractional_point_has_no_credited_context():
    start = parse_ts("2026-07-30T12:00:00Z")
    point = start + timedelta(microseconds=800000)
    snapshot = Event(2, to_z(start), "tern_snapshot", {
        "run_ref": "run", "closed": [{"proof_ref": "p1", "started_at": point.isoformat(),
                                        "confirmed_through": point.isoformat()}],
    })
    projection = project_timeline([Event(1, to_z(start), "focus", _focus(at=start))],
                                  [snapshot], None, start + timedelta(seconds=5))
    segments = focus_segments(projection, [], horizon=start + timedelta(seconds=5))
    assert all(segment.context_ref is None for segment in segments)


def test_short_raw_visit_in_live_unconfirmed_tail_cannot_resurrect_old_run(tmp_path):
    day = "2026-07-30"
    with Store(tmp_path / "sense.db") as store:
        start = parse_ts(store.day_bounds(day)[0]) + timedelta(hours=12)
        confirmed = start + timedelta(minutes=40)
        now = confirmed + timedelta(seconds=4)
        _coverage(store, start, now)
        snapshot = store.append("tern_snapshot", {"run_ref": "run", "closed": [],
                                                   "state": "observed", "windows": []},
                                ts=to_z(start))
        store.append("focus", _focus(at=start), ts=to_z(start))
        visit = confirmed + timedelta(seconds=1)
        recovery = confirmed + timedelta(seconds=2)
        store.append("focus", _focus(ref="context-b", at=visit), ts=to_z(visit))
        store.append("focus", _focus(at=recovery), ts=to_z(recovery))
        _meta(store, snapshot, start, confirmed)
        recap = compile_day_recap(store, day, now=now)
    assert recap.current_attention is not None
    assert recap.current_attention.context_started_at == to_z(confirmed + timedelta(seconds=2))
    assert recap.current_attention.stretch is None


@pytest.mark.parametrize(("exact", "expected_offset"), [(True, 0.9), (False, 1.0)])
def test_context_never_precedes_focus_publication_or_coarse_bucket_end(exact, expected_offset):
    start = parse_ts("2026-07-30T12:00:00Z")
    actual_focus = start + timedelta(milliseconds=900)
    proof_start = start + timedelta(milliseconds=800)
    confirmed = start + timedelta(seconds=5, milliseconds=100)
    horizon = start + timedelta(seconds=6)
    event = Event(1, to_z(start), "focus", _focus(at=actual_focus if exact else None))
    snapshot = Event(2, to_z(start), "tern_snapshot", {"run_ref": "run", "closed": [{
        "proof_ref": "p1", "started_at": proof_start.isoformat(),
        "confirmed_through": confirmed.isoformat(),
    }]})
    projection = project_timeline([event], [snapshot], None, horizon)
    segments = focus_segments(projection, [], horizon=horizon)
    contextual = [segment for segment in segments if segment.context_ref]
    assert len(contextual) == 1
    assert parse_ts(contextual[0].start) == start + timedelta(seconds=expected_offset)
    assert parse_ts(contextual[0].end) == confirmed
