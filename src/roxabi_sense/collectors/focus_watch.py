"""AT-SPI focus event watcher (system python + GLib main loop).

Runs a long-lived subprocess that registers Atspi.EventListener callbacks and
prints one JSON line per event. Daemon re-probes via FocusAtspiCollector.tick_focus.

Name (accessible-name) events are off | throttled | on — Chrome tab churn is the
main reason throttled is the default.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import Any, Literal

NameEventsMode = Literal["off", "throttled", "on"]


def build_listener_script(
    *,
    name_events: NameEventsMode = "throttled",
    name_throttle_s: float = 10.0,
) -> str:
    """Build the AT-SPI listener subprocess source (testable)."""
    name_mode = name_events if name_events in {"off", "throttled", "on"} else "throttled"
    throttle_ms = max(0, int(float(name_throttle_s) * 1000))
    register_name = name_mode != "off"
    return f'''
import json
import sys
import time

try:
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi, GLib
except Exception as e:
    print(json.dumps({{"error": f"gi:{{e}}"}}), flush=True)
    sys.exit(1)

_NAME_MODE = {name_mode!r}
_NAME_THROTTLE_MS = {throttle_ms}
_pending = False
_pending_reason = "activate"
_last_name_emit = 0.0
_name_trail_scheduled = False

def _flush():
    global _pending, _pending_reason, _last_name_emit
    reason = _pending_reason
    _pending = False
    _pending_reason = "activate"
    if reason == "name":
        _last_name_emit = time.monotonic() * 1000.0
    print(json.dumps({{"type": "focus_change", "source": "atspi", "reason": reason}}), flush=True)
    return False

def _arm(reason: str) -> None:
    global _pending, _pending_reason
    if _pending:
        if reason == "activate":
            _pending_reason = "activate"
        return
    _pending = True
    _pending_reason = reason
    GLib.timeout_add(80, _flush)

def _name_trail_flush():
    global _name_trail_scheduled
    _name_trail_scheduled = False
    _arm("name")
    return False

def _event_reason(event) -> str:
    try:
        et = str(getattr(event, "type", "") or "")
    except Exception:
        et = ""
    if "accessible-name" in et or "property-change" in et:
        return "name"
    return "activate"

def on_event(event):
    global _name_trail_scheduled
    reason = _event_reason(event)
    if reason == "name":
        if _NAME_MODE == "off":
            return
        if _NAME_MODE == "throttled":
            now = time.monotonic() * 1000.0
            if now - _last_name_emit >= _NAME_THROTTLE_MS:
                _arm("name")
            elif not _name_trail_scheduled:
                # Trailing edge: flush once at end of throttle window.
                _name_trail_scheduled = True
                rem = max(1, int(_NAME_THROTTLE_MS - (now - _last_name_emit)))
                GLib.timeout_add(rem, _name_trail_flush)
            return
    _arm(reason)

try:
    Atspi.init()
except Exception as e:
    print(json.dumps({{"error": f"init:{{e}}"}}), flush=True)
    sys.exit(1)

listener = Atspi.EventListener.new(on_event)
_types = [
    "window:activate",
    "window:create",
    "window:destroy",
    "object:state-changed:active",
]
if {register_name!r}:
    _types.append("object:property-change:accessible-name")
for t in _types:
    try:
        listener.register(t)
    except Exception as e:
        print(json.dumps({{"warn": f"register {{t}}: {{e}}"}}), flush=True)

print(json.dumps({{"type": "ready", "source": "atspi", "name_events": _NAME_MODE}}), flush=True)
_loop = GLib.MainLoop()
try:
    _loop.run()
except KeyboardInterrupt:
    pass
'''


def _system_python() -> str:
    for candidate in ("/usr/bin/python3", shutil.which("python3") or ""):
        if candidate and candidate != sys.executable:
            return candidate
    return sys.executable


class FocusAtspiWatch:
    """Subprocess AT-SPI listener → callback on focus/title events."""

    def __init__(
        self,
        on_event: Callable[[dict[str, Any]], None],
        *,
        name_events: NameEventsMode = "throttled",
        name_throttle_s: float = 10.0,
    ) -> None:
        self._on_event = on_event
        mode: NameEventsMode = (
            name_events if name_events in ("off", "throttled", "on") else "throttled"
        )
        self._name_events: NameEventsMode = mode
        self._name_throttle_s = name_throttle_s
        self._proc: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        if self.running:
            return
        if self._proc is not None and self._proc.poll() is not None:
            try:
                self._proc.wait(timeout=0.1)
            except Exception:  # noqa: BLE001
                pass
            self._proc = None
        self._stop.clear()
        script = build_listener_script(
            name_events=self._name_events,
            name_throttle_s=self._name_throttle_s,
        )
        self._proc = subprocess.Popen(
            [_system_python(), "-u", "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
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
                if proc.stdout is not None:
                    proc.stdout.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                proc.terminate()
                proc.wait(timeout=timeout)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                    proc.wait(timeout=timeout)
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
                if msg.get("warn"):
                    print(f"sense focus-watch: {msg['warn']}", flush=True)
                    continue
                if msg.get("type") == "ready":
                    print(
                        f"sense focus-watch: AT-SPI events armed "
                        f"(name_events={msg.get('name_events', '?')})",
                        flush=True,
                    )
                    continue
                if msg.get("type") == "focus_change":
                    try:
                        self._on_event(msg)
                    except Exception as exc:  # noqa: BLE001
                        print(f"sense focus-watch callback error: {exc}", flush=True)
        except ValueError:
            pass
