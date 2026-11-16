"""Explicit export, registration and removal of the native messaging host."""

from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import uuid
from importlib.resources import files
from pathlib import Path

from roxabi_sense.collectors.meeting_browser.install_files import (
    EXTENSION_ID,
    HOST_NAME,
    create_owned,
    files_for,
    launcher,
    manifest,
    read_owned,
    safe_directory,
)
from roxabi_sense.collectors.meeting_browser.install_intent import (
    cleanup_install as _cleanup_install,
)
from roxabi_sense.collectors.meeting_browser.install_intent import installs as _installs
from roxabi_sense.collectors.meeting_browser.install_intent import serialized as _serialized
from roxabi_sense.collectors.meeting_browser.install_intent import store_installs as _store_installs
from roxabi_sense.config import SenseConfig
from roxabi_sense.store import Store
from roxabi_sense.store.meeting_protocol import CONFLICTS_META, MAX_SOURCES, validate_sources

_META = "meeting_browser_sources"
_BINDINGS = "meeting_browser_bindings"
_REMOVALS = "meeting_browser_removals"

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


def _removals(store: Store) -> dict:
    try:
        value = json.loads(store.get_meta(_REMOVALS) or "{}")
        if not isinstance(value, dict):
            raise ValueError("invalid removal records")
        return value
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid removal records") from exc


def _conflicts(store: Store) -> dict:
    try:
        raw = store.get_meta(CONFLICTS_META)
        value = (json.loads(raw) if raw is not None else
                 {"v": 1, "scope": "chrome_google_meet", "sources": []})
        if not validate_sources(value):
            raise ValueError("invalid meeting source conflicts")
        return value
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid meeting source conflicts") from exc


def _executable(binary: str | None) -> str:
    candidate = binary if binary is not None else sys.argv[0]
    if not os.path.isabs(candidate):
        raise ValueError("invoke sense by absolute path or supply an absolute executable")
    candidate = os.path.realpath(candidate)
    if not os.path.isfile(candidate) or not os.access(candidate, os.X_OK):
        raise ValueError("sense executable is not an executable file")
    return candidate


@_serialized
def install(cfg: SenseConfig, *, profile: Path, extension_id: str,
            config_path: Path, binary: str | None = None, data_dir: bool = False) -> str:
    if not EXTENSION_ID.fullmatch(extension_id):
        raise ValueError("extension ID must be 32 lowercase letters a–p")
    executable = _executable(binary)
    config_path = Path(os.path.abspath(config_path.expanduser()))
    db = Path(os.path.abspath(cfg.db_path.expanduser()))
    profile = safe_directory(profile)
    registration, script, owner = files_for(profile, data_dir=data_dir)
    source = str(uuid.uuid4())
    script_bytes = launcher(executable, config_path, db, source, extension_id, profile,
                            data_dir=data_dir)
    manifest_bytes = manifest(script, extension_id)
    owner_data = {"v": 1, "source_id": source, "extension_id": extension_id,
                  "binary": executable, "config": str(config_path), "db": str(db),
                  "directory": str(profile), "data_dir": data_dir}
    paths = (registration, script, owner)
    contents = (manifest_bytes, script_bytes, (json.dumps(owner_data) + "\n").encode())
    stages = [registration.parent / f".{HOST_NAME}.{uuid.uuid4().hex}" for _ in paths]
    key = str(registration)
    with Store(db) as store:
        with store.batch(immediate=True):
            registry = _registry(store)
            _bindings(store, registry)
            if key in _removals(store):
                raise ValueError("previous removal must be completed before reinstalling")
            records = _installs(store)
            if key in records:
                raise ValueError("previous installation must be removed before reinstalling")
            _conflicts(store)
            if len(registry["sources"]) >= MAX_SOURCES:
                raise ValueError("meeting source limit reached")
            for path in (*paths, *stages):
                try:
                    path.lstat()
                except FileNotFoundError:
                    continue
                raise FileExistsError(f"installation target exists: {path}")
            record = {"source": source, "extension": extension_id,
                      "files": [base64.b64encode(value).decode("ascii") for value in contents],
                      "stages": [path.name for path in stages], "inodes": [None] * 3}
            records[key] = record
            _store_installs(store, records)

        for index, (path, stage, content, mode) in enumerate(
                zip(paths, stages, contents, (0o600, 0o700, 0o600), strict=True)):
            fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
            try:
                info = os.fstat(fd)
                with store.batch(immediate=True):
                    records = _installs(store)
                    record = records[key]
                    record["inodes"][index] = [info.st_dev, info.st_ino]
                    _store_installs(store, records)
                with os.fdopen(fd, "wb") as stream:
                    fd = -1
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(stage, path, follow_symlinks=False)
                stage.unlink()
            finally:
                if fd != -1:
                    os.close(fd)
        with store.batch(immediate=True):
            registry = _registry(store)
            bindings = _bindings(store, registry)
            _conflicts(store)
            records = _installs(store)
            if records.get(key) != record:
                raise ValueError("installation intent changed")
            if len(registry["sources"]) >= MAX_SOURCES:
                raise ValueError("meeting source limit reached")
            registry["sources"].append(source)
            bindings[source] = extension_id
            store.set_meta(_BINDINGS, json.dumps(bindings, separators=(",", ":")))
            _save(store, registry)
            records = _installs(store)
            del records[key]
            _store_installs(store, records)
    return source


