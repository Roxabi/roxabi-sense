"""Transition-only focus coverage and source-health observation metadata."""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from roxabi_sense.collectors.focus.probes.cosmic import CosmicFocusProbe
from roxabi_sense.collectors.focus.protocol import FocusWindow
from roxabi_sense.store import Store
from roxabi_sense.util.time import to_z, utc_now_z


class CoverageWriter:
    def __init__(self) -> None:
        self.current: tuple[str, str | None, str] | None = None
        self.event_id: int | None = None
        self.last_observed: str | None = None
        self.observed_start: str | None = None
        self.initialized = False

    def initialize(self, store: Store) -> int:
        if self.initialized:
            return 0
        self.initialized = True
        prior = store.get_meta("focus_observation")
        try:
            parsed = json.loads(prior) if prior else {}
            meta: dict[str, Any] = parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            meta = {}
        latest = store.last_by_kind("focus_coverage")
        if latest is None or latest.payload.get("state") != "observed":
            return 0
        legacy = store.last_by_kind("focus_legacy")
        if legacy is not None and legacy.id > latest.id:
            return 0
        matches = (
            meta.get("coverage_id") == latest.id
            and meta.get("source") == latest.payload.get("source")
            and meta.get("window_id") == latest.payload.get("window_id")
        )
        end = meta.get("last_observed_at") if matches else None
        store.append(
            "focus_coverage",
            {"state": "unknown", "source": latest.payload.get("source"), "reason": "restart"},
            ts=max(end, latest.ts) if isinstance(end, str) else latest.ts,
        )
        store.set_meta("focus_observation", json.dumps({"healthy": False, "reason": "restart"}))
        return 1

    def legacy(self, store: Store, source: str, windows: list[FocusWindow]) -> int:
        """Mark a post-native source as legacy, without certifying its history."""
        if sum(w.active for w in windows) != 1:
            return 0
        latest = store.last_by_kind("focus_coverage")
        if latest is None:
            return 0
        legacy = store.last_by_kind("focus_legacy")
        if legacy is not None and legacy.id > latest.id and legacy.payload.get("source") == source:
            return 0
        wrote = 0
        if latest.payload.get("state") == "observed" and (
            legacy is None or legacy.id < latest.id
        ):
            try:
                observation = json.loads(store.get_meta("focus_observation") or "{}")
                end = (
                    observation.get("last_observed_at")
                    if isinstance(observation, dict)
                    and observation.get("coverage_id") == latest.id
                    and observation.get("source") == latest.payload.get("source")
                    and observation.get("window_id") == latest.payload.get("window_id")
                    else None
                )
            except (TypeError, ValueError):
                end = None
            store.append(
                "focus_coverage",
                {"state": "unknown", "source": latest.payload.get("source"),
                 "reason": "source_changed"},
                ts=max(end, latest.ts) if isinstance(end, str) else latest.ts,
            )
            wrote += 1
        store.append("focus_legacy", {"source": source, "reason": "source_changed"})
        store.set_meta("focus_observation", json.dumps({
            "healthy": False, "source": source, "reason": "legacy_unverified",
        }))
        self.current = None
        return wrote + 1

    def update(
        self, store: Store, probe: CosmicFocusProbe, windows: list[FocusWindow]
    ) -> tuple[int, bool]:
        active = [w for w in windows if w.active]
        observed = probe.reason == "observed" and len(active) == 1 and probe.observed_at is not None
        window_id = active[0].window_id if observed else None
        reason = "observed" if observed else (
            "pending_health" if probe.reason == "observed" else probe.reason
        )
        state = "observed" if observed else "unknown"
        key = (state, window_id, reason)
        changed = key != self.current
        wrote = 0
        if changed:
            ts = utc_now_z()
            if self.current is not None and self.current[0] == "observed" and not observed:
                ts = max(self.last_observed or "", self.observed_start or ts)
            payload: dict[str, Any] = {"state": state, "source": probe.source, "reason": reason}
            if window_id:
                payload["window_id"] = window_id
            self.event_id = store.append("focus_coverage", payload, ts=ts)
            self.current = key
            self.observed_start = ts if observed else None
            self.last_observed = None
            wrote = 1
        if observed and probe.observed_at is not None:
            self.last_observed = to_z(probe.observed_at)
            store.set_meta("focus_observation", json.dumps({
                "coverage_id": self.event_id,
                "source": probe.source,
                "window_id": window_id,
                "last_observed_at": self.last_observed,
                "fresh_until": to_z(probe.observed_at + timedelta(seconds=8)),
                "healthy": True,
            }, separators=(",", ":")))
        elif not observed:
            store.set_meta("focus_observation", json.dumps({
                "coverage_id": self.event_id, "source": probe.source,
                "reason": reason, "healthy": False,
            }, separators=(",", ":")))
        return wrote, changed
