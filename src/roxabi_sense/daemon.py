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


def build_poll_collectors(cfg: SenseConfig) -> list[Any]:
    """Collectors that stay on the poll loop (not focus)."""
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


def _utc_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def run_daemon(cfg: SenseConfig) -> int:
    store = Store(cfg.db_path)
    poll_collectors = build_poll_collectors(cfg)
    focus: FocusAtspiCollector | None = FocusAtspiCollector() if cfg.focus else None
    stop = False
    focus_q: queue.Queue[str] = queue.Queue()
    watch: FocusAtspiWatch | None = None

    def _stop(*_args: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    store.set_meta("daemon_started", _utc_stamp())
    store.set_meta("machine", cfg.machine)
    mode = "events+backup" if (focus and cfg.focus_events) else "poll"
    print(
        f"sense daemon: db={cfg.db_path} poll={cfg.poll_seconds}s "
        f"focus={mode} backup={cfg.focus_backup_seconds}s "
        f"poll_collectors={[c.name for c in poll_collectors]}",
        flush=True,
    )

    if focus is not None and cfg.focus_events:
        def _on_focus_event(_msg: dict[str, Any]) -> None:
            focus_q.put("focus")

        watch = FocusAtspiWatch(on_event=_on_focus_event)
        try:
            watch.start()
        except Exception as exc:  # noqa: BLE001
            print(f"sense focus-watch failed to start: {exc} — focus stays poll-only", flush=True)
            watch = None

    next_poll = time.monotonic()
    next_focus_backup = time.monotonic() + cfg.focus_backup_seconds

    try:
        # Initial full sample (focus + others)
        wrote = tick_all(
            [*poll_collectors, *([focus] if focus is not None else [])],
            store,
        )
        store.set_meta("last_tick", _utc_stamp())
        if wrote:
            print(f"sense tick (boot): +{wrote} events (total={store.count()})", flush=True)

        while not stop:
            now = time.monotonic()
            wait = max(0.05, min(next_poll, next_focus_backup) - now)

            # Wake on focus event or timeout
            try:
                focus_q.get(timeout=wait)
                # Drain burst; one tick is enough (collector dedups)
                while True:
                    try:
                        focus_q.get_nowait()
                    except queue.Empty:
                        break
                if focus is not None:
                    n = 0
                    try:
                        n = int(focus.tick(store) or 0)
                    except Exception as exc:  # noqa: BLE001
                        print(f"sense collector error [focus_atspi/event]: {exc}", flush=True)
                    store.set_meta("last_tick", _utc_stamp())
                    store.set_meta("last_focus_source", "event")
                    if n:
                        print(
                            f"sense focus-event: +{n} events (total={store.count()})",
                            flush=True,
                        )
            except queue.Empty:
                pass

            if stop:
                break

            now = time.monotonic()
            if now >= next_poll:
                wrote = tick_all(poll_collectors, store)
                store.set_meta("last_tick", _utc_stamp())
                if wrote:
                    print(f"sense tick (poll): +{wrote} events (total={store.count()})", flush=True)
                next_poll = now + cfg.poll_seconds

            if focus is not None and now >= next_focus_backup:
                # Safety net if AT-SPI events miss a transition
                n = 0
                try:
                    n = int(focus.tick(store) or 0)
                except Exception as exc:  # noqa: BLE001
                    print(f"sense collector error [focus_atspi/backup]: {exc}", flush=True)
                store.set_meta("last_tick", _utc_stamp())
                if n:
                    store.set_meta("last_focus_source", "backup")
                    print(
                        f"sense focus-backup: +{n} events (total={store.count()})",
                        flush=True,
                    )
                next_focus_backup = now + cfg.focus_backup_seconds

            # If event watch died, fall back to poll cadence for focus
            if focus is not None and watch is not None and not watch.running:
                print("sense focus-watch: exited — focus uses backup poll only", flush=True)
                watch = None
                next_focus_backup = now  # ASAP

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
