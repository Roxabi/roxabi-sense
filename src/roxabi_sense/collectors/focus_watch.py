"""AT-SPI focus event watcher (system python + GLib main loop).

Runs a long-lived subprocess that registers Atspi.EventListener callbacks and
prints one JSON line per event. The daemon main loop consumes lines and
re-probes/writes via FocusAtspiCollector.tick (dedup still applies).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import Any

# Long-running listener — system interpreter has gi/PyGObject.
_LISTENER_SCRIPT = r"""
import json
import sys

try:
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi, GLib
except Exception as e:
    print(json.dumps({"error": f"gi:{e}"}), flush=True)
    sys.exit(1)

# Coalesce bursts (spinners) — still event-driven, just not every frame.
_pending = False
_loop = None

def _flush():
    global _pending
    _pending = False
    print(json.dumps({"type": "focus_change", "source": "atspi"}), flush=True)
    return False  # one-shot timeout

def on_event(event):
    global _pending
    if _pending:
        return
    _pending = True
    # 80ms coalesce window
    GLib.timeout_add(80, _flush)

try:
    Atspi.init()
except Exception as e:
    print(json.dumps({"error": f"init:{e}"}), flush=True)
    sys.exit(1)

listener = Atspi.EventListener.new(on_event)
for t in (
    "window:activate",
    "window:create",
    "window:destroy",
    "object:state-changed:active",
    "object:property-change:accessible-name",
):
    try:
        listener.register(t)
    except Exception as e:
        print(json.dumps({"warn": f"register {t}: {e}"}), flush=True)

print(json.dumps({"type": "ready", "source": "atspi"}), flush=True)
_loop = GLib.MainLoop()
try:
    _loop.run()
except KeyboardInterrupt:
    pass
"""


def _system_python() -> str:
    for candidate in ("/usr/bin/python3", shutil.which("python3") or ""):
        if candidate and candidate != sys.executable:
            return candidate
    return sys.executable


class FocusAtspiWatch:
    """Subprocess AT-SPI listener → callback on focus/title events."""

    def __init__(self, on_event: Callable[[dict[str, Any]], None]) -> None:
        self._on_event = on_event
        self._proc: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._proc = subprocess.Popen(
            [_system_python(), "-u", "-c", _LISTENER_SCRIPT],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._thread = threading.Thread(
            target=self._read_loop,
            name="focus-atspi-watch",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout: float = 2.0) -> None:
        self._stop.set()
        proc = self._proc
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=timeout)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        self._proc = None
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                if self._stop.is_set():
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if msg.get("error"):
                    print(f"sense focus-watch: {msg['error']}", flush=True)
                    continue
                if msg.get("type") == "ready":
                    print("sense focus-watch: AT-SPI events armed", flush=True)
                    continue
                if msg.get("type") == "focus_change":
                    try:
                        self._on_event(msg)
                    except Exception as exc:  # noqa: BLE001
                        print(f"sense focus-watch callback error: {exc}", flush=True)
        finally:
            # surface stderr if process dies
            if proc.stderr is not None and not self._stop.is_set():
                err = proc.stderr.read()
                if err:
                    print(f"sense focus-watch stderr: {err[:500]}", flush=True)
