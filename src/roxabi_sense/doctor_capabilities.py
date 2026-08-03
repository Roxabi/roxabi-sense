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
                    "idle_backend",
                    "idle_status",
                    "idle_chain_reason",
                    "session_type",
                    "desktop_family",
                ):
                    val = store.get_meta(key)
                    if val is not None:
                        meta[key] = val
        except Exception:  # noqa: BLE001
            meta = {}

    fidelity = _meeting_fidelity_capability(cfg, meta)
    return {
        "focus": _focus_capability(cfg, meta),
        "idle": _idle_capability(cfg, meta),
        "meeting": fidelity,
    }


def capability_checks(caps: dict[str, Capability]) -> list[CapCheck]:
    out: list[CapCheck] = []
    for name, cap in caps.items():
        if cap.status in {"unavailable", "degraded"}:
            st: CheckStatus = "warn"
        else:
            st = "ok"
        hint = None
        if name == "meeting" and cap.status == "degraded":
            hint = (
                "meeting_total_s may under-count multitask; "
                "AT-SPI multi-window desktop improves fidelity"
            )
        out.append(
            CapCheck(
                name=name,
                status=st,
                detail=f"{cap.status} backend={cap.backend} ({cap.reason})",
                hint=hint,
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


def _meeting_fidelity_capability(cfg: SenseConfig, meta: dict[str, str]) -> Capability:
    """Meeting call-duration honesty depends on desktop inventory class."""
    from roxabi_sense.report.meeting_fidelity import meeting_fidelity_from_events
    from roxabi_sense.store import Store

    if not cfg.db_path.is_file():
        return Capability(
            status="unavailable",
            backend="none",
            reason="no store",
        )
    try:
        with Store(cfg.db_path) as store:
            # Recent day only — enough for operator honesty signal
            events = store.events_for_day(None, kinds=("desktop_snapshot",), limit=500)
            fid, note = meeting_fidelity_from_events(
                events,
                focus_backend=meta.get("focus_backend") or store.get_meta("focus_backend"),
            )
    except Exception as exc:  # noqa: BLE001
        return Capability(
            status="degraded",
            backend="unknown",
            reason=str(exc)[:120],
        )
    if fid == "full":
        return Capability(status="available", backend=fid, reason=note[:160])
    if fid == "active_only":
        return Capability(status="degraded", backend=fid, reason=note[:160])
    return Capability(status="degraded", backend=fid, reason=note[:160])


def _idle_capability(cfg: SenseConfig, meta: dict[str, str]) -> Capability:
    if not cfg.idle:
        return Capability(
            status="unavailable",
            backend="off",
            reason="idle disabled in config",
        )
    # Prefer explicit chain meta written by daemon (#45)
    backend = meta.get("idle_backend")
    status = meta.get("idle_status")
    if backend and status:
        reason = meta.get("idle_chain_reason") or f"idle_watch={meta.get('idle_watch', '?')}"
        return Capability(status=status, backend=backend, reason=reason)

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
