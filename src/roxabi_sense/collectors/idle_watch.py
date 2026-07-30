"""Wayland ext-idle-notify watcher subprocess (ADR-002 primary idle authority).

Prints JSON lines:
  {"type":"ready","source":"wayland-idle"}
  {"type":"idle","idle":true,"source":"wayland-idle"}
  {"type":"idle","idle":false,"source":"wayland-idle"}
  {"type":"error","error":"..."}
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import Any

SOURCE = "wayland-idle"

# System python may have pywayland; uv env usually does not.
_LISTENER_SCRIPT = r"""
import json
import os
import sys
import time

threshold_ms = int(sys.argv[1]) if len(sys.argv) > 1 else 300000
source = "wayland-idle"

def emit(obj):
    print(json.dumps(obj), flush=True)

if not os.environ.get("WAYLAND_DISPLAY"):
    emit({"type": "error", "error": "no WAYLAND_DISPLAY"})
    sys.exit(2)

try:
    from pywayland.client import Display
    from pywayland.protocol.wayland import WlSeat
except Exception as e:
    emit({"type": "error", "error": f"pywayland:{e}"})
    sys.exit(3)

# Protocol may be generated or missing — try ext_idle_notify
try:
    from pywayland.protocol.ext_idle_notify_v1 import ExtIdleNotifierV1
except Exception:
    try:
        from protocol.ext_idle_notify_v1 import ExtIdleNotifierV1  # type: ignore
    except Exception as e:
        emit({"type": "error", "error": f"no ext_idle_notify protocol: {e}"})
        sys.exit(4)

notifier = None
seat = None
display = Display()
display.connect()
registry = display.get_registry()

def registry_global(reg, name, interface, version):
    global notifier, seat
    if interface == "ext_idle_notifier_v1":
        notifier = reg.bind(name, ExtIdleNotifierV1, min(version, 2))
    elif interface == "wl_seat":
        if seat is None:
            seat = reg.bind(name, WlSeat, min(version, 1))

registry.dispatcher["global"] = registry_global
display.roundtrip()
display.roundtrip()

if notifier is None or seat is None:
    emit({"type": "error", "error": "missing ext_idle_notifier_v1 or wl_seat"})
    sys.exit(5)

# Prefer input-idle (ignore inhibitors) when available
try:
    if hasattr(notifier, "get_input_idle_notification"):
        notification = notifier.get_input_idle_notification(threshold_ms, seat)
    else:
        notification = notifier.get_idle_notification(threshold_ms, seat)
except Exception as e:
    emit({"type": "error", "error": f"get_idle_notification:{e}"})
    sys.exit(6)

def on_idled(n, *a):
    emit({"type": "idle", "idle": True, "source": source})

def on_resumed(n, *a):
    emit({"type": "idle", "idle": False, "source": source})

notification.dispatcher["idled"] = on_idled
notification.dispatcher["resumed"] = on_resumed
emit({"type": "ready", "source": source, "threshold_ms": threshold_ms})

try:
    while True:
        display.dispatch(block=True)
except KeyboardInterrupt:
    pass
"""


def _system_python() -> str:
    for candidate in ("/usr/bin/python3", shutil.which("python3") or ""):
        if candidate:
            return candidate
    return sys.executable


class IdleWatch:
    """Subprocess Wayland idle-notify → callback on idle enter/leave."""

    def __init__(
        self,
        on_event: Callable[[dict[str, Any]], None],
        *,
        threshold_s: float = 300.0,
    ) -> None:
        self._on_event = on_event
        self.threshold_s = threshold_s
        self._proc: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.last_error: str | None = None
        self.ready = False

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
        self.ready = False
        self.last_error = None
        threshold_ms = max(1000, int(self.threshold_s * 1000))
        env = os.environ.copy()
        self._proc = subprocess.Popen(
            [_system_python(), "-u", "-c", _LISTENER_SCRIPT, str(threshold_ms)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=env,
        )
        self._thread = threading.Thread(
            target=self._read_loop,
            name="idle-wayland-watch",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        proc = self._proc
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        self._proc = None
        self.ready = False

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
                typ = msg.get("type")
                if typ == "ready":
                    self.ready = True
                elif typ == "error":
                    self.last_error = str(msg.get("error") or "error")
                    self.ready = False
                try:
                    self._on_event(msg)
                except Exception:  # noqa: BLE001
                    pass
        finally:
            self.ready = False
