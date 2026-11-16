"""Project Tern proof lifetimes onto native focus facts without rewriting history."""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from roxabi_sense.report.dwell.projection import project_focus
from roxabi_sense.store import Event
from roxabi_sense.util.time import parse_ts, to_z_precise


def proof_bounds(
    snapshots: list[Event], raw_meta: str | None,
) -> dict[str, tuple[datetime, datetime]]:
    """Return confirmed intervals, never extending history to a live lease."""
    bounds: dict[str, tuple[datetime, datetime]] = {}
    closed_refs: set[str] = set()
    invalid: set[str] = set()
    for event in snapshots:
        closed = event.payload.get("closed")
        if not isinstance(closed, list):
            continue
        for item in closed:
            if not isinstance(item, dict) or not isinstance(item.get("proof_ref"), str):
                continue
            ref = item["proof_ref"]
            closed_refs.add(ref)
            try:
                start = parse_ts(item["started_at"])
                end = parse_ts(item["confirmed_through"])
                if start.tzinfo is None or end.tzinfo is None or start > end:
                    invalid.add(ref)
                    continue
            except (KeyError, TypeError, ValueError):
                invalid.add(ref)
                continue
            if ref in bounds and bounds[ref] != (start, end):
                invalid.add(ref)
            else:
                bounds[ref] = (start, end)
    for ref in invalid:
        bounds.pop(ref, None)
    try:
        meta = json.loads(raw_meta or "{}")
        latest = snapshots[-1] if snapshots else None
        if (not isinstance(meta, dict) or latest is None or meta.get("healthy") is not True
                or meta.get("snapshot_id") != latest.id
                or meta.get("run_ref") != latest.payload.get("run_ref")):
            return bounds
        scopes = meta.get("scopes")
        if not isinstance(scopes, dict):
            return bounds
        for item in scopes.values():
            if not isinstance(item, dict) or not isinstance(item.get("proof_ref"), str):
                continue
            try:
                start = parse_ts(item["started_at"])
                end = parse_ts(item["last_observed_at"])
                fresh = parse_ts(item["fresh_until"])
                if (start.tzinfo is None or end.tzinfo is None or fresh.tzinfo is None
                        or not start <= end <= fresh):
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            ref = item["proof_ref"]
            if ref not in closed_refs:
                bounds[ref] = (start, end)
    except (TypeError, ValueError):
        pass
    for ref in invalid:
        bounds.pop(ref, None)
    return bounds


def clear_context(event: Event) -> Event:
    context = event.payload.get("context")
    if not isinstance(context, dict) or context.get("source") != "tern":
        return event
    payload = dict(event.payload)
    payload.pop("context", None)
    payload.pop("agent", None)
    return replace(event, payload=payload)


class ProofIndex:
    """Confirmed bounds and independently validated live leases for one source view."""

    def __init__(self, snapshots: list[Event], raw_meta: str | None) -> None:
        self.bounds = proof_bounds(snapshots, raw_meta)
        self.live_until: dict[str, datetime] = {}
        closed: set[str] = set()
        for event in snapshots:
            items = event.payload.get("closed")
            if isinstance(items, list):
                closed.update(item["proof_ref"] for item in items if isinstance(item, dict)
                              and isinstance(item.get("proof_ref"), str))
        try:
            meta = json.loads(raw_meta or "{}")
            latest = snapshots[-1] if snapshots else None
            if (not isinstance(meta, dict) or latest is None or meta.get("healthy") is not True
                    or meta.get("snapshot_id") != latest.id
                    or meta.get("run_ref") != latest.payload.get("run_ref")
                    or not isinstance(meta.get("scopes"), dict)):
                return
            for item in meta["scopes"].values():
                if not isinstance(item, dict):
                    continue
                ref = item.get("proof_ref")
                if not isinstance(ref, str) or ref in closed or ref not in self.bounds:
                    continue
                start, end = self.bounds[ref]
                fresh = parse_ts(item["fresh_until"])
                if fresh.tzinfo is not None and start <= end <= fresh:
                    self.live_until[ref] = fresh
        except (KeyError, TypeError, ValueError):
            return

    def live(self, ref: object, now: datetime) -> bool:
        if not isinstance(ref, str) or ref not in self.live_until or ref not in self.bounds:
            return False
        start, end = self.bounds[ref]
        return start <= end <= now < self.live_until[ref]

    def confirmed(self, ref: object, at: datetime) -> bool:
        if not isinstance(ref, str) or ref not in self.bounds:
            return False
        start, end = self.bounds[ref]
        return start <= at < end


def _attachment(event: Event, index: ProofIndex, at: datetime, *, live: bool) -> Event:
    context = event.payload.get("context")
    if not isinstance(context, dict) or context.get("source") != "tern":
        return event
    eligible = index.live if live else index.confirmed
    if not eligible(context.get("proof_ref"), at):
        return clear_context(event)
    if "agent" in event.payload and not eligible(context.get("agent_proof_ref"), at):
        payload = dict(event.payload)
        payload.pop("agent", None)
        return replace(event, payload=payload)
    return event


def current_context(event: Event, index: ProofIndex, now: datetime) -> Event:
    if now < _knowledge_floor(event, None):
        return clear_context(event)
    return _attachment(event, index, now, live=True)


@dataclass(frozen=True)
class FocusRecord:
    at: datetime
    raw: Event
    history: Event
    occurrence: Event
    history_eligible: bool
    title_change: bool
    context_change: bool
    barrier: bool


@dataclass(frozen=True)
class FocusProjection:
    records: list[FocusRecord]
    raw: list[Event]
    index: ProofIndex


