"""Opt-in Cursor RO collector (#49)."""

from __future__ import annotations

import json
from pathlib import Path

from roxabi_sense.collectors.cursor_sessions import CursorSessionsCollector
from roxabi_sense.config import SenseConfig, load_config
from roxabi_sense.daemon_collectors import build_poll_collectors
from roxabi_sense.store import Store
from roxabi_sense.util.cursor_paths import load_cursor_workspaces


def _fake_cursor_root(tmp: Path, *, folder: str = "file:///tmp/my-proj") -> Path:
    root = tmp / "Cursor"
    ws = root / "User" / "workspaceStorage" / "abc123hash"
    ws.mkdir(parents=True)
    (ws / "workspace.json").write_text(
        json.dumps({"folder": folder}),
        encoding="utf-8",
    )
    # noise that must not be opened as content
    (ws / "state.vscdb").write_bytes(b"not-a-real-db")
    return root


def test_load_cursor_workspaces_parses_folder(tmp_path: Path) -> None:
    root = _fake_cursor_root(tmp_path)
    rows = load_cursor_workspaces(root, max_age_days=3650.0)
    assert len(rows) == 1
    assert rows[0]["agent"] == "cursor"
    assert rows[0]["session_id"] == "abc123hash"
    assert rows[0]["cwd"] == "/tmp/my-proj"
    assert rows[0]["state"] == "workspace_present"
    assert "workspace.json" in rows[0]["source"]


def test_collector_disabled_no_write(tmp_path: Path) -> None:
    root = _fake_cursor_root(tmp_path)
    store = Store(tmp_path / "s.db")
    c = CursorSessionsCollector(root=root, enabled=False)
    assert c.tick(store) == 0
    assert store.last_by_kind("agent_sessions_snapshot") is None
    store.close()


def test_collector_emits_and_dedups(tmp_path: Path) -> None:
    root = _fake_cursor_root(tmp_path)
    store = Store(tmp_path / "s.db")
    c = CursorSessionsCollector(root=root, max_age_days=3650.0)
    assert c.tick(store) == 1
    assert c.tick(store) == 0
    snap = store.last_by_kind("agent_sessions_snapshot")
    assert snap is not None
    assert snap.payload["source"] == "cursor"
    assert snap.payload["count"] == 1
    assert snap.payload["sessions"][0]["agent"] == "cursor"
    store.close()


def test_config_off_by_default_no_cursor_in_poll() -> None:
    cfg = SenseConfig(
        agent_sessions=False,
        process_presence=False,
        idle=False,
        mpris=False,
        tmux=False,
    )
    assert cfg.cursor_sessions is False
    cols = build_poll_collectors(cfg, logind_idle=False)
    assert all(c.name != "cursor_sessions" for c in cols)


def test_config_enable_registers_collector(tmp_path: Path) -> None:
    cfg = SenseConfig(
        agent_sessions=False,
        process_presence=False,
        idle=False,
        mpris=False,
        tmux=False,
        cursor_sessions=True,
        cursor_root=tmp_path / "Cursor",
    )
    cols = build_poll_collectors(cfg, logind_idle=False)
    assert any(c.name == "cursor_sessions" for c in cols)


def test_toml_cursor_sessions(tmp_path: Path, monkeypatch) -> None:
    cfg_path = tmp_path / "config.toml"
    root = tmp_path / "croot"
    cfg_path.write_text(
        f"""
[collectors]
cursor_sessions = true
cursor_root = "{root}"
cursor_max_workspaces = 5
cursor_max_age_days = 7
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("SENSE_DB", raising=False)
    cfg = load_config(cfg_path)
    assert cfg.cursor_sessions is True
    assert cfg.cursor_root == root
    assert cfg.cursor_max_workspaces == 5
    assert cfg.cursor_max_age_days == 7.0
