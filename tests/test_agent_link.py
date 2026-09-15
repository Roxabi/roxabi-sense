from __future__ import annotations

from typing import Any

from roxabi_sense.util.agent_link import find_agent_link


def _pane(**kwargs: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "mux": "tmux",
        "pane_pid": None,
        "pane_id": "0",
        "command": "grok",
        "path": "/tmp/p",
        "attached": False,
        "pane_title": "",
    }
    row.update(kwargs)
    return row


def test_grok_tmux_pane_title_exact_links() -> None:
    panes = [
        _pane(
            mux="tmux",
            pane_pid=100,
            pane_id="1",
            command="grok",
            path="/tmp/auth",
            attached=True,
            pane_title="Fix auth - grok",
        )
    ]
    sessions = [
        {"agent": "grok", "session_id": "sid-g", "cwd": "/tmp/auth", "pid": 101}
    ]
    link = find_agent_link(
        None,
        app="ghostty",
        title="Fix auth - grok",
        sessions=sessions,
        tree={},
        panes=panes,
    )
    assert link is not None
    assert link["agent"] == "grok"
    assert link["session_id"] == "sid-g"
    assert link["cwd"] == "/tmp/auth"
    assert "pane_title" in str(link["match"])


def test_herdr_omp_pane_title_links_session() -> None:
    panes = [
        _pane(
            mux="herdr",
            pane_id="other",
            command="omp",
            path="/work/other",
            attached=True,
            pane_title="Other task",
        ),
        _pane(
            mux="herdr",
            pane_id="pr",
            command="omp",
            path="/work/foo",
            attached=False,
            pane_title="π > PR foo",
        ),
    ]
    sessions = [
        {"agent": "omp", "session_id": "sess-other", "cwd": "/work/other"},
        {"agent": "omp", "session_id": "sess-foo", "cwd": "/work/foo"},
    ]
    link = find_agent_link(
        None,
        app="ghostty",
        title="π > PR foo",
        sessions=sessions,
        tree={},
        panes=panes,
    )
    assert link is not None
    assert link["agent"] == "omp"
    assert link["session_id"] == "sess-foo"
    assert link["cwd"] == "/work/foo"


def test_generic_ghostty_title_uses_unique_focused_pane() -> None:
    panes = [
        _pane(
            mux="herdr",
            pane_id=str(i),
            command="omp",
            path=f"/w/{i}",
            attached=(i == 2),
            pane_title=f"Task {i}",
        )
        for i in range(6)
    ]
    sessions = [
        {"agent": "omp", "session_id": f"s{i}", "cwd": f"/w/{i}"} for i in range(6)
    ]
    link = find_agent_link(
        None,
        app="ghostty",
        title="Ghostty",
        sessions=sessions,
        tree={},
        panes=panes,
    )
    assert link is not None
    assert link["cwd"] == "/w/2"
    assert link["session_id"] == "s2"
    assert link["agent"] == "omp"
    assert "herdr_focused" in str(link["match"]) or "mux_focused" in str(link["match"])


def test_tmux_grok_title_preferred_over_herdr_focus() -> None:
    panes = [
        _pane(
            mux="herdr",
            pane_id="h",
            command="omp",
            path="/herdr/proj",
            attached=True,
            pane_title="OMP work",
        ),
        _pane(
            mux="tmux",
            pane_pid=50,
            pane_id="t",
            command="grok",
            path="/tmux/proj",
            attached=False,
            pane_title="Fix auth - grok",
        ),
    ]
    sessions = [
        {"agent": "omp", "session_id": "omp-1", "cwd": "/herdr/proj"},
        {"agent": "grok", "session_id": "grok-1", "cwd": "/tmux/proj", "pid": 51},
    ]
    link = find_agent_link(
        None,
        app="ghostty",
        title="Fix auth - grok",
        sessions=sessions,
        tree={},
        panes=panes,
    )
    assert link is not None
    assert link["agent"] == "grok"
    assert link["session_id"] == "grok-1"
    assert link["cwd"] == "/tmux/proj"
