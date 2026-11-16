"""Coverage exclusions for historical focus dwell."""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from roxabi_sense.store import Event
from roxabi_sense.util.time import parse_ts, to_z_precise


@dataclass(frozen=True)
class NativeConfirmation:
    """Actual coverage transitions, bounded history, and current native confirmation."""
    transitions: list[Event]
    history: list[Event]
    confirmed_through: datetime | None


def native_confirmation(
    coverage: list[Event], focus: list[Event], raw_meta: str | None,
    overall_id: int | None, horizon: datetime,
) -> NativeConfirmation:
    """Combine only causal, epoch-bound native ACKs and matching native metadata."""
    epochs = sorted((event for event in coverage if parse_ts(event.ts) <= horizon),
                    key=lambda event: event.id)
    focus_by_epoch: dict[int, list[Event]] = {}
    for event in focus:
        owner = event.payload.get("_native_coverage_id")
        if type(owner) is int:
            focus_by_epoch.setdefault(owner, []).append(event)
    try:
        meta = json.loads(raw_meta or "{}")
    except (TypeError, ValueError):
        meta = {}
    transitions = list(epochs)
    current: datetime | None = None
    for i, epoch in enumerate(epochs):
        if epoch.payload.get("state") != "observed":
            continue
        start = parse_ts(epoch.ts)
        following = epochs[i + 1] if i + 1 < len(epochs) else None
        hard_end = horizon
        if following is not None and following.payload.get("state") == "observed":
            hard_end = min(hard_end, parse_ts(following.ts))
        elif following is not None and i + 2 < len(epochs):
            hard_end = min(hard_end, parse_ts(epochs[i + 2].ts))
        end = min(horizon, parse_ts(following.ts)) if following is not None else start
        for event in focus_by_epoch.get(epoch.id, []):
            if (event.id <= epoch.id or (following is not None and event.id >= following.id)
                    or not epoch.payload.get("source") or not epoch.payload.get("window_id")
                    or event.payload.get("source") != epoch.payload.get("source")
                    or event.payload.get("window_id") != epoch.payload.get("window_id")
                    or event.payload.get("_native_coverage_id") != epoch.id):
                continue
            if (not isinstance(event.payload.get("_observed_at"), str)
                    or not isinstance(event.payload.get("_native_observed_at"), str)):
                continue
            try:
                coarse = parse_ts(event.ts)
                publication = parse_ts(event.payload["_observed_at"])
                observed = parse_ts(event.payload["_native_observed_at"])
                if (publication.tzinfo is None or observed.tzinfo is None
                        or not coarse <= publication < coarse + timedelta(seconds=1)
                        or not start <= observed <= publication <= horizon
                        or observed > hard_end):
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            end = max(end, observed)
        if (following is None and epoch.id == overall_id and isinstance(meta, dict)
                and meta.get("healthy") is True and type(meta.get("coverage_id")) is int
                and meta.get("coverage_id") == epoch.id
                and meta.get("source") == epoch.payload.get("source")
                and meta.get("window_id") == epoch.payload.get("window_id")
                and isinstance(meta.get("last_observed_at"), str)
                and isinstance(meta.get("fresh_until"), str)):
            try:
                confirmed = parse_ts(meta["last_observed_at"])
                fresh = parse_ts(meta["fresh_until"])
                if (confirmed.tzinfo is not None and fresh.tzinfo is not None
                        and start <= confirmed <= min(horizon, fresh)):
                    end = max(end, confirmed)
            except (KeyError, TypeError, ValueError):
                pass
        end = min(end, hard_end)
        if following is not None and following.payload.get("state") != "observed":
            transitions[i + 1] = replace(following, ts=to_z_precise(end))
        elif following is None:
            current = end
    transitions.sort(key=lambda event: (parse_ts(event.ts), event.id))
    history = list(transitions)
    if epochs and epochs[-1].payload.get("state") == "observed" and current is not None:
        last = epochs[-1]
        history.append(Event(id=last.id + 1, ts=to_z_precise(current), kind="focus_coverage",
                             payload={"state": "unknown", "source": last.payload.get("source")}))
        history.sort(key=lambda event: (parse_ts(event.ts), event.id))
    return NativeConfirmation(transitions, history, current)


def coverage_cuts(
    coverage: list[Event], focus: list[Event], horizon: datetime
) -> list[tuple[datetime, datetime]]:
    cuts: list[tuple[datetime, datetime]] = []
    for i, event in enumerate(coverage):
        start = parse_ts(event.ts)
        end = parse_ts(coverage[i + 1].ts) if i + 1 < len(coverage) else horizon
        if event.payload.get("state") == "legacy_unverified":
            continue
        if event.payload.get("state") != "observed":
            cuts.append((start, end))
            continue
        previous_focus = next((f for f in reversed(focus) if parse_ts(f.ts) <= start), None)
        window_id = event.payload.get("window_id")
        matches = (
            previous_focus is not None
            and previous_focus.payload.get("source") == event.payload.get("source")
            and (not window_id or previous_focus.payload.get("window_id") == window_id)
        )
        previous = coverage[i - 1] if i else None
        recovering = bool(previous and (
            previous.payload.get("state") != "observed"
            or previous.payload.get("source") != event.payload.get("source")
            or previous.payload.get("window_id") != window_id
        ))
        if recovering or not matches:
            matching = [
                parse_ts(f.ts) for f in focus
                if start <= parse_ts(f.ts) < end
                and f.payload.get("source") == event.payload.get("source")
                and (not window_id or f.payload.get("window_id") == window_id)
            ]
            cuts.append((start, min(matching, default=end)))
    return cuts


def identity_cuts(
    coverage: list[Event], horizon: datetime, source: str | None, window_id: str | None
) -> list[tuple[datetime, datetime]]:
    return [
        (parse_ts(event.ts), parse_ts(coverage[i + 1].ts) if i + 1 < len(coverage) else horizon)
        for i, event in enumerate(coverage)
        if event.payload.get("state") in {"observed", "legacy_unverified"}
        and (
            event.payload.get("source") != source
            or (event.payload.get("window_id") and event.payload.get("window_id") != window_id)
        )
    ]
