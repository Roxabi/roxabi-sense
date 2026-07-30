from __future__ import annotations

import json
from pathlib import Path

from roxabi_sense.collectors.agent_sessions import AgentSessionsCollector
from roxabi_sense.store import Store


def test_grok_sessions_snapshot_only(tmp_path: Path) -> None:
    grok = tmp_path / "active_sessions.json"
    grok.write_text(
        json.dumps(
            [
                {
                    "session_id": "abc",
                    "pid": 1,
                    "cwd": "/tmp/proj",
                    "opened_at": "2026-07-30T00:00:00Z",
                }
            ]
        ),
        encoding="utf-8",
    )
    claude = tmp_path / "history.jsonl"
    claude.write_text("{}\n", encoding="utf-8")
    store = Store(tmp_path / "s.db")
    c = AgentSessionsCollector(grok_path=grok, claude_history=claude)
    n = c.tick(store)
    assert n == 1
    assert c.tick(store) == 0
    snap = store.last_by_kind("agent_sessions_snapshot")
    assert snap is not None
    assert snap.payload["count"] == 2
    # no per-session agent_session rows
    assert store.last_by_kind("agent_session") is None
    # claude mtime change alone should not re-emit (stable fingerprint)
    claude.write_text("{}\n{}\n", encoding="utf-8")
    assert c.tick(store) == 0
    store.close()


def test_missing_and_corrupt_grok(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    missing = tmp_path / "nope.json"
    c = AgentSessionsCollector(grok_path=missing, claude_history=tmp_path / "h.jsonl")
    assert c.tick(store) == 1  # empty snapshot
    snap = store.last_by_kind("agent_sessions_snapshot")
    assert snap is not None
    assert snap.payload["count"] == 0
    bad = tmp_path / "bad.json"
    bad.write_text("not-json", encoding="utf-8")
    c2 = AgentSessionsCollector(grok_path=bad, claude_history=tmp_path / "h2.jsonl")
    assert c2.tick(store) == 1
    store.close()
