"""Resolve a current attention run from validated source and raw observations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from roxabi_sense.report.away import AwaySegment
from roxabi_sense.report.dwell.coverage import NativeConfirmation
from roxabi_sense.report.dwell.projection import project_focus
from roxabi_sense.report.dwell.tern_proofs import FocusProjection
from roxabi_sense.report.segments import MIN_DWELL_S, FocusSegment, attention_key
from roxabi_sense.store import Event
from roxabi_sense.util.time import parse_ts, to_z_precise


@dataclass(frozen=True)
class CurrentStretch:
    """Credited union; duration may be shorter than the start/end envelope."""
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


def focus_key(event: Event) -> tuple[str, ...]:
    fields = project_focus(event)
    return attention_key(FocusSegment(
        start=event.ts, end=event.ts, duration_s=0, app=fields.app, title=fields.title,
        cwd=fields.cwd, agent=fields.agent, pid=fields.pid, session_id=fields.session_id,
        agent_pid=fields.agent_pid, agent_match=fields.agent_match, window_id=fields.window_id,
        context_ref=fields.context_ref, repo_ref=fields.repo_ref,
    ))


def resolve_current_attention(
    *, current: Event | None, quality: str, focus: FocusProjection, native: NativeConfirmation,
    attention: list[FocusSegment], away: list[AwaySegment], day_start: datetime, now: datetime,
) -> CurrentAttention:
    """Resolve one run; coverage freshness validates now, never extends dwell."""
    if current is None or quality == "unknown" or parse_ts(current.ts) > now:
        return CurrentAttention(quality="unknown")
    fields = project_focus(current)
    key = focus_key(current)
    records = [record for record in focus.records if record.at <= now]
    context_floor = day_start
    previous_key: tuple[str, ...] | None = None
    for record in records:
        event_key = focus_key(record.occurrence)
        if event_key != previous_key or record.barrier:
            context_floor = max(day_start, record.at)
        previous_key = event_key
    if previous_key != key:
        return CurrentAttention(quality=quality, app=fields.app, source=fields.source,
                                window_id=fields.window_id)

    confirmed = native.confirmed_through if quality == "observed" else None
    if quality == "observed" and confirmed is None:
        return CurrentAttention(quality="unknown")
    context = current.payload.get("context")
    if isinstance(context, dict) and context.get("source") == "tern":
        ref = context.get("proof_ref")
        if isinstance(ref, str) and ref in focus.index.bounds:
            cap = focus.index.bounds[ref][1]
            confirmed = min(confirmed, cap) if confirmed is not None else cap
    end = confirmed if confirmed is not None else now
    floor = max(day_start, native.spans[0].start) if native.spans else day_start
    cuts = [(cut.start, cut.end) for cut in native.exclusions if cut.reason == "hard"]
    cuts.extend((parse_ts(a.start), parse_ts(a.end)) for a in away)
    blocked = False
    for begin, finish in cuts:
        if begin <= end:
            if begin < end < finish:
                blocked = True
            elif finish <= end:
                floor = max(floor, finish)
    floor = max(floor, day_start)
    pieces = sorted(
        (max(parse_ts(segment.start), context_floor, floor), min(parse_ts(segment.end), end))
        for segment in attention if attention_key(segment) == key
    ) if not blocked else []
    union: list[tuple[datetime, datetime]] = []
    for start, finish in pieces:
        if finish <= start:
            continue
        if union and start <= union[-1][1]:
            union[-1] = (union[-1][0], max(union[-1][1], finish))
        else:
            union.append((start, finish))
    duration = sum((finish - start).total_seconds() for start, finish in union)
    selected = None
    if (union and duration >= MIN_DWELL_S
            and (quality != "legacy_unverified" or (now - union[-1][1]).total_seconds() <= 120)):
        selected = CurrentStretch(to_z_precise(union[0][0]), to_z_precise(union[-1][1]), duration)
    return CurrentAttention(
        quality=quality, app=fields.app, source=fields.source, window_id=fields.window_id,
        coverage_started_at=to_z_precise(floor),
        context_started_at=to_z_precise(context_floor),
        confirmed_through=to_z_precise(confirmed) if confirmed is not None else None,
        stretch=selected,
    )
