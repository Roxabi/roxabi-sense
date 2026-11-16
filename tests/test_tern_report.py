import json
from datetime import UTC, datetime

from roxabi_sense.report.dwell.repos import RepoAttribution, attribute_repos
from roxabi_sense.report.dwell.tern_proofs import ProofIndex, current_context, project_timeline
from roxabi_sense.report.dwell.tern_work import add_tern_work, summarize_tern_work
from roxabi_sense.report.event_summary import redact_coarse
from roxabi_sense.report.segments import FocusSegment, focus_segments
from roxabi_sense.store import Event


def ts(minute: int) -> str:
    return f"2026-01-01T00:{minute:02d}:00Z"


def observation(snapshot_id=1):
    return json.dumps({"run_ref": "run", "snapshot_id": snapshot_id, "healthy": True,
                       "scopes": {"scope": {"proof_ref": "proof", "semantic_ref": "s",
                       "started_at": ts(0), "last_observed_at": ts(20),
                       "fresh_until": ts(21)}}})


def focus():
    return Event(2, ts(0), "focus", {"app": "so.stencil.tern", "title": "Tern",
                 "_observed_at": ts(0),
                 "window_id": "window", "context": {"source": "tern", "ref": "context",
                 "repo_ref": "repo", "cwd": "/projects/demo", "proof_ref": "proof",
                 "agent_proof_ref": "proof"},
                 "agent": {"agent": "omp", "match": "tern_foreground"}})


def test_proof_renewal_and_expiry_preserve_native_focus():
    snapshot = Event(1, ts(0), "tern_snapshot", {"run_ref": "run", "closed": []})
    horizon = datetime(2026, 1, 1, 0, 22, tzinfo=UTC)
    projection = project_timeline([focus()], [snapshot], observation(), horizon,
                                  history_ids={focus().id})
    normalized = [record.history for record in projection.records if record.history is not None]
    assert normalized[1].payload["app"] == "so.stencil.tern"
    assert "context" not in normalized[1].payload and "agent" not in normalized[1].payload
    segments = focus_segments(projection, [], horizon=horizon)
    assert segments[0].duration_s == 1200
    assert segments[0].context_ref == "context"
    assert segments[1].context_ref is None
    index = ProofIndex([snapshot], observation())
    assert current_context(
        focus(), index, datetime(2026, 1, 1, 0, 20, 30, tzinfo=UTC)
    ) == focus()
    assert "context" not in current_context(focus(), index, horizon).payload


def test_closed_proof_wins_over_mismatched_meta():
    snapshot = Event(3, ts(10), "tern_snapshot", {"run_ref": "other", "closed": [
        {"proof_ref": "proof", "started_at": ts(0), "confirmed_through": ts(5)}]})
    projection = project_timeline([focus()], [snapshot], observation(),
                                  datetime(2026, 1, 1, 0, 22, tzinfo=UTC),
                                  history_ids={focus().id})
    assert projection.records[1].at == datetime(2026, 1, 1, 0, 5, tzinfo=UTC)


def test_shared_work_views_union_and_unknown_attention():
    pane = {"cwd": "/projects/demo", "pane_working": True, "omp_foreground": True,
            "pane_work_proof_ref": "proof", "process_proof_ref": "proof"}
    snapshot = Event(1, ts(0), "tern_snapshot", {"run_ref": "run", "state": "observed",
                     "closed": [], "windows": [{"panes": [pane]}, {"panes": [pane]}]})
    result = add_tern_work(RepoAttribution([], [], None), [snapshot], observation(), [],
                           datetime(2026, 1, 1, tzinfo=UTC),
                           datetime(2026, 1, 1, 0, 22, tzinfo=UTC))
    assert result.agents[0].working_s == 1200
    assert result.agents[0].unfocused_s == 0


def test_coarse_drops_private_refs():
    assert redact_coarse({"context_ref": "secret", "repo_ref": "secret", "marker": "secret",
                          "window_key": "secret", "pane_work_proof_ref": "secret",
                          "process_proof_ref": "secret", "agent_proof_ref": "secret",
                          "app": "Tern"}) == {"app": "Tern"}


def test_coarse_redacts_standalone_eligibility_deadline():
    assert redact_coarse({"eligible_until": "private-expiry", "state": "observed"}) == {
        "state": "observed",
    }


def test_coarse_redacts_precise_observation_recursively_and_in_json():
    private = "2026-01-01T00:00:00.123456Z"
    coarse = redact_coarse({"_observed_at": private, "_native_observed_at": private,
                            "app": "Tern", "nested": [
        {"_observed_at": private, "_native_observed_at": private, "_published_at": private,
         "_native_coverage_id": "private-id", "state": "observed"}
    ]})
    assert coarse == {"app": "Tern", "nested": [{"state": "observed"}]}
    serialized = json.dumps(coarse)
    assert all(key not in serialized for key in (
        "_observed_at", "_native_observed_at", "_native_coverage_id", "_published_at",
        private, "private-id",
    ))


def test_global_work_attention_is_unknown_without_native_coverage():
    pane = {"pane_ref": "p", "pane_working": True, "omp_foreground": True,
            "pane_work_proof_ref": "proof", "process_proof_ref": "proof"}
    snapshot = Event(1, ts(0), "tern_snapshot", {"run_ref": "run", "state": "observed",
                     "closed": [], "windows": [{"panes": [pane]}, {"panes": [pane]}]})
    result = summarize_tern_work([snapshot], observation(), [],
                                 datetime(2026, 1, 1, tzinfo=UTC),
                                 datetime(2026, 1, 1, 0, 22, tzinfo=UTC))
    assert result.pane_working_s == result.omp_working_s == 1200
    assert result.unknown_attention_s == 1200
    assert result.attended_s == result.unattended_s == 0


def test_tern_repo_focus_never_uses_herdr_focused_pane():
    herdr = Event(1, ts(0), "herdr_snapshot", {"focused": {"cwd": "/projects/wrong"}})
    known = FocusSegment(ts(0), ts(5), 300, "so.stencil.tern", "Tern",
                         cwd="/projects/right", context_ref="ctx", repo_ref="repo")
    unknown = FocusSegment(ts(5), ts(10), 300, "so.stencil.tern", "Tern")
    result = attribute_repos([herdr], [known, unknown],
                             window_start=datetime(2026, 1, 1, tzinfo=UTC),
                             horizon=datetime(2026, 1, 1, 0, 10, tzinfo=UTC))
    assert result.focus == [("right", 300)]
