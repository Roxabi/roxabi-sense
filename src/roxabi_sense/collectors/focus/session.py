"""Session / DE detection from environment (no host-named packages)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

SessionType = Literal["x11", "wayland", "unknown"]
DesktopFamily = Literal["gnome", "cosmic", "kde", "wlroots", "unknown"]


@dataclass(frozen=True)
class SessionInfo:
    session_type: SessionType
    desktop_family: DesktopFamily
    desktop_raw: str


def detect_session(env: Mapping[str, str] | None = None) -> SessionInfo:
    e = env if env is not None else os.environ
    st = _session_type(e.get("XDG_SESSION_TYPE", ""))
    raw = (e.get("XDG_CURRENT_DESKTOP") or e.get("DESKTOP_SESSION") or "").strip()
    family = _desktop_family(raw, e)
    return SessionInfo(session_type=st, desktop_family=family, desktop_raw=raw)


def _session_type(raw: str) -> SessionType:
    v = raw.strip().lower()
    if v in {"x11", "xorg"}:
        return "x11"
    if v in {"wayland", "wayland-session"}:
        return "wayland"
    return "unknown"


def _desktop_family(desktop_raw: str, env: Mapping[str, str]) -> DesktopFamily:
    tokens = {t.strip().lower() for t in desktop_raw.replace(":", ";").split(";") if t.strip()}
    tokens |= {t.strip().lower() for t in desktop_raw.split(":") if t.strip()}
    # compositor hints
    if env.get("HYPRLAND_INSTANCE_SIGNATURE") or env.get("SWAYSOCK"):
        return "wlroots"
    joined = " ".join(tokens)
    if any(x in tokens for x in ("cosmic",)) or "cosmic" in joined:
        return "cosmic"
    if any(x in tokens for x in ("gnome", "ubuntu:gnome", "pop:gnome")) or "gnome" in joined:
        return "gnome"
    if any(x in tokens for x in ("kde", "plasma")) or "kde" in joined or "plasma" in joined:
        return "kde"
    if any(x in tokens for x in ("hyprland", "sway", "wlroots")):
        return "wlroots"
    if env.get("GNOME_SETUP_DISPLAY") or env.get("GNOME_DESKTOP_SESSION_ID"):
        return "gnome"
    return "unknown"
