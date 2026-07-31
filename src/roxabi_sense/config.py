"""Load sense config (TOML + env)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from roxabi_sense.paths import default_config_path, default_db_path


class ConfigError(Exception):
    """Invalid or unreadable config (CLI maps to exit 2)."""


DEFAULT_PROCESS_NAMES = (
    "discord",
    "slack",
    "chrome",
    "opera",
    "spotify",
    "ghostty",
    "tmux",
    "grok",
    "claude",
)

NameEventsMode = Literal["off", "throttled", "on"]


@dataclass
class SenseConfig:
    poll_seconds: float = 5.0
    db_path: Path = field(default_factory=default_db_path)
    # Focus: AT-SPI EventListener (primary) + rare full desktop backup
    focus_events: bool = True
    # Full desktop_snapshot interval (seconds). Events use active-only probe.
    focus_backup_seconds: float = 180.0
    # accessible-name: off | throttled | on (Chrome tab churn)
    focus_name_events: NameEventsMode = "throttled"
    focus_name_throttle_s: float = 10.0
    # Min interval between event-driven focus probes
    focus_event_min_interval_s: float = 0.5
    # Empirical AT-SPI JSONL (event source + multi-ACTIVE) — not used by recap
    atspi_trace: bool = False
    atspi_trace_hours: float = 48.0
    atspi_trace_path: Path | None = None
    offline_threshold_s: float = 120.0
    agent_sessions: bool = True
    process_presence: bool = True
    idle: bool = True
    # auto | wayland | logind | off — ADR-002 single writer
    idle_backend: str = "auto"
    idle_threshold_s: float = 300.0
    mpris: bool = True
    tmux: bool = True
    focus: bool = True
    process_names: tuple[str, ...] = DEFAULT_PROCESS_NAMES
    machine: str = "laptop"


def _parse_name_events(raw: object) -> NameEventsMode:
    s = str(raw).strip().lower()
    if s in {"off", "throttled", "on"}:
        return s  # type: ignore[return-value]
    if s in {"false", "0", "no"}:
        return "off"
    if s in {"true", "1", "yes"}:
        return "on"
    return "throttled"


def load_config(path: Path | None = None) -> SenseConfig:
    cfg = SenseConfig()
    config_path = path or default_config_path()
    if config_path.is_file():
        try:
            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
            _apply_toml(cfg, data)
        except ConfigError:
            raise
        except (OSError, UnicodeError, tomllib.TOMLDecodeError, TypeError, ValueError) as exc:
            raise ConfigError(f"invalid config {config_path}: {exc}") from exc
    import os

    if os.environ.get("SENSE_DB"):
        cfg.db_path = Path(os.environ["SENSE_DB"])
    return cfg


def _apply_toml(cfg: SenseConfig, data: dict) -> None:
    if not isinstance(data, dict):
        raise ConfigError("config root must be a table")
    daemon = data.get("daemon") or {}
    collectors = data.get("collectors") or {}
    nats = data.get("nats") or {}
    if not all(isinstance(x, dict) for x in (daemon, collectors, nats)):
        raise ConfigError("daemon/collectors/nats must be tables")
    if "poll_seconds" in daemon:
        cfg.poll_seconds = float(daemon["poll_seconds"])
    if "db_path" in daemon:
        cfg.db_path = Path(str(daemon["db_path"]))
    if "focus_events" in daemon:
        cfg.focus_events = bool(daemon["focus_events"])
    if "focus_backup_seconds" in daemon:
        cfg.focus_backup_seconds = float(daemon["focus_backup_seconds"])
    if "focus_name_events" in daemon:
        cfg.focus_name_events = _parse_name_events(daemon["focus_name_events"])
    if "focus_name_throttle_s" in daemon:
        cfg.focus_name_throttle_s = float(daemon["focus_name_throttle_s"])
    if "focus_event_min_interval_s" in daemon:
        cfg.focus_event_min_interval_s = float(daemon["focus_event_min_interval_s"])
    if "atspi_trace" in daemon:
        cfg.atspi_trace = bool(daemon["atspi_trace"])
    if "atspi_trace_hours" in daemon:
        cfg.atspi_trace_hours = float(daemon["atspi_trace_hours"])
    if "atspi_trace_path" in daemon:
        cfg.atspi_trace_path = Path(str(daemon["atspi_trace_path"]))
    if "offline_threshold_s" in daemon:
        cfg.offline_threshold_s = float(daemon["offline_threshold_s"])
    for key in (
        "agent_sessions",
        "process_presence",
        "idle",
        "mpris",
        "tmux",
        "focus",
    ):
        if key in collectors:
            setattr(cfg, key, bool(collectors[key]))
    if "idle_backend" in collectors:
        cfg.idle_backend = str(collectors["idle_backend"]).lower()
    if "idle_threshold_s" in collectors:
        cfg.idle_threshold_s = float(collectors["idle_threshold_s"])
    if "process_names" in collectors:
        raw_names = collectors["process_names"]
        if not isinstance(raw_names, list | tuple):
            raise ConfigError("collectors.process_names must be an array")
        cfg.process_names = tuple(str(x) for x in raw_names)
    if "machine" in nats:
        cfg.machine = str(nats["machine"])
