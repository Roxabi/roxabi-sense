"""Compile bounded, source-scoped meeting observations without exposing identities."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from roxabi_sense.report.meetings.sessions import (
    MeetingSession,
    meeting_sessions,
    sessions_from_samples,
)
from roxabi_sense.report.meetings.signals import meeting_samples
from roxabi_sense.store import Event, Store
from roxabi_sense.store.meeting_protocol import (
    CONTINUITY_SECONDS,
    validate_snapshot,
    validate_sources,
)
from roxabi_sense.util.time import parse_ts, to_z

GAP = timedelta(seconds=CONTINUITY_SECONDS)
LIMIT = 50_000
Interval = tuple[datetime, datetime]


def union(intervals: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(end, out[-1][1]))
        else:
            out.append((start, end))
    return out


def seconds(intervals: list[Interval]) -> float:
    return sum((b - a).total_seconds() for a, b in union(intervals))


def intersect(left: list[Interval], right: list[Interval]) -> list[Interval]:
    a, b = union(left), union(right)
    i = j = 0
    result: list[Interval] = []
    while i < len(a) and j < len(b):
        start, end = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if start < end:
            result.append((start, end))
        if a[i][1] <= b[j][1]:
            i += 1
        else:
            j += 1
    return result


def subtract(left: list[Interval], right: list[Interval]) -> list[Interval]:
    result: list[Interval] = []
    cuts = union(right)
    for start, end in union(left):
        cursor = start
        for a, b in cuts:
            if b <= cursor:
                continue
            if a >= end:
                break
            if a > cursor:
                result.append((cursor, min(a, end)))
            cursor = max(cursor, b)
            if cursor >= end:
                break
        if cursor < end:
            result.append((cursor, end))
    return result


def _registry(payload: dict[str, Any]) -> set[str] | None:
    if not validate_sources(payload):
        return None
    return set(payload["sources"])


def _snapshot(payload: dict[str, Any]) -> tuple[str, str, int, bool, dict[str, str]] | None:
    if not validate_snapshot(payload):
        return None
    source = payload["source_id"]
    epoch = payload["epoch"]
    seq = payload["seq"]
    complete = payload["complete"]
    states = {tab["tab_id"]: tab["state"] for tab in payload["tabs"]}
    return source, epoch, seq, complete, states


def compile_observations(
    events: list[Event], start: datetime, end: datetime,
) -> tuple[list[Interval], list[Interval], str, bool]:
    """Return observed joined union, covered union, live state and coverage."""
    registry: set[str] | None = None
    previous: dict[str, tuple[datetime, str, int, bool, dict[str, str]]] = {}
    epochs: dict[str, set[str]] = {}
    observed: list[Interval] = []
    covered: list[Interval] = []
    source_coverage: dict[str, list[Interval]] = {}
    registry_spans: list[tuple[datetime, datetime, set[str]]] = []
    registry_start = start
    for event in sorted(events, key=lambda e: (e.ts, e.id)):
        ts = parse_ts(event.ts)
        if ts > end:
            break
        if event.kind == 'meeting_sources':
            if registry and registry_start < ts:
                registry_spans.append((registry_start, ts, registry))
            new = _registry(event.payload)
            if new != registry:
                previous = {
                    source: value for source, value in previous.items()
                    if new is not None and source in new
                    and registry is not None and source in registry
                }
            registry, registry_start = new, max(start, ts)
            continue
        if event.kind != 'meeting_snapshot':
            continue
        snap = _snapshot(event.payload)
        if snap is None:
            previous.clear()
            continue
        source, epoch, seq, complete, states = snap
        if not registry or source not in registry:
            continue
        prev = previous.get(source)
        seen = epochs.setdefault(source, set())
        valid = True
        if prev and epoch == prev[1]:
            valid = seq == prev[2] + 1
        elif epoch in seen:
            valid = False
        else:
            valid = not seen or seq == 0
            seen.add(epoch)
            # A bounded query can begin in the middle of an existing epoch.
        if not valid:
            previous.pop(source, None)
            continue
        if prev and epoch == prev[1] and ts > prev[0] and ts - prev[0] <= GAP:
            a, b = max(start, prev[0]), min(end, ts)
            if a < b and prev[3] and complete:
                for tab, state in prev[4].items():
                    if state == 'joined' and (
                        states.get(tab) in ('joined', 'not_joined') or tab not in states
                    ):
                        observed.append((a, b))
                if 'unknown' not in prev[4].values() and 'unknown' not in states.values():
                    source_coverage.setdefault(source, []).append((a, b))
        previous[source] = (ts, epoch, seq, complete, states)
    if registry and registry_start < end:
        registry_spans.append((registry_start, end, registry))
    for a, b, sources in registry_spans:
        common = [(a, b)]
        for source in sources:
            common = intersect(common, source_coverage.get(source, []))
        covered.extend(common)
    live = 'unknown'
    known = False
    if registry:
        fresh = [previous.get(source) for source in registry]
        known = all(p is not None and p[3] and end - p[0] <= GAP
                    and 'unknown' not in p[4].values() for p in fresh)
        if any(p and p[3] and end - p[0] <= GAP and 'joined' in p[4].values() for p in fresh):
            live = 'in_call'
        elif known:
            live = 'not_joined'
    return union(observed), union(covered), live, known


def compile_day_meetings(
    store: Store, events: list[Event], start: datetime, end: datetime,
) -> tuple[list[MeetingSession], list[MeetingSession], list[Interval], list[Interval], bool]:
    lookbehind = to_z(start - timedelta(seconds=308))
    prior = store.events_between(lookbehind, to_z(start), limit=LIMIT)
    registry = store.last_by_kind_before("meeting_sources", lookbehind)
    combined = ([registry] if registry is not None else []) + prior + events
    truncated = len(events) >= LIMIT or len(prior) >= LIMIT
    observed, coverage, _, _ = compile_observations(combined, start, end)
    if truncated:
        coverage = []
    inferred = [
        replace(
            m, start=to_z(max(start, parse_ts(m.start))), end=to_z(min(end, parse_ts(m.end))),
            duration_s=(min(end, parse_ts(m.end)) - max(start, parse_ts(m.start))).total_seconds(),
        )
        for m in meeting_sessions(combined, horizon=end)
        if parse_ts(m.end) > start and parse_ts(m.start) < end
    ]
    sessions = sorted(observed_sessions(observed) + inferred, key=lambda m: m.start)
    return sessions, inferred, observed, coverage, truncated


def meeting_annotation_for_store(store: Store, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    start = now - timedelta(seconds=308)
    events = store.events_between(to_z(start), to_z(now + timedelta(seconds=1)), limit=LIMIT)
    registry = store.last_by_kind_before('meeting_sources', to_z(start))
    if registry is not None:
        events.insert(0, registry)
    _, _, live, known = compile_observations(events, start, now)
    if len(events) >= LIMIT:
        live, known = 'unknown', False
    inferred = sessions_from_samples(meeting_samples(events), horizon=now)
    active = next(
        (s for s in reversed(inferred) if parse_ts(s.start) <= now <= parse_ts(s.end)), None
    )
    return {'phase': live, 'inferred_phase': active.phase if active else None,
            'coverage': 'known' if known else 'unknown',
            'fidelity': 'observed' if known else 'partial' if live == 'in_call' else 'unknown',
            'fidelity_note': 'Chrome Google Meet observations only; unconfirmed states are unknown.'
            }


def observed_sessions(intervals: list[Interval]) -> list[MeetingSession]:
    return [MeetingSession(start=to_z(a), end=to_z(b), duration_s=(b-a).total_seconds(),
                           provider='meet', label='meeting', phase='in_call', evidence='observed')
            for a, b in union(intervals)]
