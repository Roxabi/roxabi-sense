"""Tern proof censoring is distinct from a human attention transition."""
import json
from datetime import timedelta
from typing import Any

import pytest

from roxabi_sense.report.day import compile_day_recap
from roxabi_sense.report.segments import is_terminal_app
from roxabi_sense.store import Store
from roxabi_sense.util.time import parse_ts, to_z


def _focus(proof: str | None = "p1", ref="context-a", title="Tern", window="epoch:w"):
    payload: dict[str, Any] = {"app": "so.stencil.tern", "title": title, "source": "cosmic",
               "window_id": window}
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
        store.append("focus", _focus(), ts=to_z(start))
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
        store.append("focus", _focus(), ts=to_z(start))
        store.append("tern_snapshot", {"run_ref": "run", "state": "unknown", "windows": [],
                     "closed": [{"proof_ref": "p1", "confirmed_through": to_z(end1)}]},
                     ts=to_z(failure))
        store.append("focus", _focus(proof=None), ts=to_z(failure))
        snapshot = store.append("tern_snapshot", {"run_ref": "run", "state": "observed",
                                                   "closed": [], "windows": []},
                                ts=to_z(recovery))
        store.append("focus", _focus("p2", ref, title), ts=to_z(recovery))
        _meta(store, snapshot, recovery, observed, "p2")
        recap = compile_day_recap(store, day, now=observed + timedelta(seconds=1))
    assert recap.focus_switches == expected_title
    assert recap.focus_switches_context == expected_context
    assert recap.current_attention is not None
    assert recap.current_attention.stretch is not None
    assert recap.current_attention.stretch.start == to_z(recovery)
    assert recap.current_attention.stretch.end == to_z(observed)


def test_recovery_in_another_native_window_is_not_a_proof_cut():
    from roxabi_sense.report.dwell.tern_proofs import normalize_focus
    from roxabi_sense.store import Event

    start = parse_ts("2026-07-30T12:00:00Z")
    end1 = start + timedelta(minutes=5)
    recovery = start + timedelta(minutes=10)
    end2 = start + timedelta(minutes=15)
    first = Event(1, to_z(start), "focus", _focus())
    restored = Event(2, to_z(recovery), "focus", _focus("p2", window="epoch:other"))
    snapshot = Event(3, to_z(recovery), "tern_snapshot", {
        "run_ref": "run", "closed": [{"proof_ref": "p1", "confirmed_through": to_z(end1)},
                                        {"proof_ref": "p2", "confirmed_through": to_z(end2)}],
    })
    _, cuts = normalize_focus([first, restored], [snapshot], None, end2)
    assert to_z(end1) in cuts
    assert to_z(recovery) not in cuts
