"""Daemon: AT-SPI focus event-driven + polled collectors for the rest."""

from __future__ import annotations

import queue
import signal
import time
from typing import Any

from roxabi_sense.collectors import (
    AgentSessionsCollector,
    FocusAtspiCollector,
    IdleCollector,
    MprisCollector,
    ProcessPresenceCollector,
    TmuxSessionsCollector,
)
from roxabi_sense.collectors.focus_watch import FocusAtspiWatch
from roxabi_sense.config import SenseConfig
from roxabi_sense.store import Store

# Min gap between focus probes triggered by AT-SPI events (storm control).
_FOCUS_EVENT_MIN_INTERVAL_S = 0.25


def build_poll_collectors(cfg: SenseConfig) -> list[Any]:
    """Collectors on the regular poll loop (optionally includes focus)."""
    collectors: list[Any] = []
    if cfg.agent_sessions:
        collectors.append(AgentSessionsCollector())
    if cfg.process_presence:
        collectors.append(ProcessPresenceCollector(cfg.process_names))
    if cfg.idle:
        collectors.append(IdleCollector())
    if cfg.mpris:
        collectors.append(MprisCollector())
    if cfg.tmux:
        collectors.append(TmuxSessionsCollector())
    return collectors


def build_collectors(cfg: SenseConfig) -> list[Any]:
    """All collectors including focus (for `sense once`)."""
    cols = build_poll_collectors(cfg)
    if cfg.focus:
        cols.append(FocusAtspiCollector())
    return cols


def tick_all(collectors: list[Any], store: Store) -> int:
    """Run one tick on each collector; never let one failure abort the rest."""
    wrote = 0
    with store.batch():
        for c in collectors:
            try:
                wrote += int(c.tick(store) or 0)
            except Exception as exc:  # noqa: BLE001 — isolate collectors
                print(f"sense collector error [{getattr(c, 'name', '?')}]: {exc}", flush=True)
    return wrote


def tick_one(collector: Any, store: Store, *, label: str) -> int:
    """Single collector tick under batch + isolation."""
    with store.batch():
        try:
            return int(collector.tick(store) or 0)
        except Exception as exc:  # noqa: BLE001
            print(f"sense collector error [{label}]: {exc}", flush=True)
            return 0


def _utc_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def run_daemon(cfg: SenseConfig) -> int:
    store = Store(cfg.db_path)
    poll_collectors = build_poll_collectors(cfg)
    focus: FocusAtspiCollector | None = FocusAtspiCollector() if cfg.focus else None
    # When events are off or watch dies, tick focus on the main poll cadence.
    focus_on_poll = bool(focus is not None and not cfg.focus_events)
    stop = False
    focus_q: queue.Queue[str] = queue.Queue()
    watch: FocusAtspiWatch | None = None
    last_focus_event_probe = 0.0

    def _stop(*_args: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    store.set_meta("daemon_started", _utc_stamp())
    store.set_meta("machine", cfg.machine)

    events_enabled = bool(focus is not None and cfg.focus_events)
    if events_enabled:
        mode = "events+backup"
    elif focus is not None:
        mode = "poll"
    else:
        mode = "off"

    print(
        f"sense daemon: db={cfg.db_path} poll={cfg.poll_seconds}s "
        f"focus={mode} backup={cfg.focus_backup_seconds}s "
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
            mode = "poll"

    now0 = time.monotonic()
    next_poll = now0 + cfg.poll_seconds  # avoid double-poll right after boot
    next_focus_backup = now0 + cfg.focus_backup_seconds if events_enabled else float("inf")

    try:
        # Initial full sample (focus + others)
        boot_cols = list(poll_collectors)
        if focus is not None:
            boot_cols.append(focus)
        wrote = tick_all(boot_cols, store)
        store.set_meta("last_tick", _utc_stamp())
        if wrote:
            print(f"sense tick (boot): +{wrote} events (total={store.count()})", flush=True)

        while not stop:
            now = time.monotonic()
            deadlines = [next_poll]
            if events_enabled and focus is not None:
                deadlines.append(next_focus_backup)
            wait = max(0.05, min(deadlines) - now)

            # Wake on focus event or timeout
            try:
                focus_q.get(timeout=wait)
                while True:
                    try:
                        focus_q.get_nowait()
                    except queue.Empty:
                        break
                if focus is not None and events_enabled:
                    now = time.monotonic()
                    if now - last_focus_event_probe < _FOCUS_EVENT_MIN_INTERVAL_S:
                        pass  # storm control — skip probe
                    else:
                        last_focus_event_probe = now
                        n = tick_one(focus, store, label="focus_atspi/event")
                        store.set_meta("last_tick", _utc_stamp())
                        if n:
                            store.set_meta("last_focus_source", "event")
                            print(
                                f"sense focus-event: +{n} events "
                                f"(total={store.count()})",
                                flush=True,
                            )
            except queue.Empty:
                pass

            if stop:
                break

            now = time.monotonic()
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
                        f"sense tick (poll): +{wrote} events (total={store.count()})",
                        flush=True,
                    )
                next_poll = now + cfg.poll_seconds

            if events_enabled and focus is not None and now >= next_focus_backup:
                n = tick_one(focus, store, label="focus_atspi/backup")
                store.set_meta("last_tick", _utc_stamp())
                if n:
                    store.set_meta("last_focus_source", "backup")
                    print(
                        f"sense focus-backup: +{n} events (total={store.count()})",
                        flush=True,
                    )
                next_focus_backup = now + cfg.focus_backup_seconds

            # Watch died → reaping + focus on poll cadence
            if watch is not None and not watch.running:
                print(
                    "sense focus-watch: exited — focus switches to poll cadence",
                    flush=True,
                )
                watch.stop()
                watch = None
                events_enabled = False
                focus_on_poll = focus is not None
                next_focus_backup = float("inf")

    finally:
        if watch is not None:
            watch.stop()
        store.close()
        print("sense daemon: stopped", flush=True)
    return 0


def collect_once(cfg: SenseConfig) -> int:
    """Single tick (CLI once / status --collect). Isolates collector failures."""
    store = Store(cfg.db_path)
    collectors = build_collectors(cfg)
    try:
        n = tick_all(collectors, store)
        store.set_meta("last_tick", _utc_stamp())
        return n
    finally:
        store.close()
