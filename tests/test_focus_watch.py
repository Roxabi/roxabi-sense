from __future__ import annotations

import json
from typing import Any

from roxabi_sense.collectors.focus_watch import FocusAtspiWatch


def test_watch_invokes_callback_on_focus_change_line(monkeypatch) -> None:
    events: list[dict[str, Any]] = []

    class FakeStdout:
        def __init__(self) -> None:
            self.lines = [
                json.dumps({"type": "ready", "source": "atspi"}) + "\n",
                "not-json\n",
                "[]\n",
                json.dumps({"error": "gi:fail"}) + "\n",
                json.dumps({"warn": "register x"}) + "\n",
                json.dumps({"type": "focus_change", "source": "atspi"}) + "\n",
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
    # only focus_change reaches callback (not ready/error/warn/bad json)
    assert len(events) == 1
    assert events[0].get("type") == "focus_change"


def test_callback_exception_isolated(monkeypatch) -> None:
    calls = {"n": 0}

    def boom(_m: dict[str, Any]) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("cb fail")

    class FakeStdout:
        def __iter__(self):
            yield json.dumps({"type": "focus_change"}) + "\n"
            yield json.dumps({"type": "focus_change"}) + "\n"

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
    watch = FocusAtspiWatch(on_event=boom)
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
            return None  # still running

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
    watch = FocusAtspiWatch(on_event=lambda m: None)
    watch.start()
    watch.start()
    assert n_popen["n"] == 1
    watch.stop()
