"""Focus probe selection + runtime demote/recover (daemon helper)."""

from __future__ import annotations

from collections.abc import Mapping

from roxabi_sense.collectors.focus.collector import FocusCollector
from roxabi_sense.collectors.focus.probes.atspi import AtspiFocusProbe
from roxabi_sense.collectors.focus.probes.noop import NoopFocusProbe
from roxabi_sense.collectors.focus.probes.x11 import X11FocusProbe
from roxabi_sense.collectors.focus.protocol import FocusProbe, FocusSource
from roxabi_sense.collectors.focus.select import build_probe, candidate_sources
from roxabi_sense.collectors.focus.session import SessionInfo, detect_session
from roxabi_sense.store import Store

FocusStatus = str  # available | degraded | unavailable


class FocusRuntime:
    """Owns ordered candidates, active probe, and meta for focus backend."""

    def __init__(
        self,
        collector: FocusCollector,
        *,
        env: Mapping[str, str] | None = None,
        session: SessionInfo | None = None,
    ) -> None:
        self.collector = collector
        self._env = env
        self.session = session or detect_session(env)
        self.preferred: list[FocusSource] = candidate_sources(self.session)
        self._probes: dict[str, FocusProbe] = {}
        for src in self.preferred:
            self._probes[src] = build_probe(src, env=env)
        self.active: FocusProbe = NoopFocusProbe()
        self._atspi = self._probes.get("atspi")
        if isinstance(self._atspi, AtspiFocusProbe):
            pass
        else:
            self._atspi = None

    def atspi_probe(self) -> AtspiFocusProbe | None:
        p = self._probes.get("atspi")
        return p if isinstance(p, AtspiFocusProbe) else None

    def write_session_meta(self, store: Store) -> None:
        store.set_meta("session_type", self.session.session_type)
        store.set_meta("desktop_family", self.session.desktop_family)

    def select_initial(self, store: Store) -> FocusProbe:
        """Pick first healthy probe; AT-SPI may be marked healthy later by agent."""
        # Prefer probes that probe() true. For atspi, allow selection if in list
        # even when cold probe fails — agent may come up; then demote if dead.
        chosen: FocusProbe | None = None
        for src in self.preferred:
            p = self._probes[src]
            if src == "atspi":
                # Try start path: select atspi optimistically if first preferred
                if src == self.preferred[0]:
                    chosen = p
                    break
                if p.probe():
                    chosen = p
                    break
                continue
            if p.probe():
                chosen = p
                break
        if chosen is None:
            chosen = NoopFocusProbe()
        return self._activate(store, chosen, reason="initial")

    def mark_atspi(self, store: Store, *, healthy: bool) -> None:
        ap = self.atspi_probe()
        if ap is not None:
            ap.mark_healthy(healthy)
        if healthy:
            if self.preferred and self.preferred[0] == "atspi":
                if self.active.source != "atspi":
                    self._activate(store, self._probes["atspi"], reason="recover")
            return
        # unhealthy
        if self.active.source == "atspi":
            self.demote(store, reason="atspi_dead")

    def demote(self, store: Store, *, reason: str) -> FocusProbe:
        """Move to next healthy non-atspi candidate (or noop)."""
        for src in self.preferred:
            if src == "atspi":
                continue
            p = self._probes[src]
            if p.probe():
                return self._activate(store, p, reason=reason)
        return self._activate(store, NoopFocusProbe(), reason=reason)

    def _activate(self, store: Store, probe: FocusProbe, *, reason: str) -> FocusProbe:
        prev = getattr(self.active, "source", None)
        self.active = probe
        self.collector.set_focus_probe(probe)
        self.collector.set_source(str(probe.source))
        status = self._status_for(probe)
        store.set_meta("focus_backend", str(probe.source))
        store.set_meta("focus_status", status)
        self.write_session_meta(store)
        if prev != probe.source:
            print(
                f"sense focus-backend: {probe.source} ({status}) reason={reason}",
                flush=True,
            )
        return probe

    def _status_for(self, probe: FocusProbe) -> FocusStatus:
        if probe.source == "noop":
            return "unavailable"
        if self.preferred and probe.source != self.preferred[0]:
            return "degraded"
        return "available"

    def poll_uses_probe(self) -> bool:
        """True when focus ticks should call collector.tick (not only AT-SPI events)."""
        return self.active.source != "atspi"


def ensure_x11_registered() -> type[X11FocusProbe]:
    return X11FocusProbe
