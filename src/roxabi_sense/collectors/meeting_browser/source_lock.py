"""Stable per-source lock and durable conflict marker; never unlink its inode."""

from __future__ import annotations

import fcntl
import os
import stat
from pathlib import Path
from uuid import UUID


class SourceLock:
    def __init__(self, db_path: Path, source: str) -> None:
        if str(UUID(source)) != source:
            raise ValueError("invalid source")
        path = db_path.parent / f".meeting-source-{source}.lock"
        flags = os.O_RDWR | os.O_NOFOLLOW
        try:
            fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, b"0")
                os.fsync(fd)
                directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except BaseException:
                os.close(fd)
                raise
        except FileExistsError:
            fd = os.open(path, flags)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1):
            os.close(fd)
            raise ValueError("unsafe source lock")
        self.fd = fd
        self.owned = False

    def acquire(self) -> bool:
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Mark on the stable inode before trying SQLite. If the database write
            # fails, every later connection still sees the poisoned source.
            try:
                os.pwrite(self.fd, b"1", 0)
                os.fsync(self.fd)
            except OSError:
                # The caller must still attempt the SQLite latch.
                pass
            return False
        self.owned = True
        return True

    def conflicted(self) -> bool:
        return os.pread(self.fd, 1, 0) != b"0"

    def close(self) -> None:
        os.close(self.fd)
