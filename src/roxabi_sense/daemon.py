"""Daemon: long-lived AT-SPI agent + idle watch + polled collectors."""

from __future__ import annotations

import queue
import signal
import time
from typing import Any

from roxabi_sense.atspi import FocusAtspiAgent
from roxabi_sense.collectors.focus_atspi import FocusAtspiCollector
from roxabi_sense.collectors.idle_watch import IdleWatch
from roxabi_sense.config import SenseConfig
from roxabi_sense.daemon_atspi import handle_atspi_msg, start_atspi_agent
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
from roxabi_sense.store import Store

_IDLE_RESPAWN_BASE_S = 2.0
_IDLE_RESPAWN_MAX_S = 60.0
_ATSPI_RESPAWN_BASE_S = 2.0
_ATSPI_RESPAWN_MAX_S = 60.0


def run_daemon(cfg: SenseConfig) -> int:
    store = Store(cfg.db_path)
    focus: FocusAtspiCollector | None = FocusAtspiCollector() if cfg.focus else None
    focus_on_poll = bool(focus is not None and not cfg.focus_events)
    stop = False
    atspi_q: queue.Queue[dict[str, Any]] = queue.Queue()
    idle_q: queue.Queue[dict[str, Any]] = queue.Queue()
    agent: FocusAtspiAgent | None = None
    idle_watch: IdleWatch | None = None
    wayland_healthy = False
    idle_respawn_at = 0.0
    idle_backoff = _IDLE_RESPAWN_BASE_S
    atspi_respawn_at = 0.0
    atspi_backoff = _ATSPI_RESPAWN_BASE_S
    last_activity_ts: str | None = None
    events_enabled = bool(focus is not None and cfg.focus_events)

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

    mode = "events+desktop" if events_enabled else ("poll" if focus else "off")
    use_wl = want_wayland_idle(cfg)
    poll_collectors = build_poll_collectors(
        cfg, logind_idle=want_logind_idle(cfg, wayland_healthy=False)
    )
    print(
        f"sense daemon: db={cfg.db_path} poll={cfg.poll_seconds}s "
        f"focus={mode} desktop={cfg.focus_backup_seconds}s "
        f"name_events={cfg.focus_name_events}/{cfg.focus_name_throttle_s}s "
        f"atspi=long-lived idle_backend={cfg.idle_backend} "
        f"threshold={cfg.idle_threshold_s}s "
        f"poll_collectors={[c.name for c in poll_collectors]}",
        flush=True,
    )

    def _start_atspi() -> None:
        nonlocal agent, events_enabled, focus_on_poll
        if not cfg.focus_events or focus is None:
            return
        if agent is not None:
            agent.stop()
        agent = start_atspi_agent(cfg, on_message=atspi_q.put, store=store)
        if agent is None:
            events_enabled = False
            focus_on_poll = True
        else:
            events_enabled = True
            focus_on_poll = False

    def _start_idle_watch() -> None:
        nonlocal idle_watch, wayland_healthy, idle_backoff
        if not use_wl:
            store.set_meta("idle_watch", "n/a")
            return
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
            wayland_healthy = False
            idle_watch = None

    if events_enabled:
        _start_atspi()
    if use_wl:
        _start_idle_watch()

    now0 = time.monotonic()
    next_poll = now0 + cfg.poll_seconds
    next_desktop = now0 + cfg.focus_backup_seconds if events_enabled else float("inf")

    try:
        boot_cols = list(poll_collectors)
        if focus is not None:
            boot_cols.append(focus)
        wrote = tick_all(boot_cols, store)
        store.set_meta("last_tick", _utc_stamp())
        if wrote:
            print(
                f"sense tick (boot): +{wrote} events (total={store.count()})",
                flush=True,
            )

        def _on_activity() -> None:
            nonlocal last_activity_ts
            store.set_meta("last_tick", _utc_stamp())
            last_activity_ts = _utc_stamp()

        while not stop:
            now = time.monotonic()
            deadlines = [next_poll]
            if events_enabled and focus is not None:
                deadlines.append(next_desktop)
            if idle_respawn_at > 0:
                deadlines.append(idle_respawn_at)
            if atspi_respawn_at > 0:
                deadlines.append(atspi_respawn_at)
            wait = max(0.05, min(deadlines) - now)

            try:
                batch = [atspi_q.get(timeout=wait)]
                while True:
                    try:
                        batch.append(atspi_q.get_nowait())
                    except queue.Empty:
                        break
                for msg in batch:
                    handle_atspi_msg(
                        msg, focus=focus, store=store, on_activity=_on_activity
                    )
            except queue.Empty:
                pass

            while True:
                try:
                    msg = idle_q.get_nowait()
                except queue.Empty:
                    break
                handle_idle_msg(
                    msg, store=store, cfg=cfg, last_activity_ts=last_activity_ts
                )
                typ = msg.get("type")
                if typ == "ready":
                    wayland_healthy = True
                    idle_backoff = _IDLE_RESPAWN_BASE_S
                    poll_collectors = build_poll_collectors(
                        cfg,
                        logind_idle=want_logind_idle(cfg, wayland_healthy=True),
                    )
                elif typ == "error":
                    wayland_healthy = False
                elif typ == "idle" and msg.get("idle") is False:
                    last_activity_ts = _utc_stamp()

            if stop:
                break

            now = time.monotonic()
            if (
                use_wl
                and idle_watch is not None
                and not idle_watch.running
                and idle_respawn_at <= 0
            ):
                wayland_healthy = False
                store.set_meta("idle_watch", "dead")
                poll_collectors = build_poll_collectors(
                    cfg,
                    logind_idle=want_logind_idle(cfg, wayland_healthy=False),
                )
                idle_respawn_at = now + idle_backoff
                print(
                    f"sense idle-watch: exited — degrade; respawn in {idle_backoff:.0f}s",
                    flush=True,
                )
                idle_backoff = min(_IDLE_RESPAWN_MAX_S, idle_backoff * 2)

            if use_wl and idle_respawn_at > 0 and now >= idle_respawn_at:
                idle_respawn_at = 0.0
                _start_idle_watch()

            if (
                cfg.focus_events
                and focus is not None
                and agent is not None
                and not agent.running
                and atspi_respawn_at <= 0
            ):
                store.set_meta("atspi_agent", "dead")
                print(
                    f"sense atspi-agent: exited — poll + respawn in {atspi_backoff:.0f}s",
                    flush=True,
                )
                agent.stop()
                agent = None
                focus_on_poll = True
                events_enabled = False
                next_desktop = float("inf")
                atspi_respawn_at = now + atspi_backoff
                atspi_backoff = min(_ATSPI_RESPAWN_MAX_S, atspi_backoff * 2)

            if atspi_respawn_at > 0 and now >= atspi_respawn_at:
                atspi_respawn_at = 0.0
                events_enabled = True
                _start_atspi()
                if agent is not None and agent.running:
                    next_desktop = now + cfg.focus_backup_seconds
                    atspi_backoff = _ATSPI_RESPAWN_BASE_S

            if now >= next_poll:
                cols = list(poll_collectors)
                if focus is not None and focus_on_poll:
                    cols.append(focus)
                wrote = tick_all(cols, store)
                store.set_meta("last_tick", _utc_stamp())
                if focus is not None and focus_on_poll and wrote:
                    store.set_meta("last_focus_source", "poll")
                if wrote:
                    print(
                        f"sense tick (poll): +{wrote} (total={store.count()})",
                        flush=True,
                    )
                next_poll = now + cfg.poll_seconds

            if (
                events_enabled
                and focus is not None
                and agent is not None
                and now >= next_desktop
            ):
                agent.request_probe("desktop")
                next_desktop = now + cfg.focus_backup_seconds

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
