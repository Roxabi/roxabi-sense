"""Mux listing helpers — never spawn live herdr/tmux."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from roxabi_sense.collectors.tmux_sessions import TmuxSessionsCollector
from roxabi_sense.store import Store
from roxabi_sense.util import mux


@pytest.fixture(autouse=True)
def _reset_mux_and_block_live(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    mux.reset_mux_cache()
    monkeypatch.setattr(mux, "tmux_bin", lambda: None)
    monkeypatch.setattr(mux, "herdr_bin", lambda: None)

    def _blocked(*_a: object, **_k: object) -> Any:
        raise AssertionError("live herdr/tmux subprocess forbidden")

    monkeypatch.setattr(mux.subprocess, "run", _blocked)
    yield
    mux.reset_mux_cache()


def _completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["fake"], returncode, stdout, "")


def test_parses_herdr_jsonrpc_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "id": "cli:agent:list",
        "result": {
            "agents": [
                {
                    "agent": "omp",
                    "agent_status": "working",
                    "cwd": "/tmp/proj",
                    "focused": True,
                    "pane_id": "w1:p1",
                    "agent_session": {
                        "kind": "path",
                        "value": "/home/u/.omp/agent/sessions/foo/2026-09-13T19-16-30.jsonl",
                    },
                    "terminal_title": "secret prompt",
                    "terminal_title_stripped": "secret prompt",
                }
            ]
        },
    }
    monkeypatch.setattr(mux, "herdr_bin", lambda: "/fake/herdr")
    monkeypatch.setattr(
        mux.subprocess,
        "run",
        lambda *_a, **_k: _completed(json.dumps(payload)),
    )
    agents = mux.list_herdr_agents()
    assert len(agents) == 1
    assert agents[0]["agent"] == "omp"
    assert agents[0]["terminal_title"] == "secret prompt"


def test_parses_herdr_bare_agents_object(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mux, "herdr_bin", lambda: "/fake/herdr")
    monkeypatch.setattr(
        mux.subprocess,
        "run",
        lambda *_a, **_k: _completed(json.dumps({"agents": [{"agent": "grok", "cwd": "/x"}]})),
    )
    agents = mux.list_herdr_agents()
    assert agents == [{"agent": "grok", "cwd": "/x"}]


def test_herdr_missing_binary_empty() -> None:
    assert mux.list_herdr_agents() == []
    assert mux.herdr_session_rows() == []


def test_herdr_session_id_from_jsonl_path_stem() -> None:
    agent = {
        "agent_session": {
            "kind": "path",
            "value": "/home/u/.omp/agent/sessions/ws/2026-09-13T19-16-30-937Z_abc.jsonl",
        }
    }
    assert mux.herdr_session_id(agent) == "2026-09-13T19-16-30-937Z_abc"


def test_herdr_session_rows_maps_path_kind() -> None:
    agents = [
        {
            "agent": "omp",
            "agent_status": "idle",
            "cwd": "/tmp/proj",
            "agent_session": {
                "kind": "path",
                "value": "/x/sessions/sess-1.jsonl",
            },
            "terminal_title_stripped": "must not persist",
        },
        {
            "agent": "omp",
            "agent_status": "done",
            "cwd": "",
            "agent_session": {"kind": "id", "value": "raw-id"},
        },
        {"agent": "omp"},
    ]
    rows = mux.herdr_session_rows(agents)
    assert rows == [
        {
            "agent": "omp",
            "session_id": "sess-1",
            "cwd": "/tmp/proj",
            "state": "idle",
            "source": "herdr",
        },
        {
            "agent": "omp",
            "session_id": "raw-id",
            "cwd": "",
            "state": "done",
            "source": "herdr",
        },
    ]
    assert all("title" not in k for row in rows for k in row)


def test_list_mux_agent_panes_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        mux,
        "list_tmux_panes",
        lambda: [
            {
                "mux": "tmux",
                "command": "omp",
                "path": "/p",
                "attached": True,
                "pane_title": "π > task",
                "pane_pid": 42,
                "pane_id": "%1",
            },
            {
                "mux": "tmux",
                "command": "zsh",
                "path": "/p",
                "attached": False,
                "pane_title": "",
                "pane_pid": 7,
                "pane_id": "%2",
            },
        ],
    )
    monkeypatch.setattr(
        mux,
        "list_herdr_agents",
        lambda: [
            {
                "agent": "omp",
                "cwd": "/work",
                "focused": True,
                "pane_id": "w1:p1",
                "terminal_title_stripped": "in memory only",
            }
        ],
    )
    rows = mux.list_mux_agent_panes()
    assert rows[0] == {
        "mux": "tmux",
        "pane_pid": 42,
        "pane_id": "%1",
        "command": "omp",
        "path": "/p",
        "attached": True,
        "pane_title": "π > task",
    }
    assert rows[1] == {
        "mux": "herdr",
        "pane_pid": None,
        "pane_id": "w1:p1",
        "command": "omp",
        "path": "/work",
        "attached": True,
        "pane_title": "in memory only",
    }



def test_list_tmux_agent_panes_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = (
        "s\t0\tw\tgrok\t/home/m/p\t1\t100\t%1\tMy session title - grok\n"
        "s\t0\tw\tbash\t/tmp\t1\t200\t%2\tshell\n"
        "badline\n"
        "s\t1\tw\tclaude\t/x\t0\t300\t%3\tclaude task\n"
    )
    monkeypatch.setattr(mux, "tmux_bin", lambda: "/usr/bin/tmux")
    monkeypatch.setattr(
        mux.subprocess,
        "run",
        lambda *_a, **_k: _completed(stdout),
    )
    mux.reset_mux_cache()
    panes = mux.list_tmux_agent_panes()
    assert len(panes) == 2
    assert panes[0]["command"] == "grok"
    assert panes[0]["attached"] is True
    assert panes[0]["pane_title"] == "My session title - grok"
    assert panes[0]["pane_pid"] == 100
    assert panes[1]["command"] == "claude"
    assert panes[1]["pane_title"] == "claude task"
    assert panes[1]["attached"] is False


def test_list_tmux_agent_panes_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mux, "tmux_bin", lambda: "/usr/bin/tmux")

    def boom(*_a: object, **_k: object) -> Any:
        raise subprocess.TimeoutExpired(cmd="tmux", timeout=1)

    monkeypatch.setattr(mux.subprocess, "run", boom)
    mux.reset_mux_cache()
    assert mux.list_tmux_agent_panes() == []

def test_tmux_collector_fingerprint_ignores_pane_title(tmp_path: Path) -> None:
    panes = [
        {
            "session": "s",
            "window": "0",
            "window_name": "code",
            "command": "grok",
            "path": "/tmp/proj",
            "attached": True,
            "pane_title": "Thinking…",
        }
    ]
    collector = TmuxSessionsCollector(list_panes=lambda: panes)
    store = Store(tmp_path / "s.db")
    assert collector.tick(store) == 1
    panes[0]["pane_title"] = "Running…"
    assert collector.tick(store) == 0
    panes[0]["command"] = "zsh"
    assert collector.tick(store) == 1
    store.close()


def test_tmux_missing_binary_no_write(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    collector = TmuxSessionsCollector()
    assert collector.tick(store) == 0
    assert store.count() == 0
    store.close()
