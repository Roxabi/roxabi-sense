"""Read-only Tern source, publishing semantic facts and scoped health."""
from __future__ import annotations

import time
from typing import Any

from roxabi_sense.collectors.tern import runner
from roxabi_sense.collectors.tern.bridge import boot_identity, read_registrations
from roxabi_sense.collectors.tern.metadata import (
    clients,
    endpoint_ref,
    observation_shape,
    parse_window,
    work_semantic,
)
from roxabi_sense.collectors.tern.observation import ObservationWriter
from roxabi_sense.store import Store


class TernCollector:
    """Collect explicitly routed metadata while the focus writer lock is held."""

    name = "tern"

    def __init__(self) -> None:
        self.writer = ObservationWriter()
        self.endpoint = endpoint_ref()

    def tick(self, store: Store) -> int:
        try:
            windows = self._collect()
        except (runner.TernUnavailable, OSError, ValueError, KeyError, TypeError):
            windows = None
        return self.writer.publish(store, windows)

    def _collect(self) -> list[dict[str, Any]]:
        deadline = time.monotonic() + 8

        def budget() -> None:
            if time.monotonic() >= deadline:
                raise runner.TernUnavailable("observation deadline")

        def process(key: str, pane: int) -> dict[str, Any]:
            budget()
            return runner.process(key, pane)

        initial = runner.inspect("")
        routes = clients(initial)
        if len(routes) > 16 or len({r["window_key"] for r in routes}) != len(routes):
            raise runner.TernUnavailable("ambiguous window clients")
        routes.sort(key=lambda r: r["window_key"])
        windows = []
        for route in routes:
            budget()
            raw = runner.inspect(route["window_key"])
            if len(raw.get("panes", [])) > 128:
                raise runner.TernUnavailable("too many panes")
            window = parse_window(raw, route, self.endpoint, process)
            window["endpoint_ref"] = self.endpoint
            budget()
            verified = runner.inspect(route["window_key"])
            verified_routes = [
                c for c in clients(verified) if c["window_key"] == route["window_key"]
            ]
            if observation_shape(raw) != observation_shape(verified) or verified_routes != [route]:
                raise runner.TernUnavailable("pane observation changed")
            windows.append(window)
        seen: dict[str, dict[str, Any]] = {}
        for window in windows:
            for pane in window["panes"]:
                value = work_semantic(pane)
                previous = seen.setdefault(pane["pane_ref"], value)
                if previous != value:
                    raise runner.TernUnavailable("conflicting pane observations")
        budget()
        final_routes = clients(runner.inspect(""))
        final_routes.sort(key=lambda r: r["window_key"])
        if final_routes != routes:
            raise runner.TernUnavailable("window clients changed")
        registrations = read_registrations()
        _, observed_boot = boot_identity()
        route_keys = ("endpoint_ref", "window_key", "conn", "since")
        for window in windows:
            matches = [reg for reg in registrations
                       if all(reg.get(key) == window.get(key) for key in route_keys)
                       and reg["expires_boottime"] > observed_boot]
            window["registered"] = len(matches) == 1
            if len(matches) == 1:
                window["_lease_seconds"] = matches[0]["expires_boottime"] - observed_boot
        budget()
        return windows
