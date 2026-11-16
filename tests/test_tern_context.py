"""Exact native-to-route joins fail closed on uncertainty."""
from datetime import UTC, datetime, timedelta

from roxabi_sense.collectors.focus.protocol import FocusWindow
from roxabi_sense.collectors.tern.context import resolve_context, split_marker
from roxabi_sense.collectors.tern.metadata import opaque, selected_semantic
from roxabi_sense.util.time import to_z

TOKEN = "a" * 16
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def evidence():
    native = FocusWindow(app="so.stencil.tern", title=f"[sense:t1:{TOKEN}] Tern", active=True,
                         window_id="epoch:window")
    route = {"endpoint_ref": "endpoint", "window_key": "", "conn": 1, "since": 2}
    pane = {"pane_ref": "pane", "host_ref": "host", "cwd": "/repo", "repo_ref": "repo",
            "session_context_ref": "session", "omp_foreground": True,
            "pane_working": True, "omp_working": True, "selected": True}
    window = {**route, "shown_pane": "pane", "panes": [pane]}
    snapshot = {"run_ref": "run", "state": "observed", "windows": [window]}
    scope = opaque("selected-scope", ["endpoint", "", 1, 2])
    observation = {"run_ref": "run", "healthy": True, "scopes": {scope: {
        "semantic_ref": opaque("selected", selected_semantic(pane)), "proof_ref": "proof",
        "fresh_until": to_z(NOW + timedelta(seconds=10))}}}
    registration = {**route, "token": TOKEN, "boot_id": "boot", "expires_boottime": 20}
    return native, snapshot, observation, registration


def resolve(native, inventory, snapshot, observation, registrations, *, now=NOW, boot=10):
    return resolve_context(
        native, inventory, snapshot, observation, registrations, now, boot, "boot"
    )


def test_empty_route_resolves_and_marker_is_removed_before_title_cleanup():
    native, snapshot, observation, reg = evidence()
    context, reason = resolve(native, [native], snapshot, observation, [reg])
    assert reason is None
    assert context is not None
    assert context["agent"] == {"agent": "omp", "match": "tern_foreground"}
    assert split_marker(native.title) == (TOKEN, "Tern")


def test_duplicate_marker_stale_lease_or_wrong_client_fail_closed():
    native, snapshot, observation, reg = evidence()
    duplicate = FocusWindow(
        app="so.stencil.tern", title=native.title, active=False, window_id="other"
    )
    assert resolve(native, [native, duplicate], snapshot, observation, [reg])[0] is None
    assert resolve(native, [native], snapshot, observation, [reg], boot=21)[0] is None
    reg["since"] = 99
    assert resolve(native, [native], snapshot, observation, [reg])[0] is None


def test_wrong_endpoint_stale_proof_and_invented_inventory_fail_closed():
    native, snapshot, observation, reg = evidence()
    reg["endpoint_ref"] = "another-endpoint"
    assert resolve(native, [native], snapshot, observation, [reg])[0] is None
    reg["endpoint_ref"] = "endpoint"
    assert resolve(
        native, [native], snapshot, observation, [reg], now=NOW + timedelta(seconds=11)
    )[0] is None
    other = FocusWindow(app=native.app, title=native.title, active=True, window_id="other")
    assert resolve(native, [other], snapshot, observation, [reg])[0] is None


def test_work_state_change_does_not_change_selected_context_proof():
    native, snapshot, observation, reg = evidence()
    before, _ = resolve(native, [native], snapshot, observation, [reg])
    snapshot["windows"][0]["panes"][0]["pane_working"] = False
    snapshot["windows"][0]["panes"][0]["omp_working"] = False
    after, _ = resolve(native, [native], snapshot, observation, [reg])
    assert before == after
