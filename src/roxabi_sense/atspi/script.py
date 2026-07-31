"""Agent config helpers (worker lives in agent_worker.py for system python)."""

from __future__ import annotations

from typing import Literal

NameEventsMode = Literal["off", "throttled", "on"]


def name_mode_normalized(name_events: str) -> NameEventsMode:
    if name_events in {"off", "throttled", "on"}:
        return name_events  # type: ignore[return-value]
    return "throttled"


def agent_env(
    *,
    name_events: NameEventsMode = "throttled",
    name_throttle_s: float = 10.0,
    probe_min_s: float = 0.5,
    trace: bool = False,
) -> dict[str, str]:
    """Env vars consumed by agent_worker.py (system interpreter)."""
    mode = name_mode_normalized(name_events)
    return {
        "SENSE_ATSPI_NAME_MODE": mode,
        "SENSE_ATSPI_NAME_MS": str(max(0, int(float(name_throttle_s) * 1000))),
        "SENSE_ATSPI_PROBE_MS": str(max(50, int(float(probe_min_s) * 1000))),
        "SENSE_ATSPI_TRACE": "1" if trace else "0",
    }
