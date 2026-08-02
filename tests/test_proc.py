from __future__ import annotations

from roxabi_sense.util import agent_link as al
from roxabi_sense.util import proc as proc_mod


def test_resolve_unnamed_uses_comm(monkeypatch) -> None:
    monkeypatch.setattr(proc_mod, "read_comm", lambda pid: "ghostty" if pid == 7280 else None)
    assert proc_mod.resolve_app_name("Unnamed", 7280) == "ghostty"
    assert proc_mod.resolve_app_name("unknown", 7280) == "ghostty"
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
    monkeypatch.setattr(al, "descendants", lambda root, limit=200, tree=None: [99])
    monkeypatch.setattr(al, "read_comm", lambda pid: "grok" if pid == 99 else "ghostty")
    monkeypatch.setattr(al, "children_map", lambda: {})
    link = al.find_agent_link(10, sessions=sessions, tree={})
    assert link is not None
    assert link["session_id"] == "abc"
    assert link["match"] == "session_pid"


def test_process_tree_prefers_session_over_bare_comm(monkeypatch) -> None:
    sessions = [{"agent": "grok", "session_id": "deep", "pid": 50, "cwd": "/x"}]

    def fake_desc(root, limit=200, tree=None):
        return [20, 50]  # 20 is early grok without session, 50 is session

    def fake_comm(pid):
        return "grok" if pid in (20, 50) else "bash"

    monkeypatch.setattr(al, "descendants", fake_desc)
    monkeypatch.setattr(al, "read_comm", fake_comm)
    monkeypatch.setattr(al, "read_cwd", lambda pid: "/other" if pid == 20 else "/x")
    monkeypatch.setattr(al, "children_map", lambda: {})
    link = al.find_agent_link(1, sessions=sessions, tree={})
    assert link is not None
    assert link["session_id"] == "deep"
    assert link["match"] == "session_pid"


def test_chrome_claude_title_does_not_link(monkeypatch) -> None:
    sessions = [
        {
            "agent": "grok",
            "session_id": "s1",
            "pid": 100,
            "cwd": "/home/m/projects/roxabi-sense",
        }
    ]
    monkeypatch.setattr(al, "descendants", lambda *a, **k: [])
    monkeypatch.setattr(al, "children_map", lambda: {})
    monkeypatch.setattr(
        al,
        "list_tmux_agent_panes",
        lambda: [
            {
                "pane_pid": 100,
                "command": "grok",
                "path": "/home/m/projects/roxabi-sense",
                "attached": True,
            }
        ],
    )
    link = al.find_agent_link(
        48647,
        app="Google Chrome",
        title="Claude API · roxabi-sense docs",
        sessions=sessions,
        tree={},
    )
    assert link is None


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
    monkeypatch.setattr(al, "list_tmux_agent_panes", lambda: panes)
    monkeypatch.setattr(al, "descendants", lambda *a, **k: [])
    monkeypatch.setattr(al, "children_map", lambda: {})
    link = al.find_agent_link(
        7280,
        app="ghostty",
        title="Roxabi Sense /dev-init - grok",
        sessions=sessions,
        tree={},
    )
    assert link is not None
    assert link["session_id"] == "s1"
    assert "title" in link["match"]


def test_tmux_multi_session_no_title_match_is_none(monkeypatch) -> None:
    sessions = [
        {"agent": "grok", "session_id": "s1", "pid": 100, "cwd": "/a/proj-one"},
        {"agent": "grok", "session_id": "s2", "pid": 200, "cwd": "/a/proj-two"},
    ]
    panes = [
        {
            "pane_pid": 100,
            "command": "grok",
            "path": "/a/proj-one",
            "attached": True,
            "pane_title": "Other work A - grok",
        },
        {
            "pane_pid": 200,
            "command": "grok",
            "path": "/a/proj-two",
            "attached": True,
            "pane_title": "Other work B - grok",
        },
    ]
    monkeypatch.setattr(al, "list_tmux_agent_panes", lambda: panes)
    monkeypatch.setattr(al, "descendants", lambda *a, **k: [])
    monkeypatch.setattr(al, "children_map", lambda: {})
    link = al.find_agent_link(
        7280,
        app="ghostty",
        title="Hermes Slack kit - grok",
        sessions=sessions,
        tree={},
    )
    assert link is None


