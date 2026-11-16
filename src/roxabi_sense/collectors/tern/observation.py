"""Transition-only Tern observations with durable, independently scoped proofs."""
from __future__ import annotations

import json
import math
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from roxabi_sense.collectors.tern.metadata import opaque, pane_work_semantic, selected_semantic
from roxabi_sense.store import Store
from roxabi_sense.util.time import parse_ts, to_z


def _clock() -> float:
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sample_stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")



def _close(proof: dict[str, Any], now: datetime, clock: float) -> dict[str, str]:
    """Seal confirmation and the distinct last possible live-eligibility boundary."""
    result = {"proof_ref": proof["proof_ref"], "started_at": proof["started_at"],
              "confirmed_through": proof["last_observed_at"]}
    try:
        fresh = parse_ts(proof["fresh_until"])
        expiry = float(proof["expires_boottime"])
        if fresh.tzinfo is not None and math.isfinite(expiry):
            elapsed = max(0.0, clock - expiry)
            result["eligible_until"] = _sample_stamp(min(fresh, now - timedelta(seconds=elapsed)))
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    return result

def _public(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _public(item) for key, item in value.items()
                if not isinstance(key, str) or not key.startswith("_")}
    if isinstance(value, list):
        return [_public(item) for item in value]
    return value

_MISSING = object()


def _sample(
    value: Any, now: datetime, clock: float,
) -> tuple[datetime, float, datetime, float] | None:
    if value is _MISSING:
        return now, clock, now, clock
    try:
        start = parse_ts(value["start_wall"])
        end = parse_ts(value["end_wall"])
        start_boot = float(value["start_boot"])
        end_boot = float(value["end_boot"])
        if (start.tzinfo is None or end.tzinfo is None
                or not math.isfinite(start_boot) or not math.isfinite(end_boot)
                or start_boot < 0 or start_boot > end_boot or end_boot > clock
                or start > end or end > now):
            return None
        return start, start_boot, end, end_boot
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return None


