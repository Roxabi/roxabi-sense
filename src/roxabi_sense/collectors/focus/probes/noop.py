"""Noop focus probe — always selectable, never reports windows."""

from __future__ import annotations

from roxabi_sense.collectors.focus.protocol import FocusWindow


class NoopFocusProbe:
    source: str = "noop"

    def probe(self) -> bool:
        return True

    def get_active(self) -> list[FocusWindow]:
        return []

    def get_desktop(self) -> list[FocusWindow]:
        return []
