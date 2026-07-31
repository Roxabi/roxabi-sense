"""Host-side long-lived AT-SPI agent (system python subprocess)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from roxabi_sense.atspi.script import NameEventsMode, agent_env, name_mode_normalized

ProbeMode = Literal["focus", "desktop"]

_WORKER = Path(__file__).resolve().with_name("agent_worker.py")


def _system_python() -> str:
    for candidate in ("/usr/bin/python3", shutil.which("python3") or ""):
        if candidate and candidate != sys.executable:
            return candidate
    return sys.executable


def probe_once(
    mode: ProbeMode = "desktop",
    *,
    timeout: float = 5.0,
) -> list[dict[str, Any]]:
    """One-shot desktop/focus inventory (sense once / tests)."""
    env = os.environ.copy()
    env.update(agent_env())
    env["SENSE_ATSPI_ONCE"] = mode
    try:
        proc = subprocess.run(
            [_system_python(), str(_WORKER)],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict) and msg.get("type") == "probe_result":
            raw = msg.get("windows") or []
            return [w for w in raw if isinstance(w, dict)]
    return []


class FocusAtspiAgent:
    """Long-lived AT-SPI: events + in-process probes; JSON lines on stdout."""

    def __init__(
        self,
        on_message: Callable[[dict[str, Any]], None],
        *,
        name_events: NameEventsMode = "throttled",
        name_throttle_s: float = 10.0,
        probe_min_s: float = 0.5,
        trace: bool = False,
    ) -> None:
        self._on_message = on_message
        self._name_events: NameEventsMode = name_mode_normalized(name_events)
        self._name_throttle_s = name_throttle_s
        self._probe_min_s = probe_min_s
        self._trace = bool(trace)
        self._proc: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        if self.running:
            return
        if self._proc is not None and self._proc.poll() is not None:
            try:
                self._proc.wait(timeout=0.1)
            except Exception:  # noqa: BLE001
                pass
            self._proc = None
        self._stop.clear()
        env = os.environ.copy()
        env.update(
            agent_env(
                name_events=self._name_events,
                name_throttle_s=self._name_throttle_s,
                probe_min_s=self._probe_min_s,
                trace=self._trace,
            )
        )
        self._proc = subprocess.Popen(
            [_system_python(), "-u", str(_WORKER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=env,
        )
        self._thread = threading.Thread(
            target=self._read_loop, name="focus-atspi-agent", daemon=True
        )
        self._thread.start()

    def request_probe(self, mode: ProbeMode = "desktop") -> None:
        self._send({"cmd": "probe", "mode": mode})

    def stop(self, *, timeout: float = 2.0) -> None:
        self._stop.set()
        self._send({"cmd": "quit"})
        proc = self._proc
        if proc is not None:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                if proc.stdout is not None:
                    proc.stdout.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                proc.terminate()
                proc.wait(timeout=timeout)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                    proc.wait(timeout=timeout)
                except Exception:  # noqa: BLE001
                    pass
        self._proc = None
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _send(self, msg: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            return
        try:
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
        except Exception:  # noqa: BLE001
            pass

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                if self._stop.is_set():
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                typ = msg.get("type")
                if typ == "error":
                    print(f"sense atspi-agent: {msg.get('error')}", flush=True)
                elif typ == "warn":
                    print(f"sense atspi-agent: {msg.get('warn')}", flush=True)
                elif typ == "ready":
                    tr = "trace" if msg.get("trace") else "no-trace"
                    print(
                        f"sense atspi-agent: ready "
                        f"(name_events={msg.get('name_events', '?')} {tr})",
                        flush=True,
                    )
                try:
                    self._on_message(msg)
                except Exception as exc:  # noqa: BLE001
                    print(f"sense atspi-agent callback error: {exc}", flush=True)
        except ValueError:
            pass
