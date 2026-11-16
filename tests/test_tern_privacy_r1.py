import json
from datetime import datetime
from pathlib import Path

from roxabi_sense.query import SenseQuery
from roxabi_sense.report.event_summary import redact_coarse, summarize_event
from roxabi_sense.store import Store
from roxabi_sense.util.time import to_z


def test_tern_coarse_timeline_never_serializes_private_source_facts(tmp_path: Path) -> None:
    private = {
        "run_ref": "PRIVATE_RUN",
        "endpoint_ref": "PRIVATE_ENDPOINT",
        "window_key": "PRIVATE_ROUTE",
        "marker": "PRIVATE_MARKER",
        "shown_pane": "PRIVATE_SHOWN_PANE",
        "pane_ref": "PRIVATE_PANE",
        "host_ref": "PRIVATE_HOST",
        "repo_ref": "PRIVATE_REPO",
        "session_context_ref": "PRIVATE_SESSION",
        "work_proof_ref": "PRIVATE_WORK_PROOF",
        "proof_ref": "PRIVATE_PROOF",
        "semantic_ref": "PRIVATE_SEMANTIC",
        "cwd": "/PRIVATE_CWD_PARENT/PRIVATE_CWD_NAME",
    }
    payload = {
        "version": 1,
        "run_ref": private["run_ref"],
        "state": "observed",
        "windows": [{
            "endpoint_ref": private["endpoint_ref"],
            "window_key": private["window_key"],
            "marker": private["marker"],
            "shown_pane": private["shown_pane"],
            "panes": [{key: private[key] for key in (
                "pane_ref", "host_ref", "repo_ref", "session_context_ref",
                "work_proof_ref", "cwd",
            )} | {"pane_working": True, "omp_working": True}],
        }],
        "closed": [{"proof_ref": private["proof_ref"],
                    "semantic_ref": private["semantic_ref"]}],
    }
    db = tmp_path / "sense.db"
    local_tz = datetime.now().astimezone().tzinfo
    stamp = to_z(datetime(2026, 1, 1, 12, tzinfo=local_tz))
    with Store(db) as store:
        store.append("tern_snapshot", payload, ts=stamp)

    query = SenseQuery(db_path=db, offline_threshold_s=120, idle_threshold_s=300)
    result = query.what_was_i_doing("2026-01-01")
    assert result["count"] == 1
    assert result["events"][0]["summary"] == "state=observed windows=1 panes=1"
    assert "payload" not in result["events"][0]
    serialized = json.dumps(result)
    for value in private.values():
        for sentinel in value.split("/"):
            if sentinel:
                assert sentinel not in serialized


def test_selected_pane_aliases_are_redacted_and_summary_is_allowlisted() -> None:
    aliases = ("shown_pane", "shown_pane_ref", "selected_pane", "selected_pane_ref",
               "focused_pane", "focused_pane_ref")
    assert redact_coarse({"windows": [{key: "PRIVATE" for key in aliases}]}) == {
        "windows": [{}]
    }
    assert summarize_event("tern_snapshot", {
        "state": "PRIVATE_STATE", "windows": [{"unexpected": "PRIVATE_UNKNOWN"}],
    }) == "state=unknown windows=1 panes=0"
