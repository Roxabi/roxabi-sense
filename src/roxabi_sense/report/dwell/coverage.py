"""One native coverage view for historical credit and continuity barriers."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from roxabi_sense.store import Event
from roxabi_sense.util.time import parse_ts


def _publication(event: Event) -> datetime:
    coarse = parse_ts(event.ts)
    value = event.payload.get("_observed_at")
    if isinstance(value, str):
        try:
            exact = parse_ts(value)
            if exact.tzinfo is not None and coarse <= exact < coarse + timedelta(seconds=1):
                return exact
        except ValueError:
            pass
    return coarse


@dataclass(frozen=True)
class NativeSpan:
    event: Event
    start: datetime
    end: datetime


@dataclass(frozen=True)
class NativeExclusion:
    start: datetime
    end: datetime
    reason: str  # identity and tail censor history only; hard also breaks continuity


@dataclass(frozen=True)
class NativeConfirmation:
    """Source spans and reasoned exclusions; source events are never rewritten."""
    spans: list[NativeSpan]
    exclusions: list[NativeExclusion]
    confirmed_through: datetime | None

    @property
    def observed(self) -> list[tuple[datetime, datetime]]:
        return [(s.start, s.end) for s in self.spans
                if s.event.payload.get("state") == "observed" and s.start < s.end]


def _ack(event: Event, epoch: Event, horizon: datetime) -> datetime | None:
    publication = event.payload.get("_observed_at")
    observed = event.payload.get("_native_observed_at")
    if not isinstance(publication, str) or not isinstance(observed, str):
        return None
    try:
        pub, ack, coarse = parse_ts(publication), parse_ts(observed), parse_ts(event.ts)
        if (pub.tzinfo is not None and ack.tzinfo is not None
                and coarse <= pub < coarse + timedelta(seconds=1)
                and parse_ts(epoch.ts) <= ack <= pub <= horizon):
            return ack
    except ValueError:
        pass
    return None


def native_confirmation(
    coverage: list[Event], focus: list[Event], raw_meta: str | None,
    overall_id: int | None, horizon: datetime, *,
    keys: dict[int, tuple[str, ...]] | None = None,
) -> NativeConfirmation:
    """Bind raw focus to native epochs and distinguish identity censoring from loss."""
    keys = keys or {}
    epochs = sorted((e for e in coverage if parse_ts(e.ts) <= horizon), key=lambda e: e.id)
    matching: list[list[Event]] = []
    ordered_focus = sorted(focus, key=lambda event: event.id)
    position = 0
    for i, epoch in enumerate(epochs):
        following = epochs[i + 1].id if i + 1 < len(epochs) else None
        candidates = []
        while position < len(ordered_focus) and ordered_focus[position].id <= epoch.id:
            position += 1
        while position < len(ordered_focus):
            event = ordered_focus[position]
            if following is not None and event.id >= following:
                break
            position += 1
            owner = event.payload.get("_native_coverage_id")
            if (event.payload.get("source") != epoch.payload.get("source")
                    or (epoch.payload.get("window_id")
                        and event.payload.get("window_id") != epoch.payload.get("window_id"))
                    or (owner is not None and (type(owner) is not int or owner != epoch.id))
                    or not parse_ts(epoch.ts) <= _publication(event) <= horizon):
                continue
            if owner is not None and _ack(event, epoch, horizon) is None:
                continue
            candidates.append(event)
        matching.append(sorted(candidates, key=lambda e: (_publication(e), e.id)))

    try:
        meta = json.loads(raw_meta or "{}")
    except (TypeError, ValueError):
        meta = {}
    starts = [parse_ts(e.ts) for e in epochs]
    for i, epoch in enumerate(epochs):
        if i and epoch.payload.get("state") == "observed" and matching[i]:
            starts[i] = max(starts[i], _publication(matching[i][0]))
    ends = list(starts)
    current: datetime | None = None
    for i, epoch in enumerate(epochs):
        if epoch.payload.get("state") != "observed":
            continue
        following = epochs[i + 1] if i + 1 < len(epochs) else None
        hard_end = horizon
        if following is not None and following.payload.get("state") == "observed":
            hard_end = min(hard_end, starts[i + 1])
        elif following is not None and i + 2 < len(epochs):
            hard_end = min(hard_end, starts[i + 2])
        end = min(horizon, parse_ts(following.ts)) if following is not None else parse_ts(epoch.ts)
        for event in matching[i]:
            ack = _ack(event, epoch, horizon)
            if ack is not None and _publication(event) <= hard_end:
                end = max(end, ack)
        if (following is None and epoch.id == overall_id and isinstance(meta, dict)
                and meta.get("healthy") is True and type(meta.get("coverage_id")) is int
                and meta.get("coverage_id") == epoch.id
                and meta.get("source") == epoch.payload.get("source")
                and meta.get("window_id") == epoch.payload.get("window_id")
                and isinstance(meta.get("last_observed_at"), str)
                and isinstance(meta.get("fresh_until"), str)):
            try:
                confirmed, fresh = parse_ts(meta["last_observed_at"]), parse_ts(meta["fresh_until"])
                if (confirmed.tzinfo is not None and fresh.tzinfo is not None
                        and parse_ts(epoch.ts) <= confirmed <= min(horizon, fresh)):
                    end = max(end, confirmed)
            except ValueError:
                pass
        ends[i] = min(end, hard_end)
        if following is None:
            current = ends[i]

    spans: list[NativeSpan] = []
    exclusions: list[NativeExclusion] = []
    for i, epoch in enumerate(epochs):
        state = epoch.payload.get("state")
        next_start = starts[i + 1] if i + 1 < len(epochs) else horizon
        if i and state in {"observed", "legacy_unverified"}:
            previous = epochs[i - 1]
            if (previous.payload.get("state") != state
                    or previous.payload.get("source") != epoch.payload.get("source")):
                boundary = (max(starts[i], _publication(matching[i][0]))
                            if matching[i] else starts[i])
                exclusions.append(NativeExclusion(boundary, boundary, "hard"))
        if state == "observed":
            start = starts[i]
            if matching[i]:
                start = max(start, _publication(matching[i][0]))
            else:
                start = max(start, ends[i])
            if start > starts[i]:
                exclusions.append(NativeExclusion(starts[i], start, "hard"))
            spans.append(NativeSpan(epoch, start, max(start, ends[i])))
            if i + 1 < len(epochs) and epochs[i + 1].payload.get("state") == "observed":
                next_epoch = epochs[i + 1]
                same_key = (matching[i] and matching[i + 1]
                            and keys.get(matching[i][-1].id) is not None
                            and keys.get(matching[i][-1].id) == keys.get(matching[i + 1][0].id))
                reason = ("identity" if same_key
                          and epoch.payload.get("source") == next_epoch.payload.get("source")
                          else "hard")
                exclusions.append(NativeExclusion(ends[i], next_start, reason))
            elif i + 1 == len(epochs):
                exclusions.append(NativeExclusion(ends[i], horizon, "tail"))
        elif state == "legacy_unverified":
            spans.append(NativeSpan(epoch, starts[i], next_start))
        else:
            begin = (ends[i - 1] if i and epochs[i - 1].payload.get("state") == "observed"
                     else starts[i])
            exclusions.append(NativeExclusion(begin, max(begin, next_start), "hard"))
    return NativeConfirmation(spans, exclusions, current)


def identity_cuts(
    native: NativeConfirmation, source: str | None, window_id: str | None
) -> list[tuple[datetime, datetime]]:
    return [
        (span.start, span.end) for span in native.spans
        if span.event.payload.get("source") != source
        or (span.event.payload.get("window_id")
            and span.event.payload.get("window_id") != window_id)
    ]
