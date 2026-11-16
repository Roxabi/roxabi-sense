"""Explicit export, registration and removal of the native messaging host."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from importlib.resources import files
from pathlib import Path

from roxabi_sense.collectors.meeting_browser.install_files import (
    EXTENSION_ID,
    create_owned,
    files_for,
    launcher,
    manifest,
    read_owned,
    safe_directory,
)
from roxabi_sense.config import SenseConfig
from roxabi_sense.store import Store
from roxabi_sense.store.meeting_protocol import MAX_SOURCES, validate_sources

_META = "meeting_browser_sources"
_BINDINGS = "meeting_browser_bindings"


def export_extension(destination: Path) -> Path:
    """Export into a new, empty directory without touching existing files."""
    destination = Path(os.path.abspath(destination.expanduser()))
    safe_directory(destination.parent)
    destination.mkdir(mode=0o700)
    try:
        root = files("roxabi_sense.collectors.meeting_browser").joinpath("extension")
        for item in root.iterdir():
            if not item.is_file() or item.name.startswith("."):
                raise ValueError("unexpected extension resource")
            create_owned(destination / item.name, item.read_bytes(), 0o600)
    except Exception:
        shutil.rmtree(destination)
        raise
    return destination


def _registry(store: Store) -> dict:
    raw = store.get_meta(_META)
    if raw is None:
        latest = store.last_by_kind("meeting_sources")
        if latest is not None:
            raise ValueError("meeting source registry metadata is missing")
        return {"v": 1, "scope": "chrome_google_meet", "sources": []}
    try:
        value = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid meeting source registry") from exc
    if not validate_sources(value):
        raise ValueError("invalid meeting source registry")
    return value


def _bindings(store: Store, registry: dict) -> dict[str, str]:
    raw = store.get_meta(_BINDINGS)
    try:
        bindings = {} if raw is None else json.loads(raw)
        if (not isinstance(bindings, dict) or set(bindings) != set(registry["sources"])
                or any(not isinstance(value, str) or not EXTENSION_ID.fullmatch(value)
                       for value in bindings.values())):
            raise ValueError("invalid meeting source bindings")
        return bindings
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid meeting source bindings") from exc


def _save(store: Store, registry: dict) -> None:
    store.set_meta(_META, json.dumps(registry, separators=(",", ":")))
    store.append("meeting_sources", registry)


def install(cfg: SenseConfig, *, profile: Path, extension_id: str,
            config_path: Path, binary: str | None = None) -> str:
    if not EXTENSION_ID.fullmatch(extension_id):
        raise ValueError("extension ID must be 32 lowercase letters a–p")
    executable = binary or shutil.which("sense")
    if not executable:
        raise ValueError("sense executable not found in PATH")
    executable = os.path.abspath(executable)
    config_path = Path(os.path.abspath(config_path.expanduser()))
    db = Path(os.path.abspath(cfg.db_path.expanduser()))
    profile = safe_directory(profile)
    registration, script, owner = files_for(profile)
    source = str(uuid.uuid4())
    script_bytes = launcher(executable, config_path, db, source, extension_id, profile)
    manifest_bytes = manifest(script, extension_id)
    owner_data = {"v": 1, "source_id": source, "extension_id": extension_id,
                  "binary": executable, "config": str(config_path), "db": str(db)}
    created: list[Path] = []
    try:
        with Store(db) as store, store.batch():
            registry = _registry(store)
            bindings = _bindings(store, registry)
            if len(registry["sources"]) >= MAX_SOURCES:
                raise ValueError("meeting source limit reached")
            for path, content, mode in (
                (script, script_bytes, 0o700),
                (owner, (json.dumps(owner_data) + "\n").encode(), 0o600),
                (registration, manifest_bytes, 0o600),
            ):
                create_owned(path, content, mode)
                created.append(path)
            registry["sources"].append(source)
            bindings[source] = extension_id
            store.set_meta(_BINDINGS, json.dumps(bindings, separators=(",", ":")))
            _save(store, registry)
    except Exception:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    return source


def validate_installation(cfg: SenseConfig, *, profile: Path, extension_id: str) -> dict:
    if not EXTENSION_ID.fullmatch(extension_id):
        raise ValueError("invalid extension ID")
    profile = safe_directory(profile)
    registration, script, owner = files_for(profile)
    try:
        data = json.loads(read_owned(owner))
        if (set(data) != {"v", "source_id", "extension_id", "binary", "config", "db"}
                or data["v"] != 1 or data["extension_id"] != extension_id
                or data["db"] != os.path.abspath(cfg.db_path.expanduser())):
            raise ValueError("registration does not match selected extension or store")
        if (read_owned(registration) != manifest(script, extension_id)
                or read_owned(script) != launcher(data["binary"], Path(data["config"]),
                                                  Path(data["db"]), data["source_id"],
                                                  extension_id, profile)):
            raise ValueError("registration was modified; refusing removal")
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid registration ownership record") from exc
    return data


def remove(cfg: SenseConfig, *, profile: Path, extension_id: str) -> None:
    data = validate_installation(cfg, profile=profile, extension_id=extension_id)
    registration, script, owner = files_for(profile)
    with Store(cfg.db_path) as store, store.batch():
        registry = _registry(store)
        bindings = _bindings(store, registry)
        if bindings.get(data["source_id"]) != extension_id:
            raise ValueError("source binding does not match extension")
        if data["source_id"] not in registry["sources"]:
            raise ValueError("source missing from registry")
        registry["sources"].remove(data["source_id"])
        del bindings[data["source_id"]]
        store.set_meta(_BINDINGS, json.dumps(bindings, separators=(",", ":")))
        _save(store, registry)
    for path in (registration, script, owner):
        path.unlink()
