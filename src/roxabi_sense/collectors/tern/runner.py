"""Bounded, metadata-only Tern CLI access.

Only inspect and process are accepted; neither command reads terminal output.
"""
from __future__ import annotations

import json
import os
import selectors
import subprocess
import threading
import time
from typing import Any


class TernUnavailable(Exception):
    """The source could not provide a complete bounded observation."""


def _read(
    argv: list[str], *, deadline: float | None = None,
    cancel: threading.Event | None = None, timeout: float = 3.0,
    limit: int = 2_000_000,
) -> dict[str, Any]:
    def clock() -> float:
        return time.clock_gettime(time.CLOCK_BOOTTIME)

    command_deadline = min(clock() + timeout, deadline if deadline is not None else float("inf"))

    def remaining() -> float:
        if cancel is not None and cancel.is_set():
            raise TernUnavailable("command cancelled")
        left = command_deadline - clock()
        if left <= 0:
            raise TernUnavailable("command timeout")
        return left

    remaining()
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise TernUnavailable("command unavailable") from exc
    assert proc.stdout is not None
    data = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while True:
                if not selector.select(min(remaining(), 0.1)):
                    continue
                remaining()
                chunk = os.read(proc.stdout.fileno(), min(65536, limit + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > limit:
                    raise TernUnavailable("response too large")
        while proc.poll() is None:
            try:
                proc.wait(timeout=min(remaining(), 0.1))
            except subprocess.TimeoutExpired:
                continue
        remaining()
        if proc.returncode != 0:
            raise TernUnavailable("command failed")
        value = json.loads(data)
        remaining()
        if not isinstance(value, dict):
            raise TernUnavailable("invalid response")
        return value
    except ValueError as exc:
        raise TernUnavailable("invalid or incomplete response") from exc
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()


def inspect(
    window_key: str | None, *, deadline: float | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    argv = ["tern", "inspect", "--json"]
    if window_key is not None:
        argv.extend(["--window", window_key])
    result = _read(argv, deadline=deadline, cancel=cancel)
    return {"clients": result.get("clients")} if window_key is None else result


def process(
    window_key: str, pane_id: int, *, deadline: float | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    if isinstance(pane_id, bool) or not isinstance(pane_id, int) or pane_id < 0:
        raise TernUnavailable("invalid pane")
    return _read(
        ["tern", "process", str(pane_id), "--json", "--window", window_key],
        deadline=deadline, cancel=cancel,
    )
