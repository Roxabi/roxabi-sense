"""Filesystem primitives for the explicitly installed browser connector."""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
from pathlib import Path

HOST_NAME = "com.roxabi.sense.meeting_browser"
EXTENSION_ID = re.compile(r"[a-p]{32}\Z")


def safe_directory(path: Path, *, create: bool = False) -> Path:
    """Reject symlinks and directories writable by unrelated users."""
    path = Path(os.path.abspath(path.expanduser()))
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if not create:
                raise
            current.mkdir(mode=0o700)
            info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.getuid()):
            raise ValueError(f"unsafe directory: {current}")
        if info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
            raise ValueError(f"writable directory: {current}")
    return path


def read_owned(path: Path, *, max_links: int = 1) -> bytes:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o022 or info.st_nlink > max_links or info.st_size > 65536):
            raise ValueError(f"unsafe file: {path}")
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            return stream.read()
    finally:
        if fd != -1:
            os.close(fd)


def create_owned(path: Path, content: bytes, mode: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(content)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        if fd != -1:
            os.close(fd)


def files_for(profile: Path, *, data_dir: bool = False,
              allow_missing_profile: bool = False) -> tuple[Path, Path, Path]:
    profile = Path(os.path.abspath(profile.expanduser()))
    if profile.name in ("", ".", ".."):
        raise ValueError("an explicit browser directory is required")
    if allow_missing_profile and not data_dir:
        safe_directory(profile.parent)
        try:
            profile.lstat()
        except FileNotFoundError:
            pass
        else:
            safe_directory(profile)
    else:
        safe_directory(profile)
    root = profile if data_dir else profile.parent
    host_dir = safe_directory(root / "NativeMessagingHosts", create=True)
    return (host_dir / f"{HOST_NAME}.json", host_dir / f"{HOST_NAME}.sh",
            host_dir / f"{HOST_NAME}.owner.json")


def launcher(binary: str, config: Path, db: Path, source: str, extension: str,
             profile: Path, *, data_dir: bool = False) -> bytes:
    flag = "--data-dir" if data_dir else "--profile"
    args = ["env", f"SENSE_DB={db}", binary, "--config", str(config), "meeting-browser",
            "host", flag, str(profile), "--source-id", source, "--extension-id", extension]
    return ("#!/bin/sh\nexec " + " ".join(shlex.quote(arg) for arg in args) + ' "$@"\n').encode()


def manifest(script: Path, extension: str) -> bytes:
    data = {"name": HOST_NAME, "description": "Roxabi Sense meeting observations",
            "path": str(script), "type": "stdio",
            "allowed_origins": [f"chrome-extension://{extension}/"]}
    return (json.dumps(data, indent=2) + "\n").encode()
