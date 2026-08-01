"""AT-SPI agent lifecycle helpers for the daemon (keeps daemon.py under size gate)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from roxabi_sense.atspi import FocusAtspiAgent
from roxabi_sense.atspi.trace_log import AtspiTraceWriter, default_trace_path
from roxabi_sense.collectors.focus import FocusCollector
from roxabi_sense.config import SenseConfig
from roxabi_sense.store import Store


def start_atspi_agent(
    cfg: SenseConfig,
    *,
    on_message: Callable[[dict[str, Any]], None],
    store: Store,
) -> FocusAtspiAgent | None:
    agent = FocusAtspiAgent(
        on_message=on_message,
        name_events=cfg.focus_name_events,
        name_throttle_s=cfg.focus_name_throttle_s,
        probe_min_s=cfg.focus_event_min_interval_s,
        trace=bool(cfg.atspi_trace),
    )
    try:
        agent.start()
        store.set_meta("atspi_agent", "starting")
        store.set_meta("atspi_trace", "on" if cfg.atspi_trace else "off")
        print(
            f"sense atspi-agent: starting"
            f"{' (TRACE ' + str(cfg.atspi_trace_hours) + 'h)' if cfg.atspi_trace else ''}",
            flush=True,
        )
        return agent
    except Exception as exc:  # noqa: BLE001
        print(f"sense atspi-agent failed: {exc} — poll cadence", flush=True)
        store.set_meta("atspi_agent", "dead")
        return None


def make_trace_writer(cfg: SenseConfig) -> AtspiTraceWriter | None:
    if not cfg.atspi_trace:
        return None
    path = cfg.atspi_trace_path or default_trace_path()
    return AtspiTraceWriter(path=path, hours=float(cfg.atspi_trace_hours))


def apply_probe_result(
    focus: FocusCollector,
    store: Store,
    msg: dict[str, Any],
    *,
    path: str,
) -> int:
    mode_s = str(msg.get("mode") or "focus")
    if mode_s not in ("focus", "desktop", "full"):
        mode_s = "focus"
    wins = msg.get("windows") or []
    if not isinstance(wins, list):
        wins = []
    ms = msg.get("ms")
    probe_ms = int(ms) if isinstance(ms, int) else None
    with store.batch():
        return focus.apply(
            store,
            wins,  # type: ignore[arg-type]
            mode=mode_s,  # type: ignore[arg-type]
            probe_ms=probe_ms,
            source="atspi",
        )


def handle_atspi_msg(
    msg: dict[str, Any],
    *,
    focus: FocusCollector | None,
    store: Store,
    on_activity: Callable[[], None],
    trace: AtspiTraceWriter | None = None,
) -> None:
    typ = msg.get("type")
    if typ == "atspi_raw":
        if trace is not None:
            trace.write(msg)
        return
    if typ == "ready":
        store.set_meta("atspi_agent", "ready")
        if trace is not None:
            trace.write({"type": "agent_ready", "payload": msg})
        return
    if typ != "probe_result" or focus is None:
        return
    mode = str(msg.get("mode") or "focus")
    reason = msg.get("reason")
    if reason == "cmd" and mode == "desktop":
        path = "backup"
    elif reason in ("cmd", "once"):
        path = "cmd"
    else:
        path = "event"
    n = apply_probe_result(focus, store, msg, path=path)
    on_activity()
    if n:
        store.set_meta("last_focus_path", path)
        print(f"sense focus-{path}: +{n} (total={store.count()})", flush=True)