class ObservationWriter:
    """Publish semantic transitions and renew matching proofs in metadata only.

    The caller must hold the focus writer ownership lock throughout this writer's
    lifetime. Calling from a foreign once process would invalidate its proofs.
    """

    def __init__(self, *, freshness_s: int = 10) -> None:
        self.run_ref = secrets.token_urlsafe(18)
        self.freshness_s = freshness_s
        self.snapshot_id: int | None = None
        self.fingerprint: str | None = None
        self.scopes: dict[str, dict[str, Any]] = {}
        self.watermarks: dict[str, float] = {}
        self.public_windows: list[dict[str, Any]] = []
        self.healthy = False
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
        return [{"proof_ref": item["proof_ref"], "started_at": item["started_at"],
                 "confirmed_through": item["last_observed_at"]}
                for item in old.values() if isinstance(item, dict)
                and isinstance(item.get("proof_ref"), str)
                and isinstance(item.get("started_at"), str)
                and isinstance(item.get("last_observed_at"), str)]

    def publish(self, store: Store, windows: list[dict[str, Any]] | None) -> int:
        now = datetime.now(UTC)
        clock = _clock()
        closed = self._initialize(store)
        healthy = windows is not None
        candidates: dict[str, tuple[str, Any, float | None]] = {}
        if windows is not None:
            for window in windows:
                route = [window.get("endpoint_ref"), window["window_key"],
                         window["conn"], window["since"]]
                for pane in window["panes"]:
                    candidates[opaque("pane-work-scope", pane["pane_ref"])] = (
                        opaque("pane-work", pane_work_semantic(pane)),
                        pane.get("_pane_work_sample", _MISSING), None,
                    )
                    sample = pane.get("_process_sample", _MISSING)
                    if pane.get("omp_foreground") is not None and sample is not None:
                        candidates[opaque("process-scope", pane["pane_ref"])] = (
                            opaque("process", pane["pane_ref"], pane["omp_foreground"]),
                            sample, None,
                        )
                    if pane.get("selected") and window.get("registered") is True:
                        scope = opaque("selected-scope", route)
                        expiry = window.get("_registration_expires_boot")
                        if expiry is None and "_lease_seconds" in window:
                            expiry = clock + max(0.0, float(window["_lease_seconds"]) - 1)
                        candidates[scope] = (
                            opaque("selected", selected_semantic(pane)),
                            pane.get("_selected_sample", _MISSING), expiry,
                        )

        # Expiry fences the in-flight acquisition at the time it is detected.
        for scope, proof in self.scopes.items():
            if clock >= proof["expires_boottime"]:
                self.watermarks[scope] = max(clock, self.watermarks.get(scope, 0.0))

        accepted: dict[str, tuple[str, datetime, float, float, datetime]] = {}
        for scope, (semantic, raw_sample, registration_expiry) in candidates.items():
            sample = _sample(raw_sample, now, clock)
            if sample is None:
                continue
            start, start_boot, end, _ = sample
            expiry = start_boot + self.freshness_s
            if registration_expiry is not None:
                try:
                    registration_expiry = float(registration_expiry)
                    if not math.isfinite(registration_expiry):
                        continue
                    expiry = min(expiry, registration_expiry)
                except (TypeError, ValueError, OverflowError):
                    continue
            if start_boot < self.watermarks.get(scope, 0.0) or expiry <= clock:
                continue
            accepted[scope] = (semantic, start, start_boot, expiry, end)

        next_scopes: dict[str, dict[str, Any]] = {}
        for scope, proof in self.scopes.items():
            candidate = accepted.get(scope)
            if (not healthy or candidate is None
                    or candidate[0] != proof["semantic_ref"]
                    or clock >= proof["expires_boottime"]):
                if not healthy or candidate is None:
                    self.watermarks[scope] = max(clock, self.watermarks.get(scope, 0.0))
                closed.append(_close(proof, now, clock))
            else:
                next_scopes[scope] = proof

        for scope, (semantic, start, start_boot, expiry, end) in accepted.items():
            prior = next_scopes.get(scope)
            if prior is None:
                prior = {"proof_ref": secrets.token_urlsafe(18), "semantic_ref": semantic,
                         "started_at": _sample_stamp(end), "last_observed_at": _sample_stamp(end)}
            else:
                # A request's lower bound can confirm history, never backdate it.
                confirmed = parse_ts(prior["last_observed_at"])
                if start > confirmed:
                    prior = {**prior, "last_observed_at": _sample_stamp(start)}
            next_scopes[scope] = {
                **prior, "expires_boottime": expiry,
                "fresh_until": _sample_stamp(start + timedelta(seconds=expiry - start_boot)),
            }

        clean_windows = _public(windows or [])
        published_windows = []
        for window in clean_windows:
            panes = []
            for pane in window["panes"]:
                refs = {}
                for kind in ("pane-work", "process"):
                    proof = next_scopes.get(opaque(f"{kind}-scope", pane["pane_ref"]))
                    if proof:
                        refs[f"{kind.replace('-', '_')}_proof_ref"] = proof["proof_ref"]
                panes.append({**pane, **refs})
            published_windows.append({**window, "panes": panes})
        fingerprint = _json({"healthy": healthy, "windows": published_windows})
        wrote = 0
        with store.batch():
            if fingerprint != self.fingerprint or closed or self.snapshot_id is None:
                published_at = datetime.now(UTC)
                self.snapshot_id = store.append("tern_snapshot", {
                    "version": 1, "run_ref": self.run_ref,
                    "state": "observed" if healthy else "unknown",
                    "windows": published_windows, "closed": closed,
                    "_published_at": _sample_stamp(published_at),
                }, ts=to_z(published_at))
                self.fingerprint = fingerprint
                wrote = 1
            store.set_meta("tern_observation", _json({"run_ref": self.run_ref,
                "snapshot_id": self.snapshot_id, "healthy": healthy, "scopes": next_scopes}))
        self.scopes = next_scopes
        self.public_windows = published_windows
        self.healthy = healthy
        return wrote

    def expire(self, store: Store) -> int:
        """Seal expired scopes once, without publishing healthy heartbeats."""
        now = datetime.now(UTC)
        clock = _clock()
        expired = {scope for scope, proof in self.scopes.items()
                   if clock >= proof["expires_boottime"]}
        if not expired:
            return 0
        for scope in expired:
            self.watermarks[scope] = max(clock, self.watermarks.get(scope, 0.0))
        closed = [_close(self.scopes[scope], now, clock) for scope in sorted(expired)]
        remaining = {scope: proof for scope, proof in self.scopes.items() if scope not in expired}
        windows = []
        for window in self.public_windows:
            panes = []
            for pane in window["panes"]:
                for kind in ("pane-work", "process"):
                    if opaque(f"{kind}-scope", pane["pane_ref"]) in expired:
                        key = f"{kind.replace('-', '_')}_proof_ref"
                        pane = {name: value for name, value in pane.items() if name != key}
                panes.append(pane)
            windows.append({**window, "panes": panes})
        with store.batch():
            published_at = datetime.now(UTC)
            snapshot_id = store.append("tern_snapshot", {
                "version": 1, "run_ref": self.run_ref,
                "state": "observed" if self.healthy else "unknown",
                "windows": windows, "closed": closed,
                "_published_at": _sample_stamp(published_at),
            }, ts=to_z(published_at))
            store.set_meta("tern_observation", _json({"run_ref": self.run_ref,
                "snapshot_id": snapshot_id, "healthy": self.healthy, "scopes": remaining}))
        self.snapshot_id = snapshot_id
        self.scopes = remaining
        self.public_windows = windows
        self.fingerprint = _json({"healthy": self.healthy, "windows": windows})
        return 1

    def shutdown(self, store: Store) -> int:
        """Seal remaining proofs at their last genuine confirmation."""
        return self.publish(store, None)
