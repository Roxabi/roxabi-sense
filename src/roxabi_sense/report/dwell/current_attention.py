"""Resolve a current attention run from validated source and raw observations."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from roxabi_sense.report.away import AwaySegment
from roxabi_sense.report.dwell.coverage import coverage_cuts
from roxabi_sense.report.dwell.projection import project_focus
from roxabi_sense.report.segments import MIN_DWELL_S, FocusSegment, attention_key
from roxabi_sense.store import Event
from roxabi_sense.util.time import parse_ts, to_z


@dataclass(frozen=True)
class CurrentStretch:
    start: str
    end: str
    duration_s: float


@dataclass(frozen=True)
class CurrentAttention:
    quality: str
    app: str | None = None
    source: str | None = None
    window_id: str | None = None
    coverage_started_at: str | None = None
    context_started_at: str | None = None
    confirmed_through: str | None = None
    stretch: CurrentStretch | None = None


def _key(event: Event) -> tuple[str, ...]:
    fields = project_focus(event)
    return attention_key(FocusSegment(
        start=event.ts, end=event.ts, duration_s=0, app=fields.app, title=fields.title,
        cwd=fields.cwd, agent=fields.agent, pid=fields.pid, session_id=fields.session_id,
        agent_pid=fields.agent_pid, agent_match=fields.agent_match, window_id=fields.window_id,
    ))


def resolve_current_attention(
    *, current: Event | None, quality: str, focus: list[Event], coverage: list[Event],
    attention: list[FocusSegment], away: list[AwaySegment], day_start: datetime,
    now: datetime, raw_observation: str | None,
) -> CurrentAttention:
    """Resolve one run; coverage freshness validates now, never extends dwell."""
    if current is None or quality == "unknown" or parse_ts(current.ts) > now:
        return CurrentAttention(quality="unknown")
    fields = project_focus(current)
    key = _key(current)
    focus = sorted((e for e in focus if parse_ts(e.ts) <= now), key=lambda e: (e.ts, e.id))
    coverage = sorted((e for e in coverage if parse_ts(e.ts) <= now), key=lambda e: (e.ts, e.id))
    context_floor = day_start
    previous_key: tuple[str, ...] | None = None
    for event in focus:
        event_key = _key(event)
        if event_key != previous_key:
            context_floor = max(day_start, parse_ts(event.ts))
        previous_key = event_key
    if previous_key != key:
        return CurrentAttention(quality=quality, app=fields.app, source=fields.source,
                                window_id=fields.window_id)

    confirmed: datetime | None = None
    if quality == "observed":
        try:
            observation = json.loads(raw_observation or "{}")
            confirmed = min(now, parse_ts(observation["last_observed_at"]))
        except (KeyError, TypeError, ValueError):
            return CurrentAttention(quality="unknown")
    end = confirmed if confirmed is not None else now
    floor = max(day_start, parse_ts(coverage[0].ts)) if coverage else day_start
    for index, event in enumerate(coverage):
        if index:
            previous = coverage[index - 1]
            if (event.payload.get("source"), event.payload.get("state")) != (
                previous.payload.get("source"), previous.payload.get("state")
            ):
                floor = max(floor, parse_ts(event.ts))
    cuts = coverage_cuts(coverage, focus, now)
    cuts.extend((parse_ts(a.start), parse_ts(a.end)) for a in away)
    blocked = False
    for begin, finish in cuts:
        if begin < end and finish > begin:
            if finish >= end:
                blocked = True
            else:
                floor = max(floor, finish)
    floor = max(floor, day_start)
    selected = None
    if not blocked:
        for segment in reversed(attention):
            if attention_key(segment) != key:
                continue
            segment_start = max(parse_ts(segment.start), context_floor, floor)
            segment_end = min(parse_ts(segment.end), end)
            duration = (segment_end - segment_start).total_seconds()
            if duration >= MIN_DWELL_S:
                if quality != "legacy_unverified" or (now - segment_end).total_seconds() <= 120:
                    selected = CurrentStretch(to_z(segment_start), to_z(segment_end), duration)
            break
    return CurrentAttention(
        quality=quality, app=fields.app, source=fields.source, window_id=fields.window_id,
        coverage_started_at=to_z(floor),
        context_started_at=to_z(context_floor),
        confirmed_through=to_z(confirmed) if confirmed is not None else None,
        stretch=selected,
    )
