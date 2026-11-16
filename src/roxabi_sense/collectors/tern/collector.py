"""Read-only Tern source, publishing semantic facts and scoped health."""
from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Any

from roxabi_sense.collectors.tern import runner
from roxabi_sense.collectors.tern.bridge import boot_identity, read_registrations
from roxabi_sense.collectors.tern.metadata import (
    clients,
    endpoint_ref,
    observation_shape,
    parse_window,
)
from roxabi_sense.collectors.tern.observation import ObservationWriter
from roxabi_sense.store import Store


def _stamp() -> tuple[str, float]:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z"), time.clock_gettime(
        time.CLOCK_BOOTTIME
    )


def _sample(start: tuple[str, float], end: tuple[str, float]) -> dict[str, Any]:
    return {"start_wall": start[0], "start_boot": start[1],
            "end_wall": end[0], "end_boot": end[1]}


def _merge_samples(*samples: dict[str, Any]) -> dict[str, Any]:
    first = min(samples, key=lambda sample: sample["start_boot"])
    last = max(samples, key=lambda sample: sample["end_boot"])
    return {"start_wall": first["start_wall"], "start_boot": first["start_boot"],
            "end_wall": last["end_wall"], "end_boot": last["end_boot"]}


class TernCollector:
    """Acquire bounded metadata independently; publish only on the owning thread."""

    name = "tern"

    def __init__(self) -> None:
        self.writer = ObservationWriter()
        self.endpoint = endpoint_ref()
        self._route_hints: list[dict[str, Any]] = []

    def tick(self, store: Store) -> int:
        return self.publish(store, self.collect(threading.Event()))

    def collect(self, cancel: threading.Event) -> list[dict[str, Any]] | None:
        try:
            return self._collect(cancel)
        except (runner.TernUnavailable, OSError, ValueError, KeyError, TypeError):
            return None

    def publish(self, store: Store, windows: list[dict[str, Any]] | None) -> int:
        return self.writer.publish(store, windows)

    def expire(self, store: Store) -> int:
        return self.writer.expire(store)

    def shutdown(self, store: Store) -> int:
        return self.writer.shutdown(store)

    def _collect(self, cancel: threading.Event | None = None) -> list[dict[str, Any]]:
        cancel = cancel or threading.Event()
        deadline = time.clock_gettime(time.CLOCK_BOOTTIME) + 8

        def budget() -> None:
            if cancel.is_set() or time.clock_gettime(time.CLOCK_BOOTTIME) >= deadline:
                raise runner.TernUnavailable("observation deadline")

        def inspect(key: str | None) -> dict[str, Any]:
            budget()
            return runner.inspect(key, deadline=deadline, cancel=cancel)

        registrations = read_registrations()
        hints = [reg for reg in registrations if reg.get("endpoint_ref") == self.endpoint]
        hints.extend(self._route_hints)
        initial: dict[str, Any] | None = None
        bootstrap_key: str | None = None
        for hint in hints:
            key = hint["window_key"]
            try:
                candidate = inspect(key)
                route = [r for r in clients(candidate) if r["window_key"] == key]
            except runner.TernUnavailable:
                continue
            if len(route) == 1 and all(
                route[0][field] == hint[field] for field in ("conn", "since")
            ):
                initial, bootstrap_key = candidate, key
                break
        if initial is None:
            # The unscoped combined response is inventory discovery only.
            discovery = inspect(None)
            initial = {"clients": discovery.get("clients")}
        routes = clients(initial)
        if len(routes) > 16 or len({r["window_key"] for r in routes}) != len(routes):
            raise runner.TernUnavailable("ambiguous window clients")
        routes.sort(key=lambda r: r["window_key"])
        self._route_hints = list(routes)
        if bootstrap_key is None and routes:
            bootstrap_key = routes[0]["window_key"]

        def unavailable(_key: str, _pane: int) -> dict[str, Any]:
            raise runner.TernUnavailable("process sampled in final phase")

        windows: list[dict[str, Any]] = []
        for route in routes:
            started = _stamp()
            raw = inspect(route["window_key"])
            if len(raw.get("panes", [])) > 128:
                raise runner.TernUnavailable("too many panes")
            window = parse_window(raw, route, self.endpoint, unavailable, include_pane_id=True)
            window["endpoint_ref"] = self.endpoint
            verified = inspect(route["window_key"])
            verified_routes = [
                c for c in clients(verified) if c["window_key"] == route["window_key"]
            ]
            if observation_shape(raw) != observation_shape(verified) or verified_routes != [route]:
                raise runner.TernUnavailable("pane observation changed")
            structural = _sample(started, _stamp())
            for pane in window["panes"]:
                pane["_work_sample"] = structural
                if pane["selected"]:
                    pane["_selected_sample"] = structural
            windows.append(window)

        # Re-sample exact foreground processes after structural acquisition, never
        # treating a structural recheck as evidence about the process itself.
        for window in windows:
            for pane in window["panes"]:
                pane_id = pane.pop("_pane_id")
                if not pane["selected"] and pane["pane_working"] is not True:
                    continue
                started = _stamp()
                try:
                    budget()
                    result = runner.process(
                        window["window_key"], pane_id, deadline=deadline, cancel=cancel
                    )
                    ended = _stamp()
                    fg = result.get("foreground")
                    if (type(result.get("pane")) is not int or result["pane"] != pane_id
                            or not isinstance(fg, dict)
                            or not isinstance(fg.get("name"), str)):
                        continue
                    pane["omp_foreground"] = fg["name"] == "omp"
                    if pane["pane_working"] is not False:
                        pane["omp_working"] = (
                            pane["pane_working"] if pane["omp_foreground"] else None
                        )
                    sample = _sample(started, ended)
                    if pane["selected"]:
                        pane["_selected_sample"] = _merge_samples(
                            pane["_selected_sample"], sample
                        )
                    if pane["pane_working"] is not False:
                        pane["_work_sample"] = _merge_samples(pane["_work_sample"], sample)
                except runner.TernUnavailable:
                    continue
        budget()
        if bootstrap_key is not None:
            final_routes = sorted(clients(inspect(bootstrap_key)), key=lambda r: r["window_key"])
            if final_routes != routes:
                raise runner.TernUnavailable("window clients changed")

        # Shared pane views describe one work scope; conflicting work samples
        # become unknown without discarding independently valid selected context.
        shared: dict[str, list[dict[str, Any]]] = {}
        for window in windows:
            for pane in window["panes"]:
                shared.setdefault(pane["pane_ref"], []).append(pane)
        for panes in shared.values():
            if len({pane["repo_ref"] for pane in panes}) != 1:
                raise runner.TernUnavailable("conflicting pane identity")
            working = {pane["pane_working"] for pane in panes}
            omp_working = {pane["omp_working"] for pane in panes}
            sample = _merge_samples(*(pane["_work_sample"] for pane in panes))
            for pane in panes:
                pane["pane_working"] = next(iter(working)) if len(working) == 1 else None
                pane["omp_working"] = next(iter(omp_working)) if len(omp_working) == 1 else None
                pane["_work_sample"] = sample

        registrations = read_registrations()
        _, observed_boot = boot_identity()
        route_keys = ("endpoint_ref", "window_key", "conn", "since")
        for window in windows:
            matches = [reg for reg in registrations
                       if all(reg.get(key) == window.get(key) for key in route_keys)
                       and reg["expires_boottime"] > observed_boot]
            window["registered"] = len(matches) == 1
            if len(matches) == 1:
                window["_registration_expires_boot"] = matches[0]["expires_boottime"]
        budget()
        return windows