def test_find_agent_link_via_pane_title_multi(monkeypatch) -> None:
    """12-pane style: title has no repo basename; pane_title disambiguates."""
    sessions = [
        {
            "agent": "grok",
            "session_id": "sense",
            "pid": 100,
            "cwd": "/home/m/projects/roxabi-sense",
        },
        {
            "agent": "grok",
            "session_id": "boiler",
            "pid": 200,
            "cwd": "/home/m/projects/gosilex/silex-boilerplate",
        },
        {
            "agent": "grok",
            "session_id": "spark",
            "pid": 300,
            "cwd": "/home/m/projects/gosilex/spark",
        },
    ]
    panes = [
        {
            "pane_pid": 100,
            "command": "grok",
            "path": "/home/m/projects/roxabi-sense",
            "attached": True,
            "pane_title": "P0 Focus Issues #38 to #42 - grok",
        },
        {
            "pane_pid": 200,
            "command": "grok",
            "path": "/home/m/projects/gosilex/silex-boilerplate",
            "attached": True,
            "pane_title": "Dev 61 Session Title - grok",
        },
        {
            "pane_pid": 300,
            "command": "grok",
            "path": "/home/m/projects/gosilex/spark",
            "attached": True,
            "pane_title": "Review Existing Dependabot Configuration… - grok",
        },
    ]
    monkeypatch.setattr(al, "descendants", lambda *a, **k: [])
    monkeypatch.setattr(al, "children_map", lambda: {})
    link = al.find_agent_link(
        7280,
        app="ghostty",
        title="Dev 61 Session Title - grok",
        sessions=sessions,
        tree={},
        panes=panes,
    )
    assert link is not None
    assert link["session_id"] == "boiler"
    assert link["cwd"] == "/home/m/projects/gosilex/silex-boilerplate"
    assert link["match"].startswith("tmux_pane_title")


def test_find_agent_link_pane_title_strips_thinking_prefix(monkeypatch) -> None:
    """AT-SPI normalize_title strips Thinking; pane_title may still have it."""
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
            "cwd": "/home/m/projects/other",
        },
    ]
    panes = [
        {
            "pane_pid": 100,
            "command": "grok",
            "path": "/home/m/projects/roxabi-sense",
            "attached": True,
            "pane_title": "Thinking - tu peux me faire un petit recap de ce qu… - grok",
        },
        {
            "pane_pid": 200,
            "command": "grok",
            "path": "/home/m/projects/other",
            "attached": True,
            "pane_title": "Something else entirely long enough - grok",
        },
    ]
    monkeypatch.setattr(al, "descendants", lambda *a, **k: [])
    monkeypatch.setattr(al, "children_map", lambda: {})
    # Focus collector passes normalize_title'd title (no Thinking)
    link = al.find_agent_link(
        7280,
        app="ghostty",
        title="tu peux me faire un petit recap de ce qu… - grok",
        sessions=sessions,
        tree={},
        panes=panes,
    )
    assert link is not None
    assert link["session_id"] == "s1"
    assert "tmux_pane_title" in link["match"]


def test_find_agent_link_pane_title_without_session_registry(monkeypatch) -> None:
    """Pane path still usable when session pid/cwd not in registry."""
    panes = [
        {
            "pane_pid": 999,
            "command": "grok",
            "path": "/home/m/projects/voiceCLI",
            "attached": True,
            "pane_title": "Wire voiceCLI STT endpoint tests - grok",
        },
        {
            "pane_pid": 998,
            "command": "grok",
            "path": "/home/m/projects/other",
            "attached": True,
            "pane_title": "Totally different long session name here - grok",
        },
    ]
    monkeypatch.setattr(al, "descendants", lambda *a, **k: [])
    monkeypatch.setattr(al, "children_map", lambda: {})
    link = al.find_agent_link(
        7280,
        app="ghostty",
        title="Wire voiceCLI STT endpoint tests - grok",
        sessions=[],  # empty registry
        tree={},
        panes=panes,
    )
    assert link is not None
    assert link["cwd"] == "/home/m/projects/voiceCLI"
    assert link["match"] == "tmux_pane_title"
    assert link["session_id"] is None


