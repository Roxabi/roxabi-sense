"""Load sense config (TOML + env)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from roxabi_sense.paths import default_config_path, default_db_path

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


@dataclass
class SenseConfig:
    poll_seconds: float = 5.0
    db_path: Path = field(default_factory=default_db_path)
    # Focus: AT-SPI EventListener (primary) + slow backup poll
    focus_events: bool = True
    focus_backup_seconds: float = 30.0
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


def load_config(path: Path | None = None) -> SenseConfig:
    cfg = SenseConfig()
    config_path = path or default_config_path()
    if config_path.is_file():
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        daemon = data.get("daemon") or {}
        collectors = data.get("collectors") or {}
        nats = data.get("nats") or {}
        if "poll_seconds" in daemon:
            cfg.poll_seconds = float(daemon["poll_seconds"])
        if "db_path" in daemon:
            cfg.db_path = Path(str(daemon["db_path"]))
        if "focus_events" in daemon:
            cfg.focus_events = bool(daemon["focus_events"])
        if "focus_backup_seconds" in daemon:
            cfg.focus_backup_seconds = float(daemon["focus_backup_seconds"])
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
            cfg.process_names = tuple(str(x) for x in collectors["process_names"])
        if "machine" in nats:
            cfg.machine = str(nats["machine"])
    # Env overrides (tests / install)
    import os

    if os.environ.get("SENSE_DB"):
        cfg.db_path = Path(os.environ["SENSE_DB"])
    return cfg
