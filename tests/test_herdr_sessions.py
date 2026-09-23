"""HerdrSessionsCollector — structural snapshot, no titles, no live CLI."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from roxabi_sense.collectors.mux import HerdrSessionsCollector
from roxabi_sense.collectors.mux.herdr import KEYFRAME_S
from roxabi_sense.config import SenseConfig, load_config
from roxabi_sense.daemon_collectors import build_poll_collectors
from roxabi_sense.store import Store
from roxabi_sense.util import mux

_TITLE_KEYS = frozenset({"terminal_title", "terminal_title_stripped", "pane_title"})


@pytest.fixture(autouse=True)
def _block_live(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    mux.reset_mux_cache()
    monkeypatch.setattr(mux, "herdr_bin", lambda: None)
    monkeypatch.setattr(mux, "tmux_bin", lambda: None)

    def _blocked(*_a: object, **_k: object) -> Any:
        raise AssertionError("live herdr/tmux subprocess forbidden")

    monkeypatch.setattr(mux.subprocess, "run", _blocked)
    yield
    mux.reset_mux_cache()


def _agent(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "agent": "omp",
        "agent_status": "working",
        "cwd": "/tmp/proj",
        "focused": True,
        "pane_id": "w1:p1",
        "agent_session": {
            "kind": "path",
            "value": "/home/u/.omp/agent/sessions/ws/2026-09-13T19-16-30-937Z_abc.jsonl",
        },
        "terminal_title": "π ⠏ secret prompt",
        "terminal_title_stripped": "π ⠏ secret prompt",
    }
    row.update(overrides)
    return row


def test_missing_binary_zero_writes(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    assert HerdrSessionsCollector().tick(store) == 0
    assert store.count() == 0
    store.close()


def test_empty_agents_writes_zero_count_once(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    collector = HerdrSessionsCollector(list_agents=lambda: [])
    assert collector.tick(store) == 1
    snap = store.last_by_kind("herdr_snapshot")
    assert snap is not None
    assert snap.payload == {"count": 0, "panes": []}
    assert collector.tick(store) == 0
    store.close()


def test_snapshot_keeps_session_title_never_raw_terminal_title(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    collector = HerdrSessionsCollector(list_agents=lambda: [_agent()])
    assert collector.tick(store) == 1
    snap = store.last_by_kind("herdr_snapshot")
    assert snap is not None
    assert snap.payload["count"] == 1
    pane = snap.payload["panes"][0]
    assert _TITLE_KEYS.isdisjoint(pane)
    assert pane["title"] == "secret prompt"  # spinner / π chrome stripped
    assert pane["session_id"] == "2026-09-13T19-16-30-937Z_abc"
    assert pane["status"] == "working"
    store.close()


def test_spinner_frame_does_not_reemit_but_status_and_rename_do(tmp_path: Path) -> None:
    agents = [_agent()]
    store = Store(tmp_path / "s.db")
    collector = HerdrSessionsCollector(list_agents=lambda: agents)
    assert collector.tick(store) == 1
    agents[0]["terminal_title_stripped"] = "π ⠋ secret prompt"
    assert collector.tick(store) == 0
    agents[0]["agent_status"] = "idle"
    assert collector.tick(store) == 1
    agents[0]["terminal_title_stripped"] = "π > renamed task"
    assert collector.tick(store) == 1
    snap = store.last_by_kind("herdr_snapshot")
    assert snap is not None
    assert snap.payload["panes"][0]["status"] == "idle"
    assert snap.payload["panes"][0]["title"] == "renamed task"
    store.close()


def test_keyframe_reemits_unchanged_state_after_interval(tmp_path: Path) -> None:
    """Unchanged state is re-stamped so report math can bound stale snapshots."""
    now = [1000.0]
    store = Store(tmp_path / "s.db")
    collector = HerdrSessionsCollector(list_agents=lambda: [_agent()], clock=lambda: now[0])
    assert collector.tick(store) == 1
    now[0] += KEYFRAME_S - 1
    assert collector.tick(store) == 0
    now[0] += 1
    assert collector.tick(store) == 1
    assert store.count() == 2
    store.close()


def test_focused_pane_recorded_and_focus_move_reemits(tmp_path: Path) -> None:
    focused = {"pane_id": "w2:p1", "cwd": "/tmp/shell"}
    store = Store(tmp_path / "s.db")
    collector = HerdrSessionsCollector(
        list_agents=lambda: [_agent(focused=False)], focused_pane=lambda: dict(focused)
    )
    assert collector.tick(store) == 1
    focused["cwd"] = "/tmp/other"
    assert collector.tick(store) == 1
    snap = store.last_by_kind("herdr_snapshot")
    assert snap is not None
    assert snap.payload["focused"] == {"pane_id": "w2:p1", "cwd": "/tmp/other"}
    store.close()


def test_build_poll_collectors_herdr_flag() -> None:
    off = SenseConfig(
        agent_sessions=False,
        process_presence=False,
        idle=False,
        mpris=False,
        tmux=False,
        herdr=False,
        focus=False,
    )
    assert [c.name for c in build_poll_collectors(off, logind_idle=False)] == []
    on = SenseConfig(
        agent_sessions=False,
        process_presence=False,
        idle=False,
        mpris=False,
        tmux=False,
        herdr=True,
        focus=False,
    )
    names = [c.name for c in build_poll_collectors(on, logind_idle=False)]
    assert names == ["herdr"]


def test_toml_herdr_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[collectors]\nherdr = false\n", encoding="utf-8")
    monkeypatch.delenv("SENSE_DB", raising=False)
    assert SenseConfig().herdr is True
    assert load_config(cfg_path).herdr is False
