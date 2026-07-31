"""Shared timestamp helpers (ISO-Z)."""

from __future__ import annotations

from datetime import UTC, datetime


def parse_ts(ts: str) -> datetime:
    """Parse ISO timestamp; accept trailing Z as UTC."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def to_z(dt: datetime) -> str:
    """Format datetime as UTC ISO-Z without microseconds."""
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_now_z() -> str:
    return to_z(datetime.now(UTC))
