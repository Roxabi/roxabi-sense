from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from roxabi_sense.collectors.agent_sessions import AgentSessionsCollector, _snapshot_stale
from roxabi_sense.store import Store
from roxabi_sense.util import mux
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
        herdr_sessions=lambda: [],
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
        herdr_sessions=lambda: [],
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
        herdr_sessions=lambda: [],
    )
    assert c2.tick(store) == 0
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


def _empty_file_registry(tmp_path: Path) -> tuple[Path, Path]:
    grok = tmp_path / "active_sessions.json"
    grok.write_text("[]", encoding="utf-8")
    claude_dir = tmp_path / "claude_sessions"
    claude_dir.mkdir()
    return grok, claude_dir


def test_herdr_omp_row_in_snapshot(tmp_path: Path) -> None:
    grok, claude_dir = _empty_file_registry(tmp_path)
    store = Store(tmp_path / "s.db")
    c = AgentSessionsCollector(
        grok_path=grok,
        claude_history=tmp_path / "h.jsonl",
        claude_sessions_dir=claude_dir,
        herdr_sessions=lambda: [
            {
                "agent": "omp",
                "session_id": "omp-live",
                "cwd": "/tmp/omp-proj",
                "state": "idle",
                "source": "herdr",
            }
        ],
    )
    assert c.tick(store) == 1
    snap = store.last_by_kind("agent_sessions_snapshot")
    assert snap is not None
    assert snap.payload["count"] == 1
    row = snap.payload["sessions"][0]
    assert row["agent"] == "omp"
    assert row["session_id"] == "omp-live"
    assert row["cwd"] == "/tmp/omp-proj"
    assert row["state"] == "idle"
    assert row["source"] == "herdr"
    store.close()


def test_herdr_session_id_is_jsonl_stem(tmp_path: Path) -> None:
    grok, claude_dir = _empty_file_registry(tmp_path)
    raw_path = "/home/me/.omp/projects/demo/deadbeef.jsonl"
    store = Store(tmp_path / "s.db")
    c = AgentSessionsCollector(
        grok_path=grok,
        claude_history=tmp_path / "h.jsonl",
        claude_sessions_dir=claude_dir,
        herdr_sessions=lambda: mux.herdr_session_rows(
            [
                {
                    "agent": "omp",
                    "cwd": "/tmp/demo",
                    "agent_status": "idle",
                    "agent_session": {"kind": "path", "value": raw_path},
                }
            ]
        ),
    )
    c.tick(store)
    snap = store.last_by_kind("agent_sessions_snapshot")
    assert snap is not None
    row = snap.payload["sessions"][0]
    assert row["session_id"] == "deadbeef"
    assert row["session_id"] != raw_path
    assert ".jsonl" not in row["session_id"]
    store.close()


def test_herdr_state_change_reemits(tmp_path: Path) -> None:
    grok, claude_dir = _empty_file_registry(tmp_path)
    live = {
        "agent": "omp",
        "session_id": "omp-1",
        "cwd": "/tmp/p",
        "state": "idle",
        "source": "herdr",
    }
    store = Store(tmp_path / "s.db")
    c = AgentSessionsCollector(
        grok_path=grok,
        claude_history=tmp_path / "h.jsonl",
        claude_sessions_dir=claude_dir,
        herdr_sessions=lambda: [dict(live)],
    )
    assert c.tick(store) == 1
    assert c.tick(store) == 0
    live["state"] = "working"
    assert c.tick(store) == 1
    snap = store.last_by_kind("agent_sessions_snapshot")
    assert snap is not None
    assert snap.payload["sessions"][0]["state"] == "working"
    store.close()


def test_grok_and_omp_together_duplicate_session_id_not_doubled(tmp_path: Path) -> None:
    grok = tmp_path / "active_sessions.json"
    grok.write_text(
        json.dumps(
            [{"session_id": "abc", "pid": 1, "cwd": "/tmp/proj"}]
        ),
        encoding="utf-8",
    )
    claude_dir = tmp_path / "claude_sessions"
    claude_dir.mkdir()
    store = Store(tmp_path / "s.db")
    c = AgentSessionsCollector(
        grok_path=grok,
        claude_history=tmp_path / "h.jsonl",
        claude_sessions_dir=claude_dir,
        herdr_sessions=lambda: [
            {
                "agent": "omp",
                "session_id": "abc",
                "cwd": "/tmp/other",
                "state": "working",
                "source": "herdr",
            },
            {
                "agent": "omp",
                "session_id": "omp-unique",
                "cwd": "/tmp/omp",
                "state": "idle",
                "source": "herdr",
            },
        ],
    )
    assert c.tick(store) == 1
    snap = store.last_by_kind("agent_sessions_snapshot")
    assert snap is not None
    sessions = snap.payload["sessions"]
    assert snap.payload["count"] == 2
    by_id = {s["session_id"]: s for s in sessions}
    assert set(by_id) == {"abc", "omp-unique"}
    grok_row = by_id["abc"]
    assert grok_row["agent"] == "grok"
    assert grok_row["pid"] == 1
    assert grok_row["cwd"] == "/tmp/proj"
    assert grok_row["state"] == "working"
    assert by_id["omp-unique"]["agent"] == "omp"
    store.close()


def test_grok_idle_kept_when_herdr_working(tmp_path: Path) -> None:
    grok = tmp_path / "active_sessions.json"
    grok.write_text(
        json.dumps(
            [{"session_id": "abc", "pid": 1, "cwd": "/tmp/proj", "state": "idle"}]
        ),
        encoding="utf-8",
    )
    claude_dir = tmp_path / "claude_sessions"
    claude_dir.mkdir()
    store = Store(tmp_path / "s.db")
    c = AgentSessionsCollector(
        grok_path=grok,
        claude_history=tmp_path / "h.jsonl",
        claude_sessions_dir=claude_dir,
        herdr_sessions=lambda: [
            {
                "agent": "omp",
                "session_id": "abc",
                "cwd": "/tmp/other",
                "state": "working",
                "source": "herdr",
            }
        ],
    )
    assert c.tick(store) == 1
    snap = store.last_by_kind("agent_sessions_snapshot")
    assert snap is not None
    grok_row = snap.payload["sessions"][0]
    assert grok_row["agent"] == "grok"
    assert grok_row["state"] == "idle"
    store.close()


def test_tick_refreshes_last_ok_without_new_event(tmp_path: Path) -> None:
    grok, claude_dir = _empty_file_registry(tmp_path)
    store = Store(tmp_path / "s.db")
    c = AgentSessionsCollector(
        grok_path=grok,
        claude_history=tmp_path / "history.jsonl",
        claude_sessions_dir=claude_dir,
        herdr_sessions=lambda: [],
    )
    assert c.tick(store) == 1
    assert c.tick(store) == 0
    first_id = store.last_by_kind("agent_sessions_snapshot")
    assert first_id is not None
    store.set_meta("agent_sessions_last_ok", "2026-01-01T00:00:00Z")
    assert c.tick(store) == 0
    assert store.get_meta("agent_sessions_last_ok") != "2026-01-01T00:00:00Z"
    snap = store.last_by_kind("agent_sessions_snapshot")
    assert snap is not None
    assert snap.id == first_id.id
    store.close()


def test_snapshot_stale_after_900s() -> None:
    now = datetime(2026, 1, 1, 12, 15, 1, tzinfo=UTC)
    assert _snapshot_stale("2026-01-01T12:00:00Z", now=now) is True
    assert _snapshot_stale("2026-01-01T12:00:02Z", now=now) is False
