"""AT-SPI FocusProbe — one-shot via probe_once (event path stays on agent)."""

from __future__ import annotations

from roxabi_sense.atspi import probe_once
from roxabi_sense.collectors.focus.protocol import (
    FocusWindow,
    raw_dicts_to_windows,
)


class AtspiFocusProbe:
    """Poll path for AT-SPI. Event-driven facts use the long-lived agent + collector.apply."""

    source: str = "atspi"

    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = enabled
        self._last_ok: bool | None = None

    def mark_healthy(self, healthy: bool) -> None:
        """Daemon sets this when the long-lived agent is ready/dead."""
        self._last_ok = healthy

    def probe(self) -> bool:
        if not self._enabled:
            return False
        if self._last_ok is not None:
            return self._last_ok
        # Cold check: empty / exception = unusable (probe_once rarely raises).
        try:
            rows = probe_once("focus")
            return isinstance(rows, list) and len(rows) > 0
        except Exception:  # noqa: BLE001
            return False

    def get_active(self) -> list[FocusWindow]:
        try:
            return raw_dicts_to_windows(probe_once("focus"))
        except Exception:  # noqa: BLE001
            return []

    def get_desktop(self) -> list[FocusWindow]:
        try:
            return raw_dicts_to_windows(probe_once("desktop"))
        except Exception:  # noqa: BLE001
            return []