def focus_observed_at(event: Event, source_ts: str | None = None) -> datetime:
    """Use exact publication when present; retain the coarse raw native fact otherwise."""
    coarse = parse_ts(source_ts or event.ts)
    carried = parse_ts(event.ts)
    try:
        exact = parse_ts(event.payload["_observed_at"])
        if (exact.tzinfo is not None and coarse <= exact < coarse + timedelta(seconds=1)):
            return max(carried, exact)
    except (KeyError, TypeError, ValueError):
        pass
    return carried


def _knowledge_floor(event: Event, source_ts: str | None) -> datetime:
    exact = focus_observed_at(event, source_ts)
    try:
        published = parse_ts(event.payload["_observed_at"])
        source = parse_ts(source_ts or event.ts)
        if published.tzinfo is not None and source <= published < source + timedelta(seconds=1):
            return exact
    except (KeyError, TypeError, ValueError):
        pass
    return max(exact, parse_ts(source_ts or event.ts) + timedelta(seconds=1))


def project_timeline(
    events: list[Event], snapshots: list[Event], raw_meta: str | None, horizon: datetime,
    *, history_ids: set[int] | None = None, index: ProofIndex | None = None,
    source_times: dict[int, str] | None = None,
) -> FocusProjection:
    """Project source proof boundaries and raw facts once, retaining their provenance."""
    index = index or ProofIndex(snapshots, raw_meta)
    source_times = source_times or {}
    ordered = sorted((e for e in events if focus_observed_at(e, source_times.get(e.id)) <= horizon),
                     key=lambda e: (focus_observed_at(e, source_times.get(e.id)), e.id))
    records: list[FocusRecord] = []
    last_ref: object = None
    previous: Event | None = None
    for pos, event in enumerate(ordered):
        begin = focus_observed_at(event, source_times.get(event.id))
        end = (focus_observed_at(ordered[pos + 1], source_times.get(ordered[pos + 1].id))
               if pos + 1 < len(ordered) else horizon)
        context = event.payload.get("context")
        context = context if isinstance(context, dict) and context.get("source") == "tern" else {}
        points = {begin}
        known_from = _knowledge_floor(event, source_times.get(event.id)) if context else begin
        if begin < known_from < end or (pos == len(ordered) - 1 and known_from == end):
            points.add(known_from)
        for ref in (context.get("proof_ref"), context.get("agent_proof_ref")):
            if isinstance(ref, str) and ref in index.bounds:
                points.update(t for t in index.bounds[ref]
                              if begin < t < end or (pos == len(ordered) - 1 and t == end))
        title_change = context_change = True
        semantic_change = False
        previous_context = previous.payload.get("context") if previous is not None else None
        previous_ref = (
            previous_context.get("proof_ref") if isinstance(previous_context, dict) else None
        )
        if previous is not None and event.payload.get("app") == "so.stencil.tern":
            native = ("app", "source", "window_id", "pid")
            same_native = all(event.payload.get(k) == previous.payload.get(k) for k in native)
            if same_native:
                title_change = project_focus(event).title != project_focus(previous).title
                new_ref = context.get("ref")
                semantic_change = (
                    new_ref is not None and last_ref is not None and new_ref != last_ref
                )
                context_change = semantic_change
                title_change = title_change or semantic_change
        proof_ref = context.get("proof_ref")
        if context.get("ref") is not None and isinstance(proof_ref, str):
            if proof_ref in index.bounds:
                last_ref = context["ref"]
        for at in sorted(points):
            raw_boundary = at == begin
            stamped = replace(event, ts=to_z_precise(at))
            history = _attachment(stamped, index, at, live=False)
            occurrence = _attachment(stamped, index, at, live=False)
            ref = context.get("proof_ref")
            if (isinstance(ref, str) and index.live(ref, horizon) and ref in index.bounds
                    and at >= index.bounds[ref][0]):
                occurrence = _attachment(stamped, index, horizon, live=True)
                agent_ref = context.get("agent_proof_ref")
                if (isinstance(agent_ref, str) and agent_ref in index.bounds
                        and at < index.bounds[agent_ref][0]):
                    payload = dict(occurrence.payload)
                    payload.pop("agent", None)
                    occurrence = replace(occurrence, payload=payload)
            if context and at < known_from:
                history = clear_context(history)
                occurrence = clear_context(occurrence)
            barrier = (raw_boundary and previous is not None and previous_ref is not None
                       and proof_ref != previous_ref and not index.live(previous_ref, horizon))
            records.append(FocusRecord(
                at, event, history, occurrence, history_ids is None or event.id in history_ids,
                (raw_boundary and title_change) or (at == known_from > begin and semantic_change),
                (raw_boundary and context_change) or (at == known_from > begin and semantic_change),
                barrier,
            ))
        previous = event
    return FocusProjection(records, ordered, index)


def switch_metrics(projection: FocusProjection, fine: list, attention: list) -> tuple[int, int]:
    """Count human deltas at projected boundaries, not proof-only changes."""
    from itertools import pairwise

    from roxabi_sense.report.segments import attention_key

    boundaries: dict[datetime, list[FocusRecord]] = {}
    for record in projection.records:
        if record.history_eligible:
            boundaries.setdefault(record.at, []).append(record)

    def allowed(stamp: str, *, context: bool) -> bool:
        records = boundaries.get(parse_ts(stamp))
        if records is None:
            return True
        return any(r.context_change if context else r.title_change for r in records)

    titles = sum(a.end == b.start and allowed(b.start, context=False)
                 for a, b in pairwise(fine))
    contexts = sum(a.end == b.start and attention_key(a) != attention_key(b)
                   and allowed(b.start, context=True) for a, b in pairwise(attention))
    return titles, contexts