def validate_installation(cfg: SenseConfig, *, profile: Path, extension_id: str,
                          data_dir: bool = False) -> dict:
    if not EXTENSION_ID.fullmatch(extension_id):
        raise ValueError("invalid extension ID")
    registration, script, owner = files_for(profile, data_dir=data_dir,
                                             allow_missing_profile=True)
    try:
        data = json.loads(read_owned(owner))
        if (set(data) != {"v", "source_id", "extension_id", "binary", "config", "db",
                          "directory", "data_dir"}
                or data["v"] != 1 or data["extension_id"] != extension_id
                or type(data["data_dir"]) is not bool
                or data["db"] != os.path.abspath(cfg.db_path.expanduser())
                or files_for(Path(data["directory"]), data_dir=data["data_dir"],
                             allow_missing_profile=True)[0] != registration):
            raise ValueError("registration does not match selected extension or store")
        if (read_owned(registration) != manifest(script, extension_id)
                or read_owned(script) != launcher(data["binary"], Path(data["config"]),
                                                  Path(data["db"]), data["source_id"],
                                                  extension_id, Path(data["directory"]),
                                                  data_dir=data["data_dir"])):
            raise ValueError("registration was modified; refusing removal")
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid registration ownership record") from exc
    return data


@_serialized
def remove(cfg: SenseConfig, *, profile: Path, extension_id: str, data_dir: bool = False) -> None:
    if not EXTENSION_ID.fullmatch(extension_id):
        raise ValueError("invalid extension ID")
    profile = safe_directory(profile)
    paths = files_for(profile, data_dir=data_dir)
    key = str(paths[0])
    with Store(cfg.db_path) as store:
        with store.batch(immediate=True):
            installs = _installs(store)
            if key in installs:
                _cleanup_install(installs[key], paths, extension_id)
                del installs[key]
                _store_installs(store, installs)
                return
            removals = _removals(store)
            record = removals.get(key)
            if record is None:
                data = validate_installation(cfg, profile=profile, extension_id=extension_id,
                                             data_dir=data_dir)
                registry = _registry(store)
                bindings = _bindings(store, registry)
                _conflicts(store)
                source = data["source_id"]
                if bindings.get(source) != extension_id or source not in registry["sources"]:
                    raise ValueError("source binding does not match extension")
                record = {"source": source, "extension": extension_id,
                          "files": [base64.b64encode(read_owned(path)).decode("ascii")
                                    for path in paths]}
                removals[key] = record
                store.set_meta(_REMOVALS, json.dumps(removals, separators=(",", ":")))
                registry["sources"].remove(source)
                del bindings[source]
                store.set_meta(_BINDINGS, json.dumps(bindings, separators=(",", ":")))
                _save(store, registry)

        with store.batch(immediate=True):
            removals = _removals(store)
            record = removals.get(key)
            try:
                if (not isinstance(record, dict) or set(record) != {"source", "extension", "files"}
                        or record["extension"] != extension_id
                        or not isinstance(record["files"], list) or len(record["files"]) != 3):
                    raise ValueError("invalid removal record")
                expected = [base64.b64decode(item, validate=True) for item in record["files"]]
                source = record["source"]
                conflicts = _conflicts(store)
                if not validate_sources({"v": 1, "scope": "chrome_google_meet",
                                         "sources": [source]}):
                    raise ValueError("invalid removal record")
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid removal record or conflicts") from exc
            # Inspect every survivor before removing any; absent files are owned cleanup progress.
            survivors = []
            for path, content in zip(paths, expected, strict=True):
                try:
                    actual = read_owned(path)
                except FileNotFoundError:
                    continue
                if actual != content:
                    raise ValueError("registration was modified; refusing removal")
                survivors.append(path)
            for path in survivors:
                path.unlink()
            if source in conflicts["sources"]:
                conflicts["sources"].remove(source)
                store.set_meta(CONFLICTS_META, json.dumps(conflicts, separators=(",", ":")))
            del removals[key]
            store.set_meta(_REMOVALS, json.dumps(removals, separators=(",", ":")))
