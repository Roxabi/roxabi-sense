"""Focused / visible windows via AT-SPI (works on Cosmic without Wayland client).

Uses system python3 + PyGObject (gi) because the uv venv typically has no `gi`.
Gives app name + window title + ACTIVE flag. Not multi-monitor workspace yet
(that needs zcosmic_toplevel / foreign-toplevel Wayland client).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from roxabi_sense.store import Store

KIND = "focus"
SNAPSHOT = "desktop_snapshot"

# Runs under system interpreter (has PyGObject on Pop/Cosmic).
_PROBE_SCRIPT = r"""
import json
import sys
try:
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
except Exception as e:
    print(json.dumps({"error": f"gi:{e}", "windows": []}))
    sys.exit(0)

try:
    Atspi.init()
    desktop = Atspi.get_desktop(0)
except Exception as e:
    print(json.dumps({"error": f"init:{e}", "windows": []}))
    sys.exit(0)

windows = []
try:
    n_apps = desktop.get_child_count()
except Exception as e:
    print(json.dumps({"error": f"desktop:{e}", "windows": []}))
    sys.exit(0)

for i in range(n_apps):
    try:
        app = desktop.get_child_at_index(i)
        if app is None:
            continue
        app_name = app.get_name() or "unknown"
        n_win = app.get_child_count()
        for j in range(n_win):
            frame = app.get_child_at_index(j)
            if frame is None:
                continue
            role = frame.get_role_name() or ""
            if role not in {"frame", "window"}:
                continue
            title = frame.get_name() or ""
            states = {}
            try:
                st = frame.get_state_set()
                for name in ("ACTIVE", "FOCUSED", "SHOWING", "VISIBLE", "ICONIFIED"):
                    stype = getattr(Atspi.StateType, name, None)
                    if stype is not None:
                        states[name] = bool(st.contains(stype))
            except Exception:
                pass
            if not (states.get("SHOWING") or states.get("VISIBLE") or states.get("ACTIVE")):
                continue
            windows.append({
                "app": app_name,
                "title": title,
                "active": bool(states.get("ACTIVE")),
                "role": role,
            })
    except Exception:
        continue

print(json.dumps({"windows": windows}))
"""


@dataclass
class WindowInfo:
    app: str
    title: str
    active: bool
    role: str


class FocusAtspiCollector:
    name = "focus_atspi"

    def __init__(self, probe: Callable[[], list[WindowInfo]] | None = None) -> None:
        self._probe = probe or _default_probe
        self._last: str | None = None

    def tick(self, store: Store) -> int:
        windows = self._probe()
        payload: dict[str, Any] = {
            "windows": [
                {
                    "app": w.app,
                    "title": w.title,
                    "active": w.active,
                    "role": w.role,
                }
                for w in windows
            ],
            "source": "atspi",
        }
        active = next((w for w in windows if w.active), None)
        if active is not None:
            payload["focus"] = {
                "app": active.app,
                "title": active.title,
            }
        fingerprint = json.dumps(payload, sort_keys=True)
        if fingerprint == self._last:
            return 0
        self._last = fingerprint
        store.append(SNAPSHOT, payload)
        if active is not None:
            store.append(
                KIND,
                {
                    "app": active.app,
                    "title": active.title,
                    "source": "atspi",
                },
            )
            return 2
        return 1


def _system_python() -> str:
    for candidate in ("/usr/bin/python3", shutil.which("python3") or ""):
        if candidate and candidate != sys.executable:
            return candidate
    return sys.executable


def _default_probe() -> list[WindowInfo]:
    py = _system_python()
    try:
        proc = subprocess.run(
            [py, "-c", _PROBE_SCRIPT],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    line = (proc.stdout or "").strip().splitlines()
    if not line:
        return []
    try:
        data = json.loads(line[-1])
    except json.JSONDecodeError:
        return []
    raw = data.get("windows") or []
    out: list[WindowInfo] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            WindowInfo(
                app=str(item.get("app") or "unknown"),
                title=str(item.get("title") or ""),
                active=bool(item.get("active")),
                role=str(item.get("role") or ""),
            )
        )
    return out
