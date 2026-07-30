"""Focused / visible windows via AT-SPI (works on Cosmic without Wayland client).

Gives app name + window title + ACTIVE flag. Not multi-monitor workspace yet
(that needs zcosmic_toplevel / foreign-toplevel Wayland client).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from roxabi_sense.store import Store

KIND = "focus"
SNAPSHOT = "desktop_snapshot"


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
        payload = {
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


def _default_probe() -> list[WindowInfo]:
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except (ImportError, ValueError):
        return []

    try:
        Atspi.init()
        desktop = Atspi.get_desktop(0)
    except Exception:  # noqa: BLE001
        return []

    windows: list[WindowInfo] = []
    try:
        n_apps = desktop.get_child_count()
    except Exception:  # noqa: BLE001
        return []

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
                if role not in {"frame", "window", "application"}:
                    # still accept frames; skip pure widgets
                    if role not in {"frame", "window"}:
                        continue
                title = frame.get_name() or ""
                if not title and not app_name:
                    continue
                states = _states(frame, Atspi)
                if not (states.get("SHOWING") or states.get("VISIBLE") or states.get("ACTIVE")):
                    continue
                windows.append(
                    WindowInfo(
                        app=app_name,
                        title=title,
                        active=bool(states.get("ACTIVE")),
                        role=role,
                    )
                )
        except Exception:  # noqa: BLE001
            continue
    return windows


def _states(accessible: Any, Atspi: Any) -> dict[str, bool]:
    out: dict[str, bool] = {}
    try:
        st = accessible.get_state_set()
    except Exception:  # noqa: BLE001
        return out
    for name in ("ACTIVE", "FOCUSED", "SHOWING", "VISIBLE", "ICONIFIED"):
        stype = getattr(Atspi.StateType, name, None)
        if stype is None:
            continue
        try:
            out[name] = bool(st.contains(stype))
        except Exception:  # noqa: BLE001
            out[name] = False
    return out
