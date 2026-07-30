from __future__ import annotations

from pathlib import Path

from roxabi_sense.collectors import focus_atspi
from roxabi_sense.collectors.focus_atspi import FocusAtspiCollector, WindowInfo
from roxabi_sense.store import Store


def test_focus_dedup_and_normalize(tmp_path: Path, monkeypatch) -> None:
    # same logical focus, spinner title changes → only 1 focus event
    calls = {"n": 0}

    def probe() -> list[WindowInfo]:
        calls["n"] += 1
        spinner = "⠋" if calls["n"] == 1 else "⠙"
        return [
            WindowInfo(
                app="Unnamed",
                title=f"{spinner} - Thinking - My Task - grok",
                active=True,
                role="frame",
                pid=7280,
            ),
            WindowInfo(
                app="slack",
                title="channel - SILEX",
                active=False,
                role="frame",
                pid=100,
            ),
        ]

    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_atspi.resolve_app_name",
        lambda app, pid: "ghostty" if app == "Unnamed" else app,
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_atspi.find_agent_link",
        lambda pid, app=None, title=None, sessions=None: {
            "agent": "grok",
            "session_id": "sid-1",
            "cwd": "/tmp/p",
            "pid": 99,
            "match": "session_pid",
        }
        if pid == 7280
        else None,
    )

    store = Store(tmp_path / "s.db")
    c = FocusAtspiCollector(probe=probe, sessions_loader=lambda: [])
    assert c.tick(store) == 2  # desktop + focus
    assert c.tick(store) == 0  # spinner change only → no write
    focus = store.last_by_kind("focus")
    assert focus is not None
    assert focus.payload["app"] == "ghostty"
    assert focus.payload["title"] == "My Task - grok"
    assert focus.payload["agent"]["session_id"] == "sid-1"
    assert store.count() == 2
    store.close()


def test_focus_writes_on_app_change(tmp_path: Path, monkeypatch) -> None:
    state = {"app": "ghostty", "title": "A - grok"}

    def probe() -> list[WindowInfo]:
        return [
            WindowInfo(
                app=state["app"],
                title=state["title"],
                active=True,
                role="frame",
                pid=1,
            )
        ]

    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_atspi.resolve_app_name",
        lambda app, pid: app,
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_atspi.find_agent_link",
        lambda pid, app=None, title=None, sessions=None: None,
    )
    store = Store(tmp_path / "s.db")
    c = FocusAtspiCollector(probe=probe, sessions_loader=lambda: [])
    assert c.tick(store) == 2
    state["app"] = "slack"
    state["title"] = "chan"
    assert c.tick(store) == 2  # desktop + new focus
    focus = store.last_by_kind("focus")
    assert focus is not None
    assert focus.payload["app"] == "slack"
    store.close()


def test_default_probe_bad_json(monkeypatch) -> None:
    class R:
        stdout = "not-json\n"
        returncode = 0

    monkeypatch.setattr(focus_atspi.subprocess, "run", lambda *a, **k: R())
    assert focus_atspi._default_probe() == []


def test_default_probe_timeout(monkeypatch) -> None:
    def boom(*a, **k):
        raise focus_atspi.subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(focus_atspi.subprocess, "run", boom)
    assert focus_atspi._default_probe() == []
