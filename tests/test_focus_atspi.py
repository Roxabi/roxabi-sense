from __future__ import annotations

from pathlib import Path

from roxabi_sense.atspi import agent as atspi_agent
from roxabi_sense.collectors.focus_atspi import FocusAtspiCollector, WindowInfo
from roxabi_sense.store import Store


def _patch_enrich(monkeypatch) -> None:
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_atspi.resolve_app_name",
        lambda app, pid: "ghostty" if app == "Unnamed" else app,
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_atspi.find_agent_link",
        lambda pid, app=None, title=None, sessions=None, tree=None: (
            {
                "agent": "grok",
                "session_id": "sid-1",
                "cwd": "/tmp/p",
                "pid": 99,
                "match": "session_pid",
            }
            if pid == 7280
            else None
        ),
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_atspi.children_map",
        lambda: {},
    )


def test_focus_dedup_and_normalize(tmp_path: Path, monkeypatch) -> None:
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

    _patch_enrich(monkeypatch)
    store = Store(tmp_path / "s.db")
    c = FocusAtspiCollector(probe=probe, sessions_loader=lambda: [])
    assert c.tick(store) == 2
    assert c.tick(store) == 0
    focus = store.last_by_kind("focus")
    assert focus is not None
    assert focus.payload["app"] == "ghostty"
    assert focus.payload["title"] == "My Task - grok"
    assert "Thinking" in (focus.payload.get("title_raw") or "")
    assert focus.payload["agent"]["session_id"] == "sid-1"
    assert store.count() == 2
    assert store.get_meta("focus_probe_count") == "2"
    assert store.get_meta("focus_probe_last_mode") == "full"
    store.close()


def test_tick_focus_skips_desktop_snapshot(tmp_path: Path, monkeypatch) -> None:
    def probe() -> list[WindowInfo]:
        return [
            WindowInfo(app="ghostty", title="A", active=True, role="frame", pid=1),
            WindowInfo(app="slack", title="B", active=False, role="frame", pid=2),
        ]

    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_atspi.resolve_app_name",
        lambda app, pid: app,
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_atspi.find_agent_link",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_atspi.children_map",
        lambda: {},
    )
    store = Store(tmp_path / "s.db")
    c = FocusAtspiCollector(probe=probe, sessions_loader=lambda: [])
    assert c.tick_focus(store) == 1
    assert store.last_by_kind("desktop_snapshot") is None
    assert store.last_by_kind("focus") is not None
    assert store.get_meta("focus_probe_last_mode") == "focus"
    # second identical → 0
    assert c.tick_focus(store) == 0
    store.close()


def test_tick_focus_uses_probe_focus_not_probe(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []

    def desktop() -> list[WindowInfo]:
        calls.append("desktop")
        return [
            WindowInfo(app="ghostty", title="A", active=True, role="frame", pid=1),
            WindowInfo(app="slack", title="B", active=False, role="frame", pid=2),
        ]

    def focus_only() -> list[WindowInfo]:
        calls.append("focus")
        return [WindowInfo(app="ghostty", title="A", active=True, role="frame", pid=1)]

    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_atspi.resolve_app_name",
        lambda app, pid: app,
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_atspi.find_agent_link",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_atspi.children_map",
        lambda: {},
    )
    store = Store(tmp_path / "s.db")
    c = FocusAtspiCollector(
        probe=desktop, probe_focus=focus_only, sessions_loader=lambda: []
    )
    assert c.tick_focus(store) == 1
    assert calls == ["focus"]
    assert c.tick_desktop(store) >= 1
    assert calls == ["focus", "desktop"]
    store.close()


def test_tick_desktop_writes_snapshot(tmp_path: Path, monkeypatch) -> None:
    state = {"bg": "chan-a"}

    def probe() -> list[WindowInfo]:
        return [
            WindowInfo(app="ghostty", title="A", active=True, role="frame", pid=1),
            WindowInfo(app="slack", title=state["bg"], active=False, role="frame", pid=2),
        ]

    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_atspi.resolve_app_name",
        lambda app, pid: app,
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_atspi.find_agent_link",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_atspi.children_map",
        lambda: {},
    )
    store = Store(tmp_path / "s.db")
    c = FocusAtspiCollector(probe=probe, sessions_loader=lambda: [])
    assert c.tick_desktop(store) == 2  # snapshot + focus
    assert store.last_by_kind("desktop_snapshot") is not None
    state["bg"] = "chan-b"
    assert c.tick_desktop(store) == 1  # desktop only
    assert store.get_meta("focus_probe_last_mode") == "desktop"
    store.close()


