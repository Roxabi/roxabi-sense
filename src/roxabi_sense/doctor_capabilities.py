"""Capability status for doctor (focus / idle backends)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from roxabi_sense.config import SenseConfig

CheckStatus = Literal["ok", "warn", "fail"]


@dataclass(frozen=True)
class Capability:
    status: str  # available | degraded | unavailable
    backend: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapCheck:
    name: str
    status: CheckStatus
    detail: str
    hint: str | None = None


def capabilities(cfg: SenseConfig) -> dict[str, Capability]:
    """Prefer daemon meta; cold path uses live probe without hanging long."""
    from roxabi_sense.store import Store

    meta: dict[str, str] = {}
    if cfg.db_path.is_file():
        try:
            with Store(cfg.db_path) as store:
                for key in (
                    "focus_backend",
                    "focus_status",
                    "atspi_agent",
                    "idle_watch",
                    "session_type",
                    "desktop_family",
                ):
                    val = store.get_meta(key)
                    if val is not None:
                        meta[key] = val
        except Exception:  # noqa: BLE001
            meta = {}

    return {
        "focus": _focus_capability(cfg, meta),
        "idle": _idle_capability(cfg, meta),
    }


def capability_checks(caps: dict[str, Capability]) -> list[CapCheck]:
    out: list[CapCheck] = []
    for name, cap in caps.items():
        if cap.status in {"unavailable", "degraded"}:
            st: CheckStatus = "warn"
        else:
            st = "ok"
        out.append(
            CapCheck(
                name=name,
                status=st,
                detail=f"{cap.status} backend={cap.backend} ({cap.reason})",
            )
        )
    return out


def _focus_capability(cfg: SenseConfig, meta: dict[str, str]) -> Capability:
    if not cfg.focus:
        return Capability(
            status="unavailable",
            backend="off",
            reason="focus disabled in config",
        )
    backend = meta.get("focus_backend")
    status = meta.get("focus_status")
    if backend and status:
        st = meta.get("session_type", "?")
        df = meta.get("desktop_family", "?")
        reason = f"session={st} desktop={df}"
        if meta.get("atspi_agent"):
            reason += f" atspi_agent={meta['atspi_agent']}"
        return Capability(status=status, backend=backend, reason=reason)
    try:
        from roxabi_sense.collectors.focus.select import select_probe

        probe = select_probe()
        st = "available" if probe.source != "noop" else "unavailable"
        return Capability(
            status=st,
            backend=str(probe.source),
            reason="live select (no daemon meta)",
        )
    except Exception as exc:  # noqa: BLE001
        return Capability(
            status="unavailable",
            backend="unknown",
            reason=str(exc)[:120],
        )


def _idle_capability(cfg: SenseConfig, meta: dict[str, str]) -> Capability:
    if not cfg.idle:
        return Capability(
            status="unavailable",
            backend="off",
            reason="idle disabled in config",
        )
    watch = meta.get("idle_watch")
    if watch == "ready":
        return Capability(
            status="available",
            backend="wayland-idle",
            reason="idle_watch ready",
        )
    if watch in {"dead", "restarting"}:
        return Capability(
            status="degraded",
            backend=cfg.idle_backend,
            reason=f"idle_watch={watch} (logind may cover)",
        )
    if cfg.idle_backend == "logind":
        return Capability(
            status="available",
            backend="logind",
            reason="logind backend",
        )
    if cfg.idle_backend == "off":
        return Capability(
            status="unavailable",
            backend="off",
            reason="idle_backend=off",
        )
    return Capability(
        status="available" if watch != "n/a" else "degraded",
        backend=cfg.idle_backend,
        reason=f"idle_watch={watch or 'unknown'}",
    )
