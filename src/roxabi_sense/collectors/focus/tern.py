"""Resolve Tern context only against a complete, healthy native inventory."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from roxabi_sense.collectors.focus.probes.cosmic import CosmicFocusProbe
from roxabi_sense.collectors.focus.protocol import FocusWindow
from roxabi_sense.collectors.tern.bridge import boot_identity, read_registrations
from roxabi_sense.collectors.tern.context import resolve_context
from roxabi_sense.store import Store


def tern_context(
    store: Store, probe: object, source: str, inventory: list[FocusWindow]
) -> dict[str, Any] | None:
    if source != "cosmic" or not isinstance(probe, CosmicFocusProbe):
        return None
    wire = probe.wire
    if (probe.reason != "observed" or wire is None or not wire.ready or wire.dirty
            or len(inventory) != len(wire.handles)):
        return None
    active = [window for window in inventory if window.active]
    if len(active) != 1 or active[0].app != "so.stencil.tern":
        return None
    snapshot = store.last_by_kind("tern_snapshot")
    if snapshot is None:
        return None
    try:
        observation = json.loads(store.get_meta("tern_observation") or "{}")
        if not isinstance(observation, dict) or observation.get("snapshot_id") != snapshot.id:
            return None
        boot_id, boottime = boot_identity()
        context, _ = resolve_context(active[0], inventory, snapshot.payload, observation,
                                     read_registrations(), datetime.now(UTC), boottime, boot_id)
        return context
    except (OSError, ValueError, TypeError, KeyError):
        return None
