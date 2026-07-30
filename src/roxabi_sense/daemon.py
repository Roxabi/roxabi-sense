"""Daemon: AT-SPI focus event-driven + idle watch + polled collectors."""

from __future__ import annotations

import queue
import signal
import time
from typing import Any

from roxabi_sense.collectors.focus_atspi import FocusAtspiCollector
from roxabi_sense.collectors.focus_watch import FocusAtspiWatch
from roxabi_sense.collectors.idle_facts import append_idle_transition
from roxabi_sense.collectors.idle_watch import SOURCE as WAYLAND_IDLE_SOURCE
from roxabi_sense.collectors.idle_watch import IdleWatch
from roxabi_sense.config import SenseConfig
from roxabi_sense.daemon_collectors import (  # noqa: F401 — re-export for tests
    build_collectors,
    build_poll_collectors,
    tick_all,
    tick_one,
    want_logind_idle,
    want_wayland_idle,
)
from roxabi_sense.store import Store

_FOCUS_EVENT_MIN_INTERVAL_S = 0.25
_IDLE_RESPAWN_BASE_S = 2.0
_IDLE_RESPAWN_MAX_S = 60.0


def _utc_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def run_daemon(cfg: SenseConfig) -> int:
    store = Store(cfg.db_path)
    focus: FocusAtspiCollector | None = FocusAtspiCollector() if cfg.focus else None
    focus_on_poll = bool(focus is not None and not cfg.focus_events)
    stop = False
    focus_q: queue.Queue[str] = queue.Queue()
    idle_q: queue.Queue[dict[str, Any]] = queue.Queue()
    watch: FocusAtspiWatch | None = None
    idle_watch: IdleWatch | None = None
    last_focus_event_probe = 0.0
    wayland_healthy = False
    idle_respawn_at = 0.0
    idle_backoff = _IDLE_RESPAWN_BASE_S
    last_activity_ts: str | None = None

    def _stop(*_args: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    store.set_meta("daemon_started", _utc_stamp())
    store.set_meta("machine", cfg.machine)
    store.set_meta("idle_watch", "n/a")
    store.set_meta("last_tick", _utc_stamp())

    events_enabled = bool(focus is not None and cfg.focus_events)
    mode = "events+backup" if events_enabled else ("poll" if focus else "off")
    use_wl = want_wayland_idle(cfg)
    poll_collectors = build_poll_collectors(
        cfg, logind_idle=want_logind_idle(cfg, wayland_healthy=False)
    )

    print(
        f"sense daemon: db={cfg.db_path} poll={cfg.poll_seconds}s "
        f"focus={mode} backup={cfg.focus_backup_seconds}s "
        f"idle_backend={cfg.idle_backend} threshold={cfg.idle_threshold_s}s "
        f"poll_collectors={[c.name for c in poll_collectors]}",
        flush=True,
    )

    if events_enabled:

        def _on_focus_event(_msg: dict[str, Any]) -> None:
            focus_q.put("focus")

        watch = FocusAtspiWatch(on_event=_on_focus_event)
        try:
            watch.start()
        except Exception as exc:  # noqa: BLE001
            print(
                f"sense focus-watch failed to start: {exc} — focus on poll cadence",
                flush=True,
            )
            watch = None
            focus_on_poll = True
            events_enabled = False

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

    if use_wl:
        _start_idle_watch()

    now0 = time.monotonic()
    next_poll = now0 + cfg.poll_seconds
    next_focus_backup = now0 + cfg.focus_backup_seconds if events_enabled else float("inf")

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

        while not stop:
            now = time.monotonic()
            deadlines = [next_poll]
            if events_enabled and focus is not None:
                deadlines.append(next_focus_backup)
            if idle_respawn_at > 0:
                deadlines.append(idle_respawn_at)
            wait = max(0.05, min(deadlines) - now)

            try:
                focus_q.get(timeout=wait)
                while True:
                    try:
                        focus_q.get_nowait()
                    except queue.Empty:
                        break
                if focus is not None and events_enabled:
                    now = time.monotonic()
                    if now - last_focus_event_probe >= _FOCUS_EVENT_MIN_INTERVAL_S:
                        last_focus_event_probe = now
                        n = tick_one(focus, store, label="focus_atspi/event")
                        store.set_meta("last_tick", _utc_stamp())
                        last_activity_ts = _utc_stamp()
                        if n:
                            store.set_meta("last_focus_source", "event")
                            print(
                                f"sense focus-event: +{n} (total={store.count()})",
                                flush=True,
                            )
            except queue.Empty:
                pass

            while True:
                try:
                    msg = idle_q.get_nowait()
                except queue.Empty:
                    break
                _handle_idle_msg(
                    msg,
                    store=store,
                    cfg=cfg,
                    last_activity_ts=last_activity_ts,
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

            if events_enabled and focus is not None and now >= next_focus_backup:
                n = tick_one(focus, store, label="focus_atspi/backup")
                store.set_meta("last_tick", _utc_stamp())
                if n:
                    store.set_meta("last_focus_source", "backup")
                next_focus_backup = now + cfg.focus_backup_seconds

            if watch is not None and not watch.running:
                print("sense focus-watch: exited — poll cadence", flush=True)
                watch.stop()
                watch = None
                events_enabled = False
                focus_on_poll = focus is not None
                next_focus_backup = float("inf")

    finally:
        if watch is not None:
            watch.stop()
        if idle_watch is not None:
            idle_watch.stop()
        store.close()
        print("sense daemon: stopped", flush=True)
    return 0


def _handle_idle_msg(
    msg: dict[str, Any],
    *,
    store: Store,
    cfg: SenseConfig,
    last_activity_ts: str | None,
) -> None:
    typ = msg.get("type")
    if typ == "ready":
        store.set_meta("idle_watch", "ready")
        print("sense idle-watch: ready (logind demoted)", flush=True)
    elif typ == "error":
        store.set_meta("idle_watch", "dead")
        print(f"sense idle-watch error: {msg.get('error')}", flush=True)
    elif typ == "idle" and isinstance(msg.get("idle"), bool):
        idle_flag = bool(msg["idle"])
        ts = _utc_stamp()
        with store.batch():
            append_idle_transition(
                store,
                idle=idle_flag,
                source=WAYLAND_IDLE_SOURCE,
                threshold_s=cfg.idle_threshold_s,
                ts=ts,
                last_activity_ts=None if idle_flag else last_activity_ts,
            )
            store.set_meta("last_tick", ts)



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