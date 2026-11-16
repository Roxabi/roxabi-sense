"""Nonblocking, process-wide ownership of focus facts for one store."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path


def acquire_focus_owner(db_path: Path) -> int | None:
    path = db_path.with_name(db_path.name + ".focus.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise
    return fd


def release_focus_owner(fd: int | None) -> None:
    if fd is not None:
        os.close(fd)
