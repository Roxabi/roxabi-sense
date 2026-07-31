"""Pure idle transition writer (shared by logind collector + Wayland watch)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from roxabi_sense.store import Store
from roxabi_sense.util.time import parse_ts, to_z, utc_now_z

KIND = "idle"


def compute_idle_since(
    *,
    enter_ts: str,
    threshold_s: float,
    last_activity_ts: str | None = None,
) -> str:
    """Last activity evidence when entering idle (not the notify fire time alone)."""
    if last_activity_ts:
        return last_activity_ts
    enter = parse_ts(enter_ts)
    since = enter - timedelta(seconds=threshold_s)
    return to_z(since)


def append_idle_transition(
    store: Store,
    *,
    idle: bool,
    source: str,
    threshold_s: float,
    ts: str | None = None,
    idle_since: str | None = None,
    last_activity_ts: str | None = None,
    extra: dict[str, Any] | None = None,
) -> int:
    """
    Write one idle transition fact.

    On enter (idle=True), always set idle_since from last activity or ts - threshold.
    """
    row_ts = ts or utc_now_z()
    payload: dict[str, Any] = {
        "idle": idle,
        "source": source,
        "threshold_s": threshold_s,
    }
    if idle:
        payload["idle_since"] = idle_since or compute_idle_since(
            enter_ts=row_ts,
            threshold_s=threshold_s,
            last_activity_ts=last_activity_ts,
        )
    if extra:
        for k, v in extra.items():
            if k not in payload:
                payload[k] = v
    return store.append(KIND, payload, ts=row_ts)
