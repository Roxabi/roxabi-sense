"""XDG paths for config and store."""

from __future__ import annotations

import os
from pathlib import Path


def xdg_data_home() -> Path:
    raw = os.environ.get("XDG_DATA_HOME")
    if raw:
        return Path(raw)
    return Path.home() / ".local" / "share"


def xdg_config_home() -> Path:
    raw = os.environ.get("XDG_CONFIG_HOME")
    if raw:
        return Path(raw)
    return Path.home() / ".config"


def default_db_path() -> Path:
    override = os.environ.get("SENSE_DB")
    if override:
        return Path(override)
    return xdg_data_home() / "roxabi-sense" / "sense.db"


def default_config_path() -> Path:
    override = os.environ.get("SENSE_CONFIG")
    if override:
        return Path(override)
    return xdg_config_home() / "roxabi-sense" / "config.toml"
