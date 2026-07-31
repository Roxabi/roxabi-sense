from __future__ import annotations

import json
from typing import Any

from roxabi_sense.collectors.focus_watch import FocusAtspiWatch, build_listener_script


def test_build_listener_script_name_modes() -> None:
    off = build_listener_script(name_events="off", name_throttle_s=10.0)
    assert "_NAME_MODE = 'off'" in off
    assert "if False:" in off  # do not register accessible-name

    throttled = build_listener_script(name_events="throttled", name_throttle_s=10.0)
    assert "_NAME_MODE = 'throttled'" in throttled
    assert "_NAME_THROTTLE_MS = 10000" in throttled
    assert "if True:" in throttled
    assert "accessible-name" in throttled

    on = build_listener_script(name_events="on", name_throttle_s=1.0)
    assert "_NAME_MODE = 'on'" in on
    assert "_NAME_THROTTLE_MS = 1000" in on


def test_watch_invokes_callback_on_focus_change_line(monkeypatch) -> None:
    events: list[dict[str, Any]] = []

    class FakeStdout:
        def __init__(self) -> None:
            self.lines = [
                json.dumps({"type": "ready", "source": "atspi", "name_events": "throttled"})
                + "\n",
                "not-json\n",
                "[]\n",
                json.dumps({"error": "gi:fail"}) + "\n",
                json.dumps({"warn": "register x"}) + "\n",
                json.dumps(
                    {"type": "focus_change", "source": "atspi", "reason": "activate"}
                )
                + "\n",
            ]

        def __iter__(self):
            return iter(self.lines)

        def close(self) -> None:
            pass

    class FakeProc:
        def __init__(self) -> None:
            self.stdout = FakeStdout()
            self.stderr = None
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
        "roxabi_sense.collectors.focus_watch.subprocess.Popen",
        lambda *a, **k: FakeProc(),
    )

    watch = FocusAtspiWatch(on_event=lambda m: events.append(m))
    watch.start()
    if watch._thread is not None:
        watch._thread.join(timeout=2)
        assert not watch._thread.is_alive()
    watch.stop()
    assert len(events) == 1
    assert events[0].get("type") == "focus_change"
    assert events[0].get("reason") == "activate"


def test_callback_exception_isolated(monkeypatch) -> None:
    calls = {"n": 0}

    def boom(_m: dict[str, Any]) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("cb fail")

    class FakeStdout:
        def __iter__(self):
            yield json.dumps({"type": "focus_change", "reason": "name"}) + "\n"
            yield json.dumps({"type": "focus_change", "reason": "activate"}) + "\n"

        def close(self) -> None:
            pass

    class FakeProc:
        def __init__(self) -> None:
            self.stdout = FakeStdout()
            self.stderr = None
            self._code = None

        def poll(self):
            return self._code

        def terminate(self) -> None:
            self._code = 0

        def wait(self, timeout=None) -> int:
            return 0

        def kill(self) -> None:
            pass

    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_watch.subprocess.Popen",
        lambda *a, **k: FakeProc(),
    )
    watch = FocusAtspiWatch(on_event=boom, name_events="throttled")
    watch.start()
    if watch._thread is not None:
        watch._thread.join(timeout=2)
    watch.stop()
    assert calls["n"] == 2


def test_start_idempotent_when_running(monkeypatch) -> None:
    n_popen = {"n": 0}

    class FakeStdout:
        def __iter__(self):
            return iter(())

        def close(self) -> None:
            pass

    class FakeProc:
        def __init__(self) -> None:
            self.stdout = FakeStdout()
            self.stderr = None
            self._code = None

        def poll(self):
            return None

        def terminate(self) -> None:
            self._code = 0

        def wait(self, timeout=None) -> int:
            return 0

        def kill(self) -> None:
            pass

    def fake_popen(*a, **k):
        n_popen["n"] += 1
        return FakeProc()

    monkeypatch.setattr(
        "roxabi_sense.collectors.focus_watch.subprocess.Popen",
        fake_popen,
    )
    watch = FocusAtspiWatch(on_event=lambda m: None, name_events="off")
    watch.start()
    watch.start()
    assert n_popen["n"] == 1
    watch.stop()
