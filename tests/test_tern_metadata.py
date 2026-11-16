"""Tern metadata never derives process ownership from retained session references."""
from copy import deepcopy

import pytest

from roxabi_sense.collectors.tern.metadata import clients, parse_window, selected_pane
from roxabi_sense.collectors.tern.runner import TernUnavailable


def fixture():
    return {
        "clients": [{"kind": "window", "window": "", "conn": 1, "since": 2}],
        "sessions": [{"shown": True, "tabs": [{"shown": True, "blocks": [
            {"id": 4, "live": True, "focused": True, "exited": None},
        ]}]}],
        "panes": [{"pane": 4, "identity": {"hops": []}, "cwd": "/repo",
                   "omp_session_file": "/private/session", "working": True, "exited": None}],
    }


def test_empty_route_and_old_session_reference_do_not_prove_omp():
    data = fixture()
    route = clients(data)[0]
    parsed = parse_window(data, route, "endpoint", lambda _key, pane: {
        "pane": pane, "foreground": {"name": "bash"}})
    assert route["window_key"] == ""
    assert parsed["shown_pane"] == parsed["panes"][0]["pane_ref"]
    assert parsed["panes"][0]["omp_foreground"] is False
    assert parsed["panes"][0]["omp_working"] is None


def test_foreground_process_is_required_and_failure_is_unknown():
    data = fixture()
    def unavailable(_key, _pane):
        raise TernUnavailable("failed")
    pane = parse_window(data, clients(data)[0], "endpoint", unavailable)["panes"][0]
    assert pane["omp_foreground"] is None
    assert pane["omp_working"] is None


def test_only_exact_pane_foreground_proves_omp():
    data = fixture()
    route = clients(data)[0]
    pane = parse_window(data, route, "endpoint", lambda _key, pane: {
        "pane": pane, "foreground": {"name": "omp"}
    })["panes"][0]
    assert pane["omp_foreground"] is True
    assert pane["omp_working"] is True
    mismatch = parse_window(data, route, "endpoint", lambda _key, _pane: {
        "pane": 999, "foreground": {"name": "omp"}
    })["panes"][0]
    assert mismatch["omp_foreground"] is None


def test_hidden_and_ambiguous_selection_is_unknown():
    data = fixture()
    data["sessions"][0]["tabs"].append(deepcopy(data["sessions"][0]["tabs"][0]))
    assert selected_pane(data) is None
    data["sessions"][0]["tabs"][1]["shown"] = False
    data["sessions"][0]["tabs"][0]["blocks"][0]["exited"] = 0
    assert selected_pane(data) is None


def test_reused_client_key_does_not_match_old_incarnation():
    data = fixture()
    route = clients(data)[0]
    data["clients"][0]["since"] = 3
    with pytest.raises(TernUnavailable):
        parse_window(data, route, "endpoint", lambda *_: {})


def test_shared_underlying_pane_is_stable_across_window_clients():
    first = fixture()
    second = deepcopy(first)
    second["clients"][0].update(window="2", conn=3, since=4)
    def get(data):
        return parse_window(data, clients(data)[0], "endpoint", lambda *_: {})["panes"][0]
    assert get(first)["pane_ref"] == get(second)["pane_ref"]
    assert get(first)["repo_ref"] == get(second)["repo_ref"]
    second["panes"][0]["identity"]["hops"] = [{"host": "other"}]
    assert get(first)["pane_ref"] != get(second)["pane_ref"]
    assert get(first)["session_context_ref"] != get(second)["session_context_ref"]
