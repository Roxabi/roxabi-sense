"""COSMIC native focus probe; only completed Wayland state batches are used."""
from __future__ import annotations

import os
import struct
import time
from collections.abc import Mapping
from datetime import UTC, datetime

from roxabi_sense.collectors.focus.probes.cosmic_wire import CosmicWire
from roxabi_sense.collectors.focus.protocol import FocusWindow

_HEALTH_TIMEOUT_S = 8.0
_RECONNECT_S = 3.0


class CosmicFocusProbe:
    source = "cosmic"

    def __init__(self, *, env: Mapping[str, str] | None = None) -> None:
        self.env = dict(os.environ if env is None else env)
        self.wire: CosmicWire | None = None
        self.reason = "not_connected"
        self.observed_at: datetime | None = None
        self._retry_at = 0.0
        self._first_read = True
        self._ping_at = 0.0

    def probe(self) -> bool:
        if self.wire is not None:
            return True
        try:
            self.wire = CosmicWire.connect(self.env)
        except (OSError, ValueError, TimeoutError, struct.error):
            return False
        self.observed_at = None
        self.reason = "pending_batch"
        self._ping_at = time.monotonic()
        return True

    def _poll(self) -> list[FocusWindow]:
        now = time.monotonic()
        if self.wire is None:
            if now < self._retry_at:
                return []
            if not self.probe():
                self._retry_at = now + _RECONNECT_S
                self.reason = "connection_failure"
                return []
        wire = self.wire
        assert wire is not None
        wire.deadline = time.monotonic() + 0.05
        try:
            if (
                self.observed_at is not None
                and (datetime.now(UTC) - self.observed_at).total_seconds() > _HEALTH_TIMEOUT_S
            ):
                raise TimeoutError("Wayland observation expired")
            if wire.pending_sync is not None and now - self._ping_at > _HEALTH_TIMEOUT_S:
                raise TimeoutError("Wayland sync timeout")
            # Drain only a bounded number of packets so other collectors run.
            for _ in range(32):
                before = wire.ack_at
                wire.receive()
                if wire.ack_at != before:
                    self.observed_at = datetime.now(UTC)
            if wire.pending_sync is None:
                wire.ping()
                self._ping_at = now
            if now - wire.ack_at > _HEALTH_TIMEOUT_S:
                raise TimeoutError("Wayland health expired")
            if not wire.ready or wire.dirty:
                self.reason = "pending_batch"
                return []
            committed = [h.get("committed", {}) for h in wire.handles.values()]
            active = [h for h in committed if h.get("active")]
            if len(active) != 1:
                self.reason = "no_active" if not active else "ambiguous"
                return []
            selected = active[0]
            identifier = selected.get("identifier")
            if not isinstance(identifier, str) or not identifier:
                self.reason = "missing_identity"
                return []
            self.reason = "observed"
            return [
                FocusWindow(
                    app=str(h.get("app") or "unknown"),
                    title=str(h.get("title") or ""),
                    active=bool(h.get("active")),
                    window_id=f"{wire.epoch}:{h.get('identifier')}",
                )
                for h in committed if h.get("identifier")
            ]
        except (OSError, ValueError, TimeoutError, ConnectionError, struct.error):
            wire.close()
            self.wire = None
            self.reason = "connection_failure"
            self._retry_at = now + _RECONNECT_S
            return []

    def get_active(self) -> list[FocusWindow]:
        return self._poll()

    def get_desktop(self) -> list[FocusWindow]:
        first_read, self._first_read = self._first_read, False
        deadline = time.monotonic() + (0.4 if first_read else 0.0)
        windows = self._poll()
        while self.reason == "pending_batch" and time.monotonic() < deadline:
            time.sleep(0.01)
            windows = self._poll()
        return windows
