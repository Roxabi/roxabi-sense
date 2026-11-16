"""Historical proof applicability, distinct from confirmed duration and live health."""
from __future__ import annotations

from datetime import datetime, timedelta

from roxabi_sense.store import Event
from roxabi_sense.util.time import parse_ts


def closed_eligibility(
    snapshots: list[Event], bounds: dict[str, tuple[datetime, datetime]],
) -> dict[str, datetime]:
    """Accept only consistent closed deadlines bounded by their actual publication."""
    values: dict[str, datetime | None] = {}
    for event in snapshots:
        closed = event.payload.get("closed")
        if not isinstance(closed, list):
            continue
        for item in closed:
            if not isinstance(item, dict) or not isinstance(item.get("proof_ref"), str):
                continue
            ref = item["proof_ref"]
            value = None
            try:
                if ref in bounds and isinstance(item.get("eligible_until"), str):
                    until = parse_ts(item["eligible_until"])
                    published = parse_ts(event.payload["_published_at"])
                    coarse = parse_ts(event.ts)
                    if (until.tzinfo is not None and published.tzinfo is not None
                            and coarse <= published < coarse + timedelta(seconds=1)
                            and bounds[ref][1] <= until <= published):
                        value = until
            except (KeyError, TypeError, ValueError, AttributeError):
                pass
            if ref in values and values[ref] != value:
                values[ref] = None
            else:
                values[ref] = value
    return {ref: value for ref, value in values.items() if value is not None}


def is_applicable(
    bounds: dict[str, tuple[datetime, datetime]], deadlines: dict[str, datetime],
    ref: object, at: datetime,
) -> bool:
    if not isinstance(ref, str) or ref not in bounds:
        return False
    start, confirmed = bounds[ref]
    return start <= at < deadlines.get(ref, confirmed)
