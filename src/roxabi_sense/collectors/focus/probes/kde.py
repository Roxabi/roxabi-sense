"""KDE/KWin FocusProbe — stub until stable D-Bus path is wired (issue #44).

probe() is always False so selection falls through to atspi/x11/noop.
"""

from __future__ import annotations

from roxabi_sense.collectors.focus.protocol import FocusWindow


class KdeFocusProbe:
    """Placeholder backend (source=kde). Not yet implemented."""

    source: str = "kde"
    reason: str = "kwin dbus active-window not wired yet"

    def probe(self) -> bool:
        return False

    def get_active(self) -> list[FocusWindow]:
        return []

    def get_desktop(self) -> list[FocusWindow]:
        return []
