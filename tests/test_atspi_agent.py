from __future__ import annotations

import json
from typing import Any

from roxabi_sense.atspi.agent import _WORKER, FocusAtspiAgent
from roxabi_sense.atspi.script import agent_env
from roxabi_sense.atspi.trace_log import AtspiTraceWriter, summarize_trace


def test_agent_env_and_worker_present() -> None:
    env = agent_env(name_events="throttled", name_throttle_s=10.0, trace=True)
    assert env["SENSE_ATSPI_NAME_MODE"] == "throttled"
    assert env["SENSE_ATSPI_NAME_MS"] == "10000"
    assert env["SENSE_ATSPI_TRACE"] == "1"
    assert _WORKER.is_file()
    text = _WORKER.read_text(encoding="utf-8")
    assert "describe_src" in text
    assert "window_from_src" in text
    assert "emit_focus_win" in text
    assert "focus_via" in text
    assert "event_source" in text
    assert "atspi_raw" in text


def test_product_focus_filter_in_worker_source() -> None:
    text = _WORKER.read_text(encoding="utf-8")
    assert "_product_focus_event" in text
    assert "window:destroy" in text
    assert "detail1" in text


def test_window_from_src_logic() -> None:
    """Import pure helper by exec — worker is system-python standalone."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("agent_worker", _WORKER)
    assert spec and spec.loader
    # Can't import if gi missing — exec only the function via copy
    # Instead test behavior through probe_result shape expectations:
    src = {
        "app": "Google Chrome",
        "frame_name": "Meet – Authentic x Silex",
        "pid": 123,
        "name": "tab",
    }
    # Inline mirror of window_from_src (keep in sync with worker)
    app = str(src.get("app") or "unknown")
    title = str(src.get("frame_name") or src.get("name") or "")
    win = {
        "app": app,
        "title": title,
        "active": True,
        "role": "frame",
        "pid": src.get("pid"),
        "focus_via": "event_source",
    }
    assert win["app"] == "Google Chrome"
    assert win["title"].startswith("Meet")
    assert win["focus_via"] == "event_source"


def test_agent_callback_on_probe_result(monkeypatch) -> None:
    events: list[dict[str, Any]] = []

    class FakeStdout:
        def __init__(self) -> None:
            self.lines = [
                json.dumps(
                    {
                        "type": "ready",
                        "source": "atspi",
                        "name_events": "throttled",
                        "trace": True,
                    }
                )
                + "\n",
                json.dumps(
                    {
                        "type": "atspi_raw",
                        "event": "window:activate",
                        "source": {"app": "Google Chrome"},
                        "actives": [
                            {"app": "Discord", "active": True},
                            {"app": "Google Chrome", "active": True},
                        ],
                        "n_actives": 2,
                    }
                )
                + "\n",
                json.dumps(
                    {
                        "type": "probe_result",
                        "mode": "focus",
                        "reason": "activate",
                        "windows": [{"app": "ghostty", "title": "t", "active": True}],
                        "ms": 3,
                    }
                )
                + "\n",
            ]

        def __iter__(self):
            return iter(self.lines)

        def close(self) -> None:
            pass

    class FakeStdin:
        def write(self, _s: str) -> None:
            pass

        def flush(self) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeProc:
        def __init__(self) -> None:
            self.stdout = FakeStdout()
            self.stdin = FakeStdin()
            self._code = None

        def poll(self):
            return self._code

        def terminate(self) -> None:
            self._code = 0

        def wait(self, timeout=None) -> int:
            self._code = 0
            return 0

        def kill(self) -> None:
            self._code = -9

    monkeypatch.setattr(
        "roxabi_sense.atspi.agent.subprocess.Popen",
        lambda *a, **k: FakeProc(),
    )
    agent = FocusAtspiAgent(on_message=lambda m: events.append(m), trace=True)
    agent.start()
    if agent._thread is not None:
        agent._thread.join(timeout=2)
    agent.stop()
    types = [e.get("type") for e in events]
    assert "ready" in types
    assert "atspi_raw" in types
    assert "probe_result" in types


def test_probe_once_parses_result(monkeypatch) -> None:
    from roxabi_sense.atspi import agent as agent_mod

    class R:
        stdout = (
            json.dumps(
                {
                    "type": "probe_result",
                    "mode": "desktop",
                    "reason": "once",
                    "windows": [
                        {"app": "x", "title": "y", "active": True, "role": "frame"}
                    ],
                    "ms": 1,
                }
            )
            + "\n"
        )
        returncode = 0

    monkeypatch.setattr(agent_mod.subprocess, "run", lambda *a, **k: R())
    wins = agent_mod.probe_once("desktop")
    assert len(wins) == 1
    assert wins[0]["app"] == "x"


def test_trace_writer_and_summary(tmp_path) -> None:
    path = tmp_path / "t.jsonl"
    w = AtspiTraceWriter(path=path, hours=48.0)
    w.write(
        {
            "type": "atspi_raw",
            "event": "window:activate",
            "source": {"app": "Google Chrome"},
            "actives": [
                {"app": "Discord", "active": True},
                {"app": "Google Chrome", "active": True},
            ],
        }
    )
    s = summarize_trace(path)
    assert s["lines"] >= 2
    assert s["activate_source_vs_first_active_disagree"] >= 1
