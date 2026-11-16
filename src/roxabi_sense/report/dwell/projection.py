"""Shared projection of a raw focus fact into its attention identity."""
from __future__ import annotations

from typing import NamedTuple

from roxabi_sense.store import Event
from roxabi_sense.util.titles import normalize_title


class FocusFields(NamedTuple):
    app: str
    title: str
    cwd: str | None
    agent: str | None
    pid: int | None
    session_id: str | None
    agent_match: str | None
    agent_pid: int | None
    window_id: str | None
    source: str | None


def _integer(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def project_focus(event: Event) -> FocusFields:
    from roxabi_sense.report.segments import norm_app

    payload = event.payload
    raw_agent = payload.get("agent")
    agent = raw_agent if isinstance(raw_agent, dict) else {}
    return FocusFields(
        norm_app(str(payload.get("app") or "")),
        normalize_title(str(payload.get("title") or "")),
        str(agent["cwd"]) if agent.get("cwd") else None,
        str(agent["agent"]) if agent.get("agent") else None,
        _integer(payload.get("pid")),
        str(agent["session_id"]) if agent.get("session_id") else None,
        str(agent["match"]) if agent.get("match") else None,
        _integer(agent.get("pid")),
        str(payload["window_id"]) if payload.get("window_id") else None,
        str(payload["source"]) if payload.get("source") else None,
    )
