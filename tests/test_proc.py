from __future__ import annotations

from roxabi_sense.util import proc as proc_mod


def test_resolve_unnamed_uses_comm(monkeypatch) -> None:
    monkeypatch.setattr(proc_mod, "read_comm", lambda pid: "ghostty" if pid == 7280 else None)
    assert proc_mod.resolve_app_name("Unnamed", 7280) == "ghostty"
    assert proc_mod.resolve_app_name("Google Chrome", 1) == "Google Chrome"
    assert proc_mod.resolve_app_name("Unnamed", None) == "Unnamed"


def test_find_agent_link_by_session_pid(monkeypatch) -> None:
    sessions = [
        {
            "agent": "grok",
            "session_id": "abc",
            "pid": 99,
            "cwd": "/home/m/proj",
        }
    ]
    monkeypatch.setattr(proc_mod, "descendants", lambda root, limit=200: [99])
    monkeypatch.setattr(proc_mod, "read_comm", lambda pid: "grok" if pid == 99 else "ghostty")
    link = proc_mod.find_agent_link(10, sessions=sessions)
    assert link is not None
    assert link["session_id"] == "abc"
    assert link["match"] == "session_pid"
    assert link["pid"] == 99


def test_find_agent_link_tmux_title(monkeypatch) -> None:
    sessions = [
        {
            "agent": "grok",
            "session_id": "s1",
            "pid": 100,
            "cwd": "/home/m/projects/roxabi-sense",
        },
        {
            "agent": "grok",
            "session_id": "s2",
            "pid": 200,
            "cwd": "/home/m/projects/gosilex",
        },
    ]
    panes = [
        {
            "pane_pid": 100,
            "command": "grok",
            "path": "/home/m/projects/roxabi-sense",
            "attached": True,
        },
        {
            "pane_pid": 200,
            "command": "grok",
            "path": "/home/m/projects/gosilex",
            "attached": True,
        },
    ]
    monkeypatch.setattr(proc_mod, "list_tmux_agent_panes", lambda: panes)
    monkeypatch.setattr(proc_mod, "descendants", lambda root, limit=200: [])
    link = proc_mod.find_agent_link(
        7280,
        app="ghostty",
        title="Roxabi Sense /dev-init - grok",
        sessions=sessions,
    )
    assert link is not None
    assert link["session_id"] == "s1"
    assert "title" in link["match"]


def test_find_agent_link_none_without_signals() -> None:
    assert proc_mod.find_agent_link(None) is None
