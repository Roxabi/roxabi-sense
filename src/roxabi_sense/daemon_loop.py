"""Main daemon loop helpers (size gate for daemon.py)."""

from __future__ import annotations

import queue
import time
from collections.abc import Callable
from typing import Any

from roxabi_sense.atspi import FocusAtspiAgent
from roxabi_sense.atspi.trace_log import AtspiTraceWriter
from roxabi_sense.collectors.focus import FocusCollector
from roxabi_sense.collectors.focus.runtime import FocusRuntime
from roxabi_sense.collectors.idle_watch import IdleWatch
from roxabi_sense.config import SenseConfig
from roxabi_sense.daemon_atspi import handle_atspi_msg
from roxabi_sense.daemon_collectors import (
    _utc_stamp,
    build_poll_collectors,
    handle_idle_msg,
    tick_all,
    want_logind_idle,
)
from roxabi_sense.store import Store

_IDLE_RESPAWN_BASE_S = 2.0
_IDLE_RESPAWN_MAX_S = 60.0
_ATSPI_RESPAWN_BASE_S = 2.0
_ATSPI_RESPAWN_MAX_S = 60.0


def run_main_loop(
    *,
    cfg: SenseConfig,
    store: Store,
    focus: FocusCollector | None,
    focus_rt: FocusRuntime | None,
    poll_collectors: list[Any],
    use_wl: bool,
    atspi_q: queue.Queue[dict[str, Any]],
    idle_q: queue.Queue[dict[str, Any]],
    agent: FocusAtspiAgent | None,
    idle_watch: IdleWatch | None,
    start_atspi: Callable[[], FocusAtspiAgent | None],
    start_idle_watch: Callable[[], IdleWatch | None],
    sync_focus_mode: Callable[[FocusAtspiAgent | None], tuple[bool, bool]],
    stop_flag: Callable[[], bool],
    trace: AtspiTraceWriter | None,
) -> None:
    events_enabled, focus_on_poll = sync_focus_mode(agent)
    idle_respawn_at = 0.0
    idle_backoff = _IDLE_RESPAWN_BASE_S
    atspi_respawn_at = 0.0
    atspi_backoff = _ATSPI_RESPAWN_BASE_S
    last_activity_ts: str | None = None
    now0 = time.monotonic()
    next_poll = now0 + cfg.poll_seconds
    next_desktop = now0 + cfg.focus_backup_seconds if events_enabled else float("inf")

    boot_cols = list(poll_collectors)
    if focus is not None:
        boot_cols.append(focus)
    wrote = tick_all(boot_cols, store)
    store.set_meta("last_tick", _utc_stamp())
    if wrote:
        print(f"sense tick (boot): +{wrote} events (total={store.count()})", flush=True)

    def _on_activity() -> None:
        nonlocal last_activity_ts
        store.set_meta("last_tick", _utc_stamp())
        last_activity_ts = _utc_stamp()

    while not stop_flag():
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
                if msg.get("type") == "ready" and focus_rt is not None:
                    focus_rt.mark_atspi(store, healthy=True)
                    events_enabled, focus_on_poll = sync_focus_mode(agent)
                    if events_enabled:
                        next_desktop = now + cfg.focus_backup_seconds
                        atspi_backoff = _ATSPI_RESPAWN_BASE_S
                handle_atspi_msg(
                    msg,
                    focus=focus,
                    store=store,
                    on_activity=_on_activity,
                    trace=trace,
                )
        except queue.Empty:
            pass

        while True:
            try:
                msg = idle_q.get_nowait()
            except queue.Empty:
                break
            typ = msg.get("type")
            logind_on = False
            if typ == "ready":
                idle_backoff = _IDLE_RESPAWN_BASE_S
                logind_on = want_logind_idle(cfg, wayland_healthy=True)
                poll_collectors[:] = build_poll_collectors(
                    cfg, logind_idle=logind_on
                )
            elif typ == "error":
                logind_on = want_logind_idle(cfg, wayland_healthy=False)
                poll_collectors[:] = build_poll_collectors(
                    cfg, logind_idle=logind_on
                )
            elif typ == "idle" and msg.get("idle") is False:
                last_activity_ts = _utc_stamp()
            handle_idle_msg(
                msg,
                store=store,
                cfg=cfg,
                last_activity_ts=last_activity_ts,
                logind_active=logind_on,
            )

        if stop_flag():
            break

        now = time.monotonic()
        if (
            use_wl
            and idle_watch is not None
            and not idle_watch.running
            and idle_respawn_at <= 0
        ):
            from roxabi_sense.collectors.idle_meta import write_idle_meta

            store.set_meta("idle_watch", "dead")
            logind_on = want_logind_idle(cfg, wayland_healthy=False)
            poll_collectors[:] = build_poll_collectors(cfg, logind_idle=logind_on)
            write_idle_meta(
                store, cfg, wayland_healthy=False, logind_active=logind_on
            )
            idle_respawn_at = now + idle_backoff
            print(
                f"sense idle-watch: exited — degrade; respawn in {idle_backoff:.0f}s",
                flush=True,
            )
            idle_backoff = min(_IDLE_RESPAWN_MAX_S, idle_backoff * 2)

        if use_wl and idle_respawn_at > 0 and now >= idle_respawn_at:
            idle_respawn_at = 0.0
            idle_watch = start_idle_watch()

        if (
            cfg.focus_events
            and focus is not None
            and focus_rt is not None
            and agent is not None
            and not agent.running
            and atspi_respawn_at <= 0
        ):
            store.set_meta("atspi_agent", "dead")
            print(
                f"sense atspi-agent: exited — demote + respawn in {atspi_backoff:.0f}s",
                flush=True,
            )
            agent.stop()
            agent = None
            focus_rt.mark_atspi(store, healthy=False)
            events_enabled, focus_on_poll = sync_focus_mode(agent)
            next_desktop = float("inf")
            atspi_respawn_at = now + atspi_backoff
            atspi_backoff = min(_ATSPI_RESPAWN_MAX_S, atspi_backoff * 2)

        if atspi_respawn_at > 0 and now >= atspi_respawn_at:
            atspi_respawn_at = 0.0
            agent = start_atspi()
            if agent is None or not agent.running:
                if focus_rt is not None:
                    focus_rt.mark_atspi(store, healthy=False)
                events_enabled, focus_on_poll = sync_focus_mode(agent)
                atspi_respawn_at = now + atspi_backoff
                atspi_backoff = min(_ATSPI_RESPAWN_MAX_S, atspi_backoff * 2)
            else:
                events_enabled, focus_on_poll = sync_focus_mode(agent)

        if now >= next_poll:
            cols = list(poll_collectors)
            if focus is not None and focus_on_poll:
                cols.append(focus)
            wrote = tick_all(cols, store)
            store.set_meta("last_tick", _utc_stamp())
            if focus is not None and focus_on_poll and wrote:
                store.set_meta("last_focus_path", "poll")
            if wrote:
                print(f"sense tick (poll): +{wrote} (total={store.count()})", flush=True)
            next_poll = now + cfg.poll_seconds

        if (
            events_enabled
            and focus is not None
            and agent is not None
            and now >= next_desktop
        ):
            agent.request_probe("desktop")
            next_desktop = now + cfg.focus_backup_seconds
