from __future__ import annotations

import json
from pathlib import Path

from roxabi_sense.collectors.agent_sessions import AgentSessionsCollector
from roxabi_sense.store import Store
from roxabi_sense.util.session_registry import SessionRegistry


def test_grok_and_claude_sessions_snapshot(tmp_path: Path) -> None:
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
    claude_dir = tmp_path / "claude_sessions"
    claude_dir.mkdir()
    (claude_dir / "42.json").write_text(
        json.dumps(
            {
                "sessionId": "claude-sid",
                "pid": 42,
                "cwd": "/tmp/claude-proj",
                "startedAt": 1,
            }
        ),
        encoding="utf-8",
    )
    store = Store(tmp_path / "s.db")
    c = AgentSessionsCollector(
        grok_path=grok,
        claude_history=tmp_path / "history.jsonl",
        claude_sessions_dir=claude_dir,
    )
    n = c.tick(store)
    assert n == 1
    assert c.tick(store) == 0
    snap = store.last_by_kind("agent_sessions_snapshot")
    assert snap is not None
    assert snap.payload["count"] == 2
    agents = {s["agent"] for s in snap.payload["sessions"]}
    assert agents == {"grok", "claude"}
    # no per-session agent_session rows
    assert store.last_by_kind("agent_session") is None
    # history.jsonl is unused — mtime change alone must not re-emit
    (tmp_path / "history.jsonl").write_text("{}\n{}\n", encoding="utf-8")
    assert c.tick(store) == 0
    store.close()


def test_missing_and_corrupt_grok(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    missing = tmp_path / "nope.json"
    empty_claude = tmp_path / "claude_empty"
    empty_claude.mkdir()
    c = AgentSessionsCollector(
        grok_path=missing,
        claude_history=tmp_path / "h.jsonl",
        claude_sessions_dir=empty_claude,
    )
    assert c.tick(store) == 1  # empty snapshot
    snap = store.last_by_kind("agent_sessions_snapshot")
    assert snap is not None
    assert snap.payload["count"] == 0
    bad = tmp_path / "bad.json"
    bad.write_text("not-json", encoding="utf-8")
    c2 = AgentSessionsCollector(
        grok_path=bad,
        claude_history=tmp_path / "h2.jsonl",
        claude_sessions_dir=empty_claude,
    )
    assert c2.tick(store) == 1
    store.close()


def test_registry_skips_read_when_signature_unchanged(tmp_path: Path, monkeypatch) -> None:
    """stat only when unchanged; full JSON re-read only on mtime/size change."""
    grok = tmp_path / "active_sessions.json"
    grok.write_text(
        json.dumps([{"session_id": "s1", "pid": 1, "cwd": "/a"}]),
        encoding="utf-8",
    )
    claude_dir = tmp_path / "sessions"
    claude_dir.mkdir()
    (claude_dir / "9.json").write_text(
        json.dumps({"sessionId": "c1", "pid": 9, "cwd": "/c"}),
        encoding="utf-8",
    )
    reg = SessionRegistry(grok_path=grok, claude_dir=claude_dir)
    reads: list[str] = []
    real_read = Path.read_text

    def counting_read(self: Path, *a, **k):
        reads.append(str(self))
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", counting_read)
    first = reg.load_all()
    assert len(first) == 2
    assert len(reads) == 2  # grok file + one claude json
    reads.clear()
    second = reg.load_all()
    assert second is first or second == first
    assert reads == []  # signature hit — no re-read
    # mutate grok content (size change) → re-read grok only
    grok.write_text(
        json.dumps(
            [
                {"session_id": "s1", "pid": 1, "cwd": "/a"},
                {"session_id": "s2", "pid": 2, "cwd": "/b"},
            ]
        ),
        encoding="utf-8",
    )
    third = reg.load_all()
    assert len(third) == 3
    assert any("active_sessions.json" in r for r in reads)
    assert not any(r.endswith("9.json") for r in reads)


def test_registry_reloads_claude_when_file_added(tmp_path: Path) -> None:
    grok = tmp_path / "active_sessions.json"
    grok.write_text("[]", encoding="utf-8")
    claude_dir = tmp_path / "sessions"
    claude_dir.mkdir()
    reg = SessionRegistry(grok_path=grok, claude_dir=claude_dir)
    assert reg.load_claude() == []
    (claude_dir / "1.json").write_text(
        json.dumps({"sessionId": "x", "pid": 1, "cwd": "/p"}),
        encoding="utf-8",
    )
    rows = reg.load_claude()
    assert len(rows) == 1
    assert rows[0]["session_id"] == "x"
    assert rows[0]["agent"] == "claude"
