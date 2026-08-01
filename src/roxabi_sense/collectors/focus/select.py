"""Ordered FocusProbe candidates from session info; first probe() wins."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from roxabi_sense.collectors.focus.probes.atspi import AtspiFocusProbe
from roxabi_sense.collectors.focus.probes.noop import NoopFocusProbe
from roxabi_sense.collectors.focus.probes.x11 import X11FocusProbe
from roxabi_sense.collectors.focus.protocol import FocusProbe, FocusSource
from roxabi_sense.collectors.focus.session import SessionInfo, detect_session

ProbeFactory = Callable[[], FocusProbe]


def candidate_sources(session: SessionInfo) -> list[FocusSource]:
    """Deterministic backend order for env (always ends with noop)."""
    if session.session_type == "x11":
        return ["x11", "atspi", "noop"]
    # wayland + unknown: prefer a11y, then x11 (XWayland), then noop
    return ["atspi", "x11", "noop"]


def build_probe(source: FocusSource, *, env: Mapping[str, str] | None = None) -> FocusProbe:
    if source == "atspi":
        return AtspiFocusProbe()
    if source == "x11":
        return X11FocusProbe(env=dict(env) if env is not None else None)
    if source == "noop":
        return NoopFocusProbe()
    # wlr/kde reserved for P1 — treat as noop until implemented
    return NoopFocusProbe()


def select_probe(
    *,
    session: SessionInfo | None = None,
    env: Mapping[str, str] | None = None,
    factories: Sequence[ProbeFactory] | None = None,
) -> FocusProbe:
    """First candidate with probe() True; always has noop as last resort."""
    info = session or detect_session(env)
    if factories is not None:
        for factory in factories:
            p = factory()
            if p.probe():
                return p
        return NoopFocusProbe()
    for source in candidate_sources(info):
        p = build_probe(source, env=env)
        if p.probe():
            return p
    return NoopFocusProbe()