def test_score_pane_title_basics() -> None:
    assert al.score_pane_title(
        "Dev 61 Session Title - grok",
        "Dev 61 Session Title - grok",
    ) == 100
    assert (
        al.score_pane_title(
            "tu peux me faire un petit recap de ce qu… - grok",
            "Thinking - tu peux me faire un petit recap de ce qu… - grok",
        )
        >= 90
    )
    assert (
        al.score_pane_title("Hermes Slack kit - grok", "Other work entirely - grok") == 0
    )


def test_list_tmux_agent_panes_parse(monkeypatch) -> None:
    class R:
        returncode = 0
        stdout = (
            "100\tgrok\t/home/m/p\t1\tMy session title - grok\n"
            "200\tbash\t/tmp\t1\tshell\n"
            "badline\n"
            "300\tclaude\t/x\t0\tclaude task\n"
        )

    monkeypatch.setattr(al, "_TMUX", "/usr/bin/tmux")
    monkeypatch.setattr(al.subprocess, "run", lambda *a, **k: R())
    panes = al.list_tmux_agent_panes()
    assert len(panes) == 2
    assert panes[0]["command"] == "grok"
    assert panes[0]["attached"] is True
    assert panes[0]["pane_title"] == "My session title - grok"
    assert panes[1]["command"] == "claude"
    assert panes[1]["pane_title"] == "claude task"


def test_list_tmux_agent_panes_error(monkeypatch) -> None:
    monkeypatch.setattr(al, "_TMUX", "/usr/bin/tmux")

    def boom(*a, **k):
        raise al.subprocess.TimeoutExpired(cmd="tmux", timeout=1)

    monkeypatch.setattr(al.subprocess, "run", boom)
    assert al.list_tmux_agent_panes() == []


def test_find_agent_link_none_without_signals() -> None:
    assert al.find_agent_link(None) is None


def test_tmux_child_pid_join(monkeypatch) -> None:
    """Ghostty has no agent descendants; join via tmux pane shell → session pid."""
    sessions = [
        {
            "agent": "grok",
            "session_id": "sess-tmux",
            "pid": 500,
            "cwd": "/home/m/proj",
        }
    ]
    panes = [
        {
            "pane_pid": 400,  # shell in pane
            "command": "grok",
            "path": "/home/m/other",
            "attached": True,
        }
    ]
    tree = {400: [500]}
    monkeypatch.setattr(al, "list_tmux_agent_panes", lambda: panes)

    def fake_desc(root, limit=200, tree=None):
        return [500] if root == 400 else []

    monkeypatch.setattr(al, "descendants", fake_desc)
    monkeypatch.setattr(al, "children_map", lambda: tree)
    link = al.find_agent_link(
        7280,  # ghostty — no agent under it
        app="ghostty",
        title="proj - grok",
        sessions=sessions,
        tree={},
        panes=panes,
    )
    assert link is not None
    assert link["session_id"] == "sess-tmux"
    assert link["match"] == "tmux_child_pid"


def test_find_agent_link_reuses_passed_panes(monkeypatch) -> None:
    """Batch enrich: caller passes panes → list_tmux not re-invoked."""
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise AssertionError("list_tmux_agent_panes should not run when panes= given")

    monkeypatch.setattr(al, "list_tmux_agent_panes", boom)
    monkeypatch.setattr(al, "descendants", lambda *a, **k: [])
    monkeypatch.setattr(al, "children_map", lambda: {})
    sessions = [
        {"agent": "grok", "session_id": "s1", "pid": 100, "cwd": "/home/m/proj"},
    ]
    panes = [
        {
            "pane_pid": 100,
            "command": "grok",
            "path": "/home/m/proj",
            "attached": True,
        }
    ]
    for _ in range(3):
        link = al.find_agent_link(
            1,
            app="ghostty",
            title="proj - grok",
            sessions=sessions,
            tree={},
            panes=panes,
        )
        assert link is not None
        assert link["session_id"] == "s1"
    assert calls["n"] == 0
