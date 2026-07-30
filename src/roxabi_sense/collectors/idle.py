"""Idle detection via systemd-logind session IdleHint.

cosmic-idle owns org.freedesktop.ScreenSaver but only Inhibit/UnInhibit —
no idle-seconds API. logind IdleHint is the reliable boolean on this machine.
"""

from __future__ import annotations

import subprocess
from typing import Any

from roxabi_sense.store import Store

KIND = "idle"


class IdleCollector:
    name = "idle"

    def __init__(self) -> None:
        self._last: tuple[bool | None, bool | None] | None = None

    def tick(self, store: Store) -> int:
        state = self._read()
        key = (state.get("idle"), state.get("locked"))
        if key == self._last:
            return 0
        self._last = key  # type: ignore[assignment]
        store.append(KIND, state)
        return 1

    def _read(self) -> dict[str, Any]:
        session = self._active_session()
        if not session:
            return {"idle": None, "locked": None, "source": "logind", "session": None}
        idle = self._prop(session, "IdleHint")
        locked = self._prop(session, "LockedHint")
        idle_since = self._prop(session, "IdleSinceHint")
        return {
            "idle": idle == "yes",
            "locked": locked == "yes",
            "idle_since_hint": idle_since,
            "session": session,
            "source": "logind",
        }

    @staticmethod
    def _active_session() -> str | None:
        try:
            proc = subprocess.run(
                ["loginctl", "list-sessions", "--no-legend"],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            sid, _uid, user = parts[0], parts[1], parts[2]
            # Prefer seat sessions (graphical)
            if "seat" in line or user:
                active = IdleCollector._prop(sid, "Active")
                stype = IdleCollector._prop(sid, "Type")
                if active == "yes" and stype in {"wayland", "x11", "mir", "tty"}:
                    return sid
        return None

    @staticmethod
    def _prop(session: str, name: str) -> str | None:
        try:
            proc = subprocess.run(
                ["loginctl", "show-session", session, "-p", name, "--value"],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        val = proc.stdout.strip()
        return val or None
