"""Collector protocol."""

from __future__ import annotations

from typing import Protocol

from roxabi_sense.store import Store


class Collector(Protocol):
    name: str

    def tick(self, store: Store) -> int:
        """Run one collect cycle. Return number of events written."""
        ...
