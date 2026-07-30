from __future__ import annotations

import json
from pathlib import Path

from roxabi_sense.collectors.agent_sessions import AgentSessionsCollector
from roxabi_sense.store import Store


def test_grok_sessions_collect(tmp_path: Path) -> None:
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
    assert n >= 2
    # second tick no change
    assert c.tick(store) == 0
    snap = store.last_by_kind("agent_sessions_snapshot")
    assert snap is not None
    assert snap.payload["count"] == 2  # grok + claude hint
    store.close()
