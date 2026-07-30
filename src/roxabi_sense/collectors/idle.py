"""Idle detection — logind fallback writer (demoted when Wayland watch healthy).

Primary authority is Wayland ext-idle-notify via idle_watch (ADR-002).
This collector only runs when backend is logind or auto with watch unavailable.
"""

from __future__ import annotations

import subprocess
from typing import Any

from roxabi_sense.collectors.idle_facts import append_idle_transition
from roxabi_sense.store import Store

KIND = "idle"
SOURCE = "logind"


class IdleCollector:
    """Logind IdleHint/LockedHint — secondary writer only."""

    name = "idle"

    def __init__(self, *, threshold_s: float = 300.0, enabled: bool = True) -> None:
        self.threshold_s = threshold_s
        self.enabled = enabled
        self._last: tuple[bool | None, bool | None] | None = None

    def tick(self, store: Store) -> int:
        if not self.enabled:
            return 0
        state = self._read()
        idle = state.get("idle")
        locked = state.get("locked")
        idle_b = idle if isinstance(idle, bool) else None
        locked_b = locked if isinstance(locked, bool) else None
        key = (idle_b, locked_b)
        if key == self._last:
            return 0
        self._last = key
        if not isinstance(idle, bool):
            return 0
        append_idle_transition(
            store,
            idle=idle,
            source=SOURCE,
            threshold_s=self.threshold_s,
            extra={"locked": locked, "session": state.get("session")},
        )
        return 1

    def _read(self) -> dict[str, Any]:
        session = self._active_session()
        if not session:
            return {"idle": None, "locked": None, "source": SOURCE, "session": None}
        idle = self._prop(session, "IdleHint")
        locked = self._prop(session, "LockedHint")
        return {
            "idle": idle == "yes",
            "locked": locked == "yes",
            "session": session,
            "source": SOURCE,
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
            sid = parts[0]
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
