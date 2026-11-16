"""Bounded Chrome Native Messaging reader; stdout is reserved for the protocol."""

from __future__ import annotations

import json
import os
import re
import select
import struct
import sys
import time
import uuid
from typing import Any

from roxabi_sense.config import SenseConfig
from roxabi_sense.store import Store
from roxabi_sense.store.meeting_protocol import (
    MAX_FRAME_BYTES,
    SCOPE,
    validate_sources,
    validate_wire,
)

_READ_TIMEOUT = 20.0
_MIN_INTERVAL = 1.0
_EXTENSION_ID = re.compile(r"[a-p]{32}\Z")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _read_exact(fd: int, size: int, deadline: float) -> bytes:
    chunks: list[bytes] = []
    while size:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise TimeoutError("native input timed out")
        chunk = os.read(fd, size)
        if not chunk:
            if chunks:
                raise ValueError("truncated frame")
            raise EOFError
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def _registered(store: Store, source_id: str, extension_id: str) -> bool:
    try:
        registry = json.loads(store.get_meta("meeting_browser_sources") or "null")
        bindings = json.loads(store.get_meta("meeting_browser_bindings") or "null")
    except (ValueError, TypeError):
        return False
    return (
        validate_sources(registry) and source_id in registry["sources"]
        and isinstance(bindings, dict) and bindings.get(source_id) == extension_id
    )


def run_host(cfg: SenseConfig, source_id: str, extension_id: str, origin: str) -> int:
    """Read one connection. Invalid origin, sequence or frame ends the epoch."""
    if (
        not cfg.meeting_browser
        or not _EXTENSION_ID.fullmatch(extension_id)
        or origin != f"chrome-extension://{extension_id}/"
    ):
        return 1
    epoch = str(uuid.uuid4())
    seq = 0
    previous: float | None = None
    try:
        with Store(cfg.db_path) as store:
            if not _registered(store, source_id, extension_id):
                return 1
            fd = sys.stdin.fileno()
            while True:
                deadline = time.monotonic() + _READ_TIMEOUT
                header = _read_exact(fd, 4, deadline)
                size = struct.unpack("=I", header)[0]
                if size == 0 or size > MAX_FRAME_BYTES:
                    return 1
                try:
                    raw = _read_exact(fd, size, deadline)
                except EOFError as exc:
                    raise ValueError("truncated frame") from exc
                now = time.monotonic()
                if previous is not None and now - previous < _MIN_INTERVAL:
                    return 1
                payload = json.loads(raw, object_pairs_hook=_unique_object)
                if not validate_wire(payload) or payload["seq"] != seq:
                    return 1
                with store.batch():
                    if not _registered(store, source_id, extension_id):
                        return 1
                    store.append("meeting_snapshot", {
                        "v": 1, "scope": SCOPE, "source_id": source_id,
                        "epoch": epoch, "seq": seq, "complete": payload["complete"],
                        "tabs": payload["tabs"],
                    })
                previous = now
                seq += 1
    except EOFError:
        return 0
    except (OSError, ValueError, TypeError, TimeoutError, json.JSONDecodeError):
        return 1
