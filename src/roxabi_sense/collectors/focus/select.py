"""Ordered FocusProbe candidates from session info; first probe() wins."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from roxabi_sense.collectors.focus.probes.atspi import AtspiFocusProbe
from roxabi_sense.collectors.focus.probes.kde import KdeFocusProbe
from roxabi_sense.collectors.focus.probes.noop import NoopFocusProbe
from roxabi_sense.collectors.focus.probes.wlr import WlrFocusProbe
from roxabi_sense.collectors.focus.probes.x11 import X11FocusProbe
from roxabi_sense.collectors.focus.protocol import FocusProbe, FocusSource
from roxabi_sense.collectors.focus.session import SessionInfo, detect_session

ProbeFactory = Callable[[], FocusProbe]


def candidate_sources(session: SessionInfo) -> list[FocusSource]:
    """Deterministic backend order for env (always ends with noop)."""
    if session.session_type == "x11":
        return ["x11", "atspi", "noop"]
    if session.desktop_family == "wlroots":
        return ["wlr", "atspi", "x11", "noop"]
    if session.desktop_family == "kde":
        # AT-SPI often works on Plasma; kde stub is alternative until D-Bus ships
        return ["atspi", "kde", "x11", "noop"]
    # gnome / cosmic / unknown wayland
    return ["atspi", "x11", "noop"]


def build_probe(source: FocusSource, *, env: Mapping[str, str] | None = None) -> FocusProbe:
    env_map = dict(env) if env is not None else None
    if source == "atspi":
        return AtspiFocusProbe()
    if source == "x11":
        return X11FocusProbe(env=env_map)
    if source == "wlr":
        return WlrFocusProbe(env=env_map)
    if source == "kde":
        return KdeFocusProbe()
    if source == "noop":
        return NoopFocusProbe()
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
