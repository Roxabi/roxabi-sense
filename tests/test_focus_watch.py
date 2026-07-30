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
                json.dumps({"type": "focus_change", "source": "atspi"}) + "\n",
            ]

        def __iter__(self):
            return iter(self.lines)

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
    # reader thread
    if watch._thread is not None:
        watch._thread.join(timeout=2)
    watch.stop()
    assert any(e.get("type") == "focus_change" for e in events)