def test_agent_attach_updates_focus_key(tmp_path: Path, monkeypatch) -> None:
    """Stable title but agent appears later → new focus row."""
    state: dict = {"agent": None}

    def probe() -> list[WindowInfo]:
        return [
            WindowInfo(
                app="ghostty",
                title="My Task - grok",
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
        lambda pid, app=None, title=None, sessions=None, tree=None: state["agent"],
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_atspi.children_map",
        lambda: {},
    )
    store = Store(tmp_path / "s.db")
    c = FocusAtspiCollector(probe=probe, sessions_loader=lambda: [])
    assert c.tick(store) == 2
    state["agent"] = {
        "agent": "grok",
        "session_id": "new",
        "cwd": "/p",
        "pid": 9,
        "match": "tmux",
    }
    assert c.tick(store) >= 1
    focus = store.last_by_kind("focus")
    assert focus is not None
    assert focus.payload["agent"]["session_id"] == "new"
    store.close()


def test_desktop_only_change_no_new_focus(tmp_path: Path, monkeypatch) -> None:
    state = {"bg_title": "chan-a"}

    def probe() -> list[WindowInfo]:
        return [
            WindowInfo(
                app="ghostty",
                title="A - grok",
                active=True,
                role="frame",
                pid=1,
            ),
            WindowInfo(
                app="slack",
                title=state["bg_title"],
                active=False,
                role="frame",
                pid=2,
            ),
        ]

    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_atspi.resolve_app_name",
        lambda app, pid: app,
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_atspi.find_agent_link",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_atspi.children_map",
        lambda: {},
    )
    store = Store(tmp_path / "s.db")
    c = FocusAtspiCollector(probe=probe, sessions_loader=lambda: [])
    assert c.tick(store) == 2
    focus_n = store.count()
    state["bg_title"] = "chan-b"
    n = c.tick(store)
    assert n == 1
    assert store.count() == focus_n + 1
    focus = store.last_by_kind("focus")
    assert focus is not None
    assert focus.payload["title"] == "A - grok"
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
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_atspi.children_map",
        lambda: {},
    )
    store = Store(tmp_path / "s.db")
    c = FocusAtspiCollector(probe=probe, sessions_loader=lambda: [])
    assert c.tick(store) == 2
    state["app"] = "slack"
    state["title"] = "chan"
    assert c.tick(store) == 2
    focus = store.last_by_kind("focus")
    assert focus is not None
    assert focus.payload["app"] == "slack"
    store.close()


def test_default_probe_bad_json(monkeypatch) -> None:
    class R:
        stdout = "not-json\n"
        returncode = 0

    monkeypatch.setattr(atspi_agent.subprocess, "run", lambda *a, **k: R())
    assert atspi_agent.probe_once("desktop") == []


def test_default_probe_timeout(monkeypatch) -> None:
    def boom(*a, **k):
        raise atspi_agent.subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(atspi_agent.subprocess, "run", boom)
    assert atspi_agent.probe_once("focus") == []


def test_apply_from_agent_payload(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_atspi.resolve_app_name",
        lambda app, pid: app,
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_atspi.find_agent_link",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_atspi.children_map",
        lambda: {},
    )
    store = Store(tmp_path / "s.db")
    c = FocusAtspiCollector(probe=lambda: [], sessions_loader=lambda: [])
    n = c.apply(
        store,
        [{"app": "ghostty", "title": "A", "active": True, "role": "frame", "pid": 1}],
        mode="focus",
        probe_ms=4,
    )
    assert n == 1
    assert store.get_meta("focus_probe_last_ms") == "4"
    assert store.get_meta("focus_probe_last_mode") == "focus"
    assert store.last_by_kind("desktop_snapshot") is None
    store.close()
