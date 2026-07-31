from __future__ import annotations

import json
from typing import Any

from roxabi_sense.atspi.agent import FocusAtspiAgent
from roxabi_sense.atspi.script import build_agent_script


def test_build_agent_script_name_modes() -> None:
    off = build_agent_script(name_events="off", name_throttle_s=10.0, probe_min_s=0.5)
    assert "_NAME_MODE = 'off'" in off
    assert "if False:" in off
    assert "probe_result" in off
    assert "SENSE_ATSPI_ONCE" in off

    throttled = build_agent_script(
        name_events="throttled", name_throttle_s=10.0, probe_min_s=0.5
    )
    assert "_NAME_MODE = 'throttled'" in throttled
    assert "_NAME_THROTTLE_MS = 10000" in throttled
    assert "_PROBE_MIN_MS = 500" in throttled
    assert "name_trail_flush" in throttled
    assert "if True:" in throttled

    on = build_agent_script(name_events="on", name_throttle_s=1.0, probe_min_s=0.25)
    assert "_NAME_MODE = 'on'" in on
    assert "_PROBE_MIN_MS = 250" in on


def test_agent_callback_on_probe_result(monkeypatch) -> None:
    events: list[dict[str, Any]] = []

    class FakeStdout:
        def __init__(self) -> None:
            self.lines = [
                json.dumps({"type": "ready", "source": "atspi", "name_events": "throttled"})
                + "\n",
                "not-json\n",
                json.dumps({"type": "error", "error": "gi:fail"}) + "\n",
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
    agent = FocusAtspiAgent(on_message=lambda m: events.append(m))
    agent.start()
    if agent._thread is not None:
        agent._thread.join(timeout=2)
    agent.stop()
    types = [e.get("type") for e in events]
    assert "ready" in types
    assert "probe_result" in types
    pr = next(e for e in events if e.get("type") == "probe_result")
    assert pr["mode"] == "focus"
    assert pr["windows"][0]["app"] == "ghostty"


def test_probe_once_parses_result(monkeypatch) -> None:
    from roxabi_sense.atspi import agent as agent_mod

    class R:
        stdout = (
            json.dumps(
                {
                    "type": "probe_result",
                    "mode": "desktop",
                    "reason": "once",
                    "windows": [{"app": "x", "title": "y", "active": True, "role": "frame"}],
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
