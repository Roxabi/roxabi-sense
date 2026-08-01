"""Shared status / presence snapshot for CLI, MCP, and other surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from roxabi_sense.report.presence import (
    DEFAULT_IDLE_THRESHOLD_S,
    DEFAULT_OFFLINE_THRESHOLD_S,
    Presence,
    derive_presence,
    presence_from_store,
)
from roxabi_sense.store import STATUS_KINDS, Event, Store


@dataclass(frozen=True)
class StatusSnapshot:
    """Query product for `sense status` / future MCP `active_now`."""

    db_path: Path
    db_exists: bool
    events: int
    last_tick: str | None
    daemon_started: str | None
    machine: str | None
    presence: Presence
    last_event: Event | None
    latest_by_kind: dict[str, Event]

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "db": str(self.db_path),
            "db_exists": self.db_exists,
            "events": self.events,
            "last_tick": self.last_tick,
            "daemon_started": self.daemon_started,
            "machine": self.machine,
            "presence": self.presence.to_dict(),
        }
        if self.last_event is not None:
            body["last_event"] = {
                "ts": self.last_event.ts,
                "kind": self.last_event.kind,
                "payload": self.last_event.payload,
            }
        body["latest_by_kind"] = {
            kind: {"ts": ev.ts, "kind": ev.kind, "payload": ev.payload}
            for kind, ev in self.latest_by_kind.items()
        }
        return body


def status_snapshot(
    store: Store,
    *,
    offline_threshold_s: float = DEFAULT_OFFLINE_THRESHOLD_S,
    idle_threshold_s: float = DEFAULT_IDLE_THRESHOLD_S,
) -> StatusSnapshot:
    """Build status from an open store (single source for all surfaces)."""
    presence = presence_from_store(
        store,
        offline_threshold_s=offline_threshold_s,
        idle_threshold_s=idle_threshold_s,
    )
    return StatusSnapshot(
        db_path=store.path,
        db_exists=True,
        events=store.count(),
        last_tick=store.get_meta("last_tick"),
        daemon_started=store.get_meta("daemon_started"),
        machine=store.get_meta("machine"),
        presence=presence,
        last_event=store.last_event(),
        latest_by_kind=store.latest_by_kinds(STATUS_KINDS),
    )


def status_snapshot_missing(
    db_path: Path,
    *,
    offline_threshold_s: float = DEFAULT_OFFLINE_THRESHOLD_S,
    idle_threshold_s: float = DEFAULT_IDLE_THRESHOLD_S,
) -> StatusSnapshot:
    """Offline shape when the DB file is absent (daemon never started)."""
    presence = derive_presence(
        last_tick=None,
        idle_watch="n/a",
        offline_threshold_s=offline_threshold_s,
        idle_threshold_s=idle_threshold_s,
    )
    return StatusSnapshot(
        db_path=db_path,
        db_exists=False,
        events=0,
        last_tick=None,
        daemon_started=None,
        machine=None,
        presence=presence,
        last_event=None,
        latest_by_kind={},
    )


def load_status_snapshot(
    db_path: Path,
    *,
    offline_threshold_s: float = DEFAULT_OFFLINE_THRESHOLD_S,
    idle_threshold_s: float = DEFAULT_IDLE_THRESHOLD_S,
) -> StatusSnapshot:
    """Open store if present; otherwise missing-db snapshot."""
    if not db_path.is_file():
        return status_snapshot_missing(
            db_path,
            offline_threshold_s=offline_threshold_s,
            idle_threshold_s=idle_threshold_s,
        )
    with Store(db_path) as store:
        return status_snapshot(
            store,
            offline_threshold_s=offline_threshold_s,
            idle_threshold_s=idle_threshold_s,
        )
