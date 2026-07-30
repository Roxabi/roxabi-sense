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
    agent_sessions: bool = True
    process_presence: bool = True
    idle: bool = True
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
        if "process_names" in collectors:
            cfg.process_names = tuple(str(x) for x in collectors["process_names"])
        if "machine" in nats:
            cfg.machine = str(nats["machine"])
    return cfg
