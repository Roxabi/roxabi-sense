"""Daemon: focus probes + AT-SPI agent + idle watch + polled collectors."""

from __future__ import annotations

import queue
import signal
from typing import Any

from roxabi_sense.atspi import FocusAtspiAgent
from roxabi_sense.collectors.focus import FocusCollector
from roxabi_sense.collectors.focus.runtime import FocusRuntime
from roxabi_sense.collectors.idle_watch import IdleWatch
from roxabi_sense.config import SenseConfig
from roxabi_sense.daemon_atspi import make_trace_writer, start_atspi_agent
from roxabi_sense.daemon_collectors import (  # noqa: F401 — re-export for tests
    _utc_stamp,
    build_collectors,
    build_poll_collectors,
    handle_idle_msg,
    tick_all,
    tick_one,
    want_logind_idle,
    want_wayland_idle,
)
from roxabi_sense.daemon_loop import run_main_loop
from roxabi_sense.store import Store


def run_daemon(cfg: SenseConfig) -> int:
    store = Store(cfg.db_path)
    focus: FocusCollector | None = FocusCollector() if cfg.focus else None
    focus_rt: FocusRuntime | None = FocusRuntime(focus) if focus is not None else None
    stop = False
    atspi_q: queue.Queue[dict[str, Any]] = queue.Queue()
    idle_q: queue.Queue[dict[str, Any]] = queue.Queue()
    agent: FocusAtspiAgent | None = None
    idle_watch: IdleWatch | None = None
    trace = make_trace_writer(cfg)
    if trace is not None:
        print(f"sense atspi-trace: {trace.path} ({cfg.atspi_trace_hours}h)", flush=True)

    def _stop(*_args: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    store.set_meta("daemon_started", _utc_stamp())
    store.set_meta("machine", cfg.machine)
    store.set_meta("idle_watch", "n/a")
    store.set_meta("atspi_agent", "n/a")
    store.set_meta("last_tick", _utc_stamp())

    if focus_rt is not None:
        focus_rt.select_initial(store)

    def _sync_focus_mode(ag: FocusAtspiAgent | None) -> tuple[bool, bool]:
        if focus is None or focus_rt is None:
            return False, False
        atspi_live = (
            focus_rt.active.source == "atspi"
            and cfg.focus_events
            and ag is not None
            and ag.running
        )
        return bool(atspi_live), not atspi_live

    def _start_atspi() -> FocusAtspiAgent | None:
        nonlocal agent
        if not cfg.focus_events or focus is None or focus_rt is None:
            return agent
        if "atspi" not in focus_rt.preferred:
            return agent
        if agent is not None:
            agent.stop()
        agent = start_atspi_agent(cfg, on_message=atspi_q.put, store=store)
        if agent is None:
            focus_rt.mark_atspi(store, healthy=False)
        else:
            focus_rt.mark_atspi(store, healthy=False)
        return agent

    def _start_idle_watch() -> IdleWatch | None:
        nonlocal idle_watch
        if not use_wl:
            store.set_meta("idle_watch", "n/a")
            return None
        if idle_watch is not None:
            idle_watch.stop()
        idle_watch = IdleWatch(
            on_event=lambda m: idle_q.put(m),
            threshold_s=cfg.idle_threshold_s,
        )
        try:
            idle_watch.start()
            store.set_meta("idle_watch", "restarting")
            print("sense idle-watch: starting", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"sense idle-watch failed to start: {exc}", flush=True)
            store.set_meta("idle_watch", "dead")
            idle_watch = None
        return idle_watch

    use_wl = want_wayland_idle(cfg)
    poll_collectors = build_poll_collectors(
        cfg, logind_idle=want_logind_idle(cfg, wayland_healthy=False)
    )
    mode = "events+desktop" if (focus and cfg.focus_events) else ("poll" if focus else "off")
    print(
        f"sense daemon: db={cfg.db_path} poll={cfg.poll_seconds}s "
        f"focus={mode} desktop={cfg.focus_backup_seconds}s "
        f"name={cfg.focus_name_events}/{cfg.focus_name_throttle_s}s "
        f"atspi=long-lived idle={cfg.idle_backend}/{cfg.idle_threshold_s}s "
        f"poll={[c.name for c in poll_collectors]}",
        flush=True,
    )

    if focus is not None and cfg.focus_events and focus_rt is not None:
        if "atspi" in focus_rt.preferred:
            _start_atspi()
    try:
        if use_wl:
            _start_idle_watch()
        run_main_loop(
            cfg=cfg,
            store=store,
            focus=focus,
            focus_rt=focus_rt,
            poll_collectors=poll_collectors,
            use_wl=use_wl,
            atspi_q=atspi_q,
            idle_q=idle_q,
            agent=agent,
            idle_watch=idle_watch,
            start_atspi=_start_atspi,
            start_idle_watch=_start_idle_watch,
            sync_focus_mode=_sync_focus_mode,
            stop_flag=lambda: stop,
            trace=trace,
        )
    finally:
        if agent is not None:
            agent.stop()
        if idle_watch is not None:
            idle_watch.stop()
        store.close()
        print("sense daemon: stopped", flush=True)
    return 0


def collect_once(cfg: SenseConfig) -> int:
    store = Store(cfg.db_path)
    collectors = build_collectors(cfg)
    try:
        n = tick_all(collectors, store)
        store.set_meta("last_tick", _utc_stamp())
        if store.get_meta("idle_watch") is None:
            store.set_meta("idle_watch", "n/a")
        return n
    finally:
        store.close()


__all__ = ["build_collectors", "build_poll_collectors", "collect_once", "run_daemon"]
