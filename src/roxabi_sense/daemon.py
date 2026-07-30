"""Foreground collector loop."""

from __future__ import annotations

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
from roxabi_sense.config import SenseConfig
from roxabi_sense.store import Store


def build_collectors(cfg: SenseConfig) -> list[Any]:
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
    if cfg.focus:
        collectors.append(FocusAtspiCollector())
    return collectors


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
    collectors = build_collectors(cfg)
    stop = False

    def _stop(*_args: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    store.set_meta("daemon_started", _utc_stamp())
    store.set_meta("machine", cfg.machine)
    print(
        f"sense daemon: db={cfg.db_path} poll={cfg.poll_seconds}s "
        f"collectors={[c.name for c in collectors]}",
        flush=True,
    )

    while not stop:
        wrote = tick_all(collectors, store)
        store.set_meta("last_tick", _utc_stamp())
        if wrote:
            print(f"sense tick: +{wrote} events (total={store.count()})", flush=True)
        deadline = time.monotonic() + cfg.poll_seconds
        while not stop and time.monotonic() < deadline:
            time.sleep(0.2)

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
