"""Transition-only Tern observations with durable, independently scoped proofs."""
from __future__ import annotations

import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from roxabi_sense.collectors.tern.metadata import opaque, selected_semantic, work_semantic
from roxabi_sense.store import Store
from roxabi_sense.util.time import to_z


def _clock() -> float:
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class ObservationWriter:
    """Publish semantic transitions and renew matching proofs in metadata only.

    The caller must hold the focus writer ownership lock throughout this writer's
    lifetime. Calling from a foreign once process would invalidate its proofs.
    """

    def __init__(self, *, freshness_s: int = 10) -> None:
        self.run_ref = secrets.token_urlsafe(18)
        self.freshness_s = freshness_s
        self.last_clock: float | None = None
        self.snapshot_id: int | None = None
        self.fingerprint: str | None = None
        self.scopes: dict[str, dict[str, Any]] = {}
        self.initialized = False

    def _initialize(self, store: Store) -> list[dict[str, str]]:
        if self.initialized:
            return []
        self.initialized = True
        try:
            meta = json.loads(store.get_meta("tern_observation") or "{}")
        except (TypeError, ValueError):
            return []
        latest = store.last_by_kind("tern_snapshot")
        if (not isinstance(meta, dict) or latest is None
                or meta.get("snapshot_id") != latest.id
                or meta.get("run_ref") != latest.payload.get("run_ref")
                or meta.get("healthy") is not True):
            return []
        old = meta.get("scopes")
        if not isinstance(old, dict):
            return []
        return [{"proof_ref": item["proof_ref"], "confirmed_through": item["last_observed_at"]}
                for item in old.values() if isinstance(item, dict)
                and isinstance(item.get("proof_ref"), str)
                and isinstance(item.get("last_observed_at"), str)]

    def publish(self, store: Store, windows: list[dict[str, Any]] | None) -> int:
        now = datetime.now(UTC)
        stamp = to_z(now)
        clock = _clock()
        closed = self._initialize(store)
        gap = self.last_clock is not None and clock - self.last_clock > self.freshness_s
        healthy = windows is not None
        semantics: dict[str, str] = {}
        freshness: dict[str, float] = {}
        if windows is not None:
            for window in windows:
                route = [window.get("endpoint_ref"), window["window_key"],
                         window["conn"], window["since"]]
                for pane in window["panes"]:
                    if pane.get("selected") and window.get("registered") is True:
                        scope = opaque("selected-scope", route)
                        semantics[scope] = opaque("selected", selected_semantic(pane))
                        freshness[scope] = max(0.0, min(
                            self.freshness_s,
                            float(window.get("_lease_seconds", self.freshness_s)) - 1,
                        ))
                    semantics[opaque("work-scope", pane["pane_ref"])] = opaque(
                        "work", work_semantic(pane)
                    )
        for scope, proof in self.scopes.items():
            if (gap or not healthy or semantics.get(scope) != proof["semantic_ref"]
                    or clock >= proof.get("expires_boottime", 0)):
                closed.append({"proof_ref": proof["proof_ref"],
                               "confirmed_through": proof["last_observed_at"]})
        next_scopes: dict[str, dict[str, Any]] = {}
        for scope, semantic in semantics.items():
            prior = self.scopes.get(scope)
            if (gap or prior is None or prior["semantic_ref"] != semantic
                    or clock >= prior.get("expires_boottime", 0)):
                prior = {"proof_ref": secrets.token_urlsafe(18), "semantic_ref": semantic,
                         "started_at": stamp}
            lifetime = freshness.get(scope, float(self.freshness_s))
            next_scopes[scope] = {**prior, "last_observed_at": stamp,
                                  "expires_boottime": clock + lifetime,
                                  "fresh_until": to_z(now + timedelta(seconds=lifetime))}
        clean_windows = [{k: v for k, v in window.items() if k != "_lease_seconds"}
                         for window in windows or []]
        fingerprint = _json({"healthy": healthy, "windows": clean_windows})
        published_windows = [
            {**window, "panes": [
                {**pane, "work_proof_ref": next_scopes[
                    opaque("work-scope", pane["pane_ref"])
                ]["proof_ref"]}
                for pane in window["panes"]
            ]}
            for window in clean_windows
        ]
        wrote = 0
        with store.batch():
            if fingerprint != self.fingerprint or closed or self.snapshot_id is None:
                self.snapshot_id = store.append("tern_snapshot", {
                    "version": 1, "run_ref": self.run_ref,
                    "state": "observed" if healthy else "unknown",
                    "windows": published_windows, "closed": closed,
                }, ts=stamp)
                self.fingerprint = fingerprint
                wrote = 1
            store.set_meta("tern_observation", _json({"run_ref": self.run_ref,
                "snapshot_id": self.snapshot_id, "healthy": healthy, "scopes": next_scopes}))
        self.scopes = next_scopes
        self.last_clock = clock
        return wrote
