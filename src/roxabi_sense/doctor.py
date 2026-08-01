"""Operator health checks — is sense useful for agents right now?"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from roxabi_sense import __version__
from roxabi_sense.config import SenseConfig
from roxabi_sense.install_service import unit_path
from roxabi_sense.paths import default_config_path
from roxabi_sense.report import load_status_snapshot
from roxabi_sense.report.presence import age_seconds

CheckStatus = Literal["ok", "warn", "fail"]


@dataclass(frozen=True)
class Check:
    name: str
    status: CheckStatus
    detail: str
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DoctorReport:
    version: str
    ok: bool
    fail_count: int
    warn_count: int
    checks: list[Check]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "ok": self.ok,
            "fail_count": self.fail_count,
            "warn_count": self.warn_count,
            "checks": [c.to_dict() for c in self.checks],
        }


def run_doctor(cfg: SenseConfig) -> DoctorReport:
    """Collect install + data-plane + MCP readiness checks."""
    checks: list[Check] = [
        Check(name="package", status="ok", detail=f"roxabi-sense {__version__}"),
        _check_binary(),
        _check_config(cfg),
        _check_db(cfg.db_path),
        *_check_store_and_presence(cfg),
        _check_unit_file(),
        _check_systemd_unit(),
        _check_mcp_sdk(),
    ]
    fails = sum(1 for c in checks if c.status == "fail")
    warns = sum(1 for c in checks if c.status == "warn")
    return DoctorReport(
        version=__version__,
        ok=fails == 0,
        fail_count=fails,
        warn_count=warns,
        checks=checks,
    )


def format_doctor_text(report: DoctorReport) -> str:
    lines = [
        f"sense doctor  v{report.version}",
        f"result: {'OK' if report.ok else 'FAIL'}"
        f"  (fail={report.fail_count} warn={report.warn_count})",
        "",
    ]
    for c in report.checks:
        mark = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL"}[c.status]
        lines.append(f"[{mark}] {c.name}: {c.detail}")
        if c.hint:
            lines.append(f"       hint: {c.hint}")
    if not report.ok:
        lines.append("")
        lines.append("Agents should not trust timeline tools until FAIL items are fixed.")
    return "\n".join(lines)


def doctor_exit_code(report: DoctorReport) -> int:
    """0 = no fails (warns allowed); 1 = at least one fail."""
    return 0 if report.ok else 1


def _check_binary() -> Check:
    which = shutil.which("sense")
    if which:
        return Check(name="binary", status="ok", detail=f"PATH sense → {which}")
    return Check(
        name="binary",
        status="warn",
        detail="sense not on PATH (dev uv run still works)",
        hint="uv tool install -e '.[mcp]'  # see README install matrix",
    )


def _check_config(cfg: SenseConfig) -> Check:
    path = default_config_path()
    if not path.is_file():
        return Check(name="config", status="ok", detail=f"no file at {path} (defaults)")
    return Check(
        name="config",
        status="ok",
        detail=f"loaded {path}  mcp.detail={cfg.mcp_detail}",
    )


def _check_db(db_path: Path) -> Check:
    if not db_path.is_file():
        return Check(
            name="db",
            status="fail",
            detail=f"missing {db_path}",
            hint="sense once  OR  systemctl --user enable --now roxabi-sense.service",
        )
    try:
        readable = os.access(db_path, os.R_OK)
        writable = os.access(db_path, os.W_OK)
    except OSError as exc:
        return Check(name="db", status="fail", detail=f"cannot access {db_path}: {exc}")
    if not readable:
        return Check(name="db", status="fail", detail=f"not readable: {db_path}")
    if not writable:
        return Check(
            name="db",
            status="warn",
            detail=f"path={db_path} (read-only)",
            hint="check ownership/permissions on the DB and parent dir",
        )
    return Check(name="db", status="ok", detail=f"path={db_path}")


def _check_store_and_presence(cfg: SenseConfig) -> list[Check]:
    snap = load_status_snapshot(
        cfg.db_path,
        offline_threshold_s=cfg.offline_threshold_s,
        idle_threshold_s=cfg.idle_threshold_s,
    )
    if not snap.db_exists:
        return [
            Check(
                name="events",
                status="fail",
                detail="no store (0 events)",
                hint="sense once  OR  start the user unit",
            ),
            Check(
                name="presence",
                status="fail",
                detail="offline (no last_tick)",
                hint="run collectors until last_tick is set",
            ),
        ]
    out: list[Check] = [
        Check(
            name="events",
            status="ok" if snap.events > 0 else "warn",
            detail=f"count={snap.events}",
            hint=None if snap.events > 0 else "store empty — wait for ticks or sense once",
        )
    ]
    age = age_seconds(snap.last_tick)
    thr = cfg.offline_threshold_s
    if snap.last_tick is None:
        out.append(
            Check(
                name="last_tick",
                status="fail",
                detail="missing meta last_tick",
                hint="sense once  OR  ensure daemon is writing",
            )
        )
    elif age is not None and age > thr:
        out.append(
            Check(
                name="last_tick",
                status="fail",
                detail=f"{snap.last_tick}  age={age:.0f}s > offline_threshold={thr:.0f}s",
                hint="systemctl --user status roxabi-sense.service",
            )
        )
    else:
        age_s = f"{age:.0f}s" if age is not None else "?"
        out.append(Check(name="last_tick", status="ok", detail=f"{snap.last_tick}  age={age_s}"))

    p = snap.presence
    if p.state == "offline":
        out.append(
            Check(
                name="presence",
                status="fail",
                detail=f"state={p.state} authority={p.authority}",
                hint="daemon stale — enable/restart user unit or run sense daemon",
            )
        )
    else:
        out.append(
            Check(
                name="presence",
                status="ok",
                detail=f"state={p.state} authority={p.authority} conf={p.confidence}",
            )
        )
    return out


def _check_unit_file() -> Check:
    path = unit_path()
    if path.is_file():
        return Check(name="unit_file", status="ok", detail=str(path))
    return Check(
        name="unit_file",
        status="warn",
        detail=f"missing {path}",
        hint="sense install-service",
    )


def _check_systemd_unit() -> Check:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return Check(name="systemd", status="warn", detail="systemctl not on PATH")
    try:
        proc = subprocess.run(
            [systemctl, "--user", "is-active", "roxabi-sense.service"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Check(name="systemd", status="warn", detail=f"systemctl query failed: {exc}")
    state = (proc.stdout or proc.stderr or "").strip() or f"exit={proc.returncode}"
    if state == "active":
        return Check(name="systemd", status="ok", detail="roxabi-sense.service active")
    return Check(
        name="systemd",
        status="warn",
        detail=f"roxabi-sense.service → {state}",
        hint="systemctl --user enable --now roxabi-sense.service",
    )


def _check_mcp_sdk() -> Check:
    try:
        from mcp.server import MCPServer  # noqa: F401
    except ImportError:
        return Check(
            name="mcp_sdk",
            status="fail",
            detail="mcp package not importable",
            hint="uv tool install -e '.[mcp]' --force   # or: uv sync --extra mcp",
        )
    return Check(name="mcp_sdk", status="ok", detail="mcp SDK importable")
