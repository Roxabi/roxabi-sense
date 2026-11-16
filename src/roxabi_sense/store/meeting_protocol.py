"""Strict, private browser-meeting fact schemas and observation policy."""

from __future__ import annotations

from uuid import UUID

SCOPE = "chrome_google_meet"
CONTINUITY_SECONDS = 8
MAX_SOURCES = 32
MAX_TABS = 128
MAX_FRAME_BYTES = 65536


def _uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def validate_sources(payload: object) -> bool:
    if not isinstance(payload, dict) or set(payload) != {"v", "scope", "sources"}:
        return False
    sources = payload["sources"]
    return (
        type(payload["v"]) is int and payload["v"] == 1
        and payload["scope"] == SCOPE
        and isinstance(sources, list) and len(sources) <= MAX_SOURCES
        and all(_uuid(item) for item in sources)
        and len(set(sources)) == len(sources)
    )


def validate_wire(payload: object) -> bool:
    if not isinstance(payload, dict) or set(payload) != {"v", "seq", "complete", "tabs"}:
        return False
    if type(payload["v"]) is not int or payload["v"] != 1:
        return False
    if type(payload["seq"]) is not int or not 0 <= payload["seq"] <= 2**31 - 1:
        return False
    if type(payload["complete"]) is not bool or not isinstance(payload["tabs"], list):
        return False
    tabs = payload["tabs"]
    if len(tabs) > MAX_TABS or (not payload["complete"] and tabs):
        return False
    seen: set[str] = set()
    combinations = {
        ("joined", "leave_control"), ("not_joined", "home"),
        ("not_joined", "prejoin"), ("not_joined", "postcall"),
        ("unknown", "unrecognized"), ("unknown", "no_response"),
    }
    for tab in tabs:
        if not isinstance(tab, dict) or set(tab) != {"tab_id", "state", "basis"}:
            return False
        tab_id = tab["tab_id"]
        if not _uuid(tab_id) or tab_id in seen:
            return False
        if not isinstance(tab["state"], str) or not isinstance(tab["basis"], str):
            return False
        if (tab["state"], tab["basis"]) not in combinations:
            return False
        seen.add(tab_id)
    return True


def validate_snapshot(payload: object) -> bool:
    if not isinstance(payload, dict) or set(payload) != {
        "v", "scope", "source_id", "epoch", "seq", "complete", "tabs"
    }:
        return False
    return (
        payload["scope"] == SCOPE and _uuid(payload["source_id"]) and _uuid(payload["epoch"])
        and validate_wire({key: payload[key] for key in ("v", "seq", "complete", "tabs")})
    )
