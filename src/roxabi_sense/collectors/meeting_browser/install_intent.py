"""Durable installation intents and serialized filesystem recovery."""

import base64
import fcntl
import functools
import json
import os
from pathlib import Path

from roxabi_sense.collectors.meeting_browser.install_files import HOST_NAME, files_for, read_owned
from roxabi_sense.store import Store
from roxabi_sense.store.meeting_protocol import validate_sources

_INSTALLS = "meeting_browser_installs"


def serialized(function):
    """Serialize lifecycle operations for one native-host directory."""
    @functools.wraps(function)
    def wrapped(cfg, *, profile, extension_id, **kwargs):
        paths = files_for(profile, data_dir=kwargs.get("data_dir", False))
        fd = os.open(paths[0].parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            return function(cfg, profile=profile, extension_id=extension_id, **kwargs)
        finally:
            os.close(fd)
    return wrapped


def installs(store: Store) -> dict:
    try:
        value = json.loads(store.get_meta(_INSTALLS) or "{}")
        if not isinstance(value, dict):
            raise ValueError("invalid installation records")
        return value
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid installation records") from exc


def store_installs(store: Store, records: dict) -> None:
    store.set_meta(_INSTALLS, json.dumps(records, separators=(",", ":")))


def _install_record(record: object, paths: tuple[Path, Path, Path], extension: str):
    try:
        if (not isinstance(record, dict)
                or set(record) != {"source", "extension", "files", "stages", "inodes"}
                or record["extension"] != extension
                or not validate_sources({"v": 1, "scope": "chrome_google_meet",
                                         "sources": [record["source"]]})
                or any(not isinstance(record[k], list) or len(record[k]) != 3
                       for k in ("files", "stages", "inodes"))):
            raise ValueError
        contents = [base64.b64decode(value, validate=True) for value in record["files"]]
        stages = []
        for name in record["stages"]:
            if (not isinstance(name, str) or not name.startswith(f".{HOST_NAME}.")
                    or len(name) != len(f".{HOST_NAME}.") + 32
                    or any(c not in "0123456789abcdef" for c in name[-32:])):
                raise ValueError
            stages.append(paths[0].parent / name)
        for inode in record["inodes"]:
            if inode is not None and (not isinstance(inode, list) or len(inode) != 2
                                      or any(type(n) is not int or n < 0 for n in inode)):
                raise ValueError
        return contents, stages
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid installation record") from exc


def cleanup_install(record: dict, paths: tuple[Path, Path, Path], extension: str) -> None:
    contents, stages = _install_record(record, paths, extension)
    survivors = []
    for index, (path, stage, expected) in enumerate(zip(paths, stages, contents, strict=True)):
        inode = record["inodes"][index]
        pair = []
        for candidate, is_stage in ((path, False), (stage, True)):
            try:
                actual = read_owned(candidate, max_links=2)
                info = candidate.lstat()
            except FileNotFoundError:
                continue
            if ((inode is not None and [info.st_dev, info.st_ino] != inode)
                    or (not is_stage and (inode is None or actual != expected))
                    or (is_stage and not expected.startswith(actual))):
                raise ValueError("registration was modified; refusing removal")
            pair.append((candidate, info))
        if any(info.st_nlink != len(pair) for _, info in pair):
            raise ValueError("registration has unowned links; refusing removal")
        if len(pair) == 2 and (pair[0][1].st_dev, pair[0][1].st_ino) != (
                pair[1][1].st_dev, pair[1][1].st_ino):
            raise ValueError("registration has unowned links; refusing removal")
        survivors.extend(path for path, _ in pair)
    for path in survivors:
        path.unlink()
