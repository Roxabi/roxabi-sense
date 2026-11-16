"""Bounded, metadata-only Tern CLI access.

Only inspect and process are accepted; neither command reads terminal output.
"""
from __future__ import annotations

import json
import os
import selectors
import subprocess
import time
from typing import Any


class TernUnavailable(Exception):
    """The source could not provide a complete bounded observation."""


def _read(argv: list[str], *, timeout: float = 3.0, limit: int = 2_000_000) -> dict[str, Any]:
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise TernUnavailable("command unavailable") from exc
    assert proc.stdout is not None
    data = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TernUnavailable("command timeout")
                chunk = os.read(proc.stdout.fileno(), min(65536, limit + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > limit:
                    raise TernUnavailable("response too large")
        if proc.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
            raise TernUnavailable("command failed")
        value = json.loads(data)
        if not isinstance(value, dict):
            raise TernUnavailable("invalid response")
        return value
    except (ValueError, subprocess.TimeoutExpired) as exc:
        raise TernUnavailable("invalid or incomplete response") from exc
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()


def inspect(window_key: str) -> dict[str, Any]:
    return _read(["tern", "inspect", "--json", "--window", window_key])


def process(window_key: str, pane_id: int) -> dict[str, Any]:
    if isinstance(pane_id, bool) or not isinstance(pane_id, int) or pane_id < 0:
        raise TernUnavailable("invalid pane")
    return _read(["tern", "process", str(pane_id), "--json", "--window", window_key])
