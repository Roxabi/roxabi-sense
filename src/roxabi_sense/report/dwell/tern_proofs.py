"""Project Tern proof lifetimes onto native focus facts without rewriting history."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime

from roxabi_sense.store import Event
from roxabi_sense.util.time import parse_ts, to_z


def proof_ends(snapshots: list[Event], raw_meta: str | None) -> dict[str, datetime]:
    ends: dict[str, datetime] = {}
    for event in snapshots:
        closed = event.payload.get("closed")
        if not isinstance(closed, list):
            continue
        for item in closed:
            if not isinstance(item, dict) or not isinstance(item.get("proof_ref"), str):
                continue
            try:
                end = parse_ts(item["confirmed_through"])
            except (KeyError, TypeError, ValueError):
                continue
            ref = item["proof_ref"]
            ends[ref] = min(ends.get(ref, end), end)
    try:
        meta = json.loads(raw_meta or "{}")
        latest = snapshots[-1] if snapshots else None
        if (not isinstance(meta, dict) or latest is None or meta.get("healthy") is not True
                or meta.get("snapshot_id") != latest.id
                or meta.get("run_ref") != latest.payload.get("run_ref")):
            return ends
        scopes = meta.get("scopes")
        if not isinstance(scopes, dict):
            return ends
        for item in scopes.values():
            if not isinstance(item, dict) or not isinstance(item.get("proof_ref"), str):
                continue
            try:
                start = parse_ts(item["started_at"])
                end = parse_ts(item["last_observed_at"])
                fresh = parse_ts(item["fresh_until"])
                if not start <= end <= fresh:
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            ref = item["proof_ref"]
            if ref not in ends:
                ends[ref] = end
    except (TypeError, ValueError):
        pass
    return ends


def clear_context(event: Event) -> Event:
    context = event.payload.get("context")
    if not isinstance(context, dict) or context.get("source") != "tern":
        return event
    payload = dict(event.payload)
    payload.pop("context", None)
    payload.pop("agent", None)
    return replace(event, payload=payload)


def normalize_focus(events: list[Event], snapshots: list[Event], raw_meta: str | None,
                    horizon: datetime, *, context_cuts: set[str] | None = None
                    ) -> tuple[list[Event], set[str]]:
    """Cut expired context at last genuine observation; native focus remains intact."""
    ends = proof_ends(snapshots, raw_meta)
    ordered = sorted(events, key=lambda e: (e.ts, e.id))
    result: list[Event] = []
    cuts: set[str] = set()
    # Remember the last proven context across a knowledge gap. A replacement
    # proof is not itself a human switch, but native or semantic changes are.
    last_proven: Event | None = None
    gap = False
    previous: Event | None = None
    for index, event in enumerate(ordered):
        context = event.payload.get("context")
        is_tern = isinstance(context, dict) and context.get("source") == "tern"
        if isinstance(context, dict) and is_tern and gap and last_proven and previous:
            old_context = last_proven.payload.get("context")
            native_fields = ("app", "source", "window_id", "pid")
            if (all(event.payload.get(k) == previous.payload.get(k) for k in native_fields)
                    and isinstance(old_context, dict)
                    and context.get("ref") == old_context.get("ref")):
                if context_cuts is not None:
                    context_cuts.add(event.ts)
                if event.payload.get("title") == previous.payload.get("title"):
                    cuts.add(event.ts)
        if not is_tern:
            if (last_proven is not None and previous is not None
                    and all(event.payload.get(k) == previous.payload.get(k)
                            for k in ("app", "source", "window_id", "pid"))
                    and event.payload.get("app") == "so.stencil.tern"):
                if context_cuts is not None:
                    context_cuts.add(event.ts)
                if event.payload.get("title") == previous.payload.get("title"):
                    cuts.add(event.ts)
            result.append(event)
            if last_proven is not None:
                gap = True
            previous = event
            continue
        assert isinstance(context, dict)
        ref = context.get("proof_ref")
        end = ends.get(ref) if isinstance(ref, str) else None
        start = parse_ts(event.ts)
        next_time = parse_ts(ordered[index + 1].ts) if index + 1 < len(ordered) else horizon
        if end is None or end <= start:
            result.append(clear_context(event))
            gap = True
        else:
            result.append(event)
            last_proven = event
            gap = False
            if end < min(next_time, horizon):
                stamp = to_z(end)
                result.append(replace(clear_context(event), ts=stamp))
                cuts.add(stamp)
                if context_cuts is not None:
                    context_cuts.add(stamp)
                gap = True
        previous = event
    return result, cuts


def current_context(event: Event, snapshot: Event | None, raw_meta: str | None,
                    now: datetime) -> Event:
    context = event.payload.get("context")
    if not isinstance(context, dict) or context.get("source") != "tern":
        return event
    try:
        meta = json.loads(raw_meta or "{}")
        scopes = meta.get("scopes", {})
        if (snapshot is not None and meta.get("healthy") is True
                and meta.get("snapshot_id") == snapshot.id
                and meta.get("run_ref") == snapshot.payload.get("run_ref")
                and isinstance(scopes, dict)):
            for item in scopes.values():
                if (isinstance(item, dict) and item.get("proof_ref") == context.get("proof_ref")
                        and parse_ts(item["started_at"]) <= now
                        and parse_ts(item["last_observed_at"]) <= now
                        < parse_ts(item["fresh_until"])):
                    return event
    except (KeyError, TypeError, ValueError, AttributeError):
        pass
    return clear_context(event)
