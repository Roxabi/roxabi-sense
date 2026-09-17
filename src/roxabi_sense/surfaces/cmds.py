"""CLI command handlers — format only over store / report / query."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from roxabi_sense.config import SenseConfig
from roxabi_sense.query import SenseQuery
from roxabi_sense.report import (
    compile_day_recap,
    format_day_recap,
    format_day_recap_share,
    summarize_event,
)
from roxabi_sense.store import DEFAULT_DAY_LIMIT, Store, clamp_event_limit


def cmd_care_brief(cfg: SenseConfig, *, day: str | None) -> int:
    body = SenseQuery.from_config(cfg).care_brief(day)
    print(json.dumps(body, ensure_ascii=False, indent=2))
    return 0 if body.get("db_exists") else 1


def cmd_day(db_path: Path, *, day: str | None, as_json: bool, limit: int) -> int:
    if not db_path.is_file():
        print(f"db: missing ({db_path})", file=sys.stderr)
        return 1
    lim = clamp_event_limit(limit, default=DEFAULT_DAY_LIMIT)
    try:
        with Store(db_path) as store:
            start, end = store.day_bounds(day)
            events = store.events_for_day(day, limit=lim)
    except ValueError as exc:
        print(f"sense day: {exc}", file=sys.stderr)
        return 2
    if as_json:
        for e in events:
            row = {"ts": e.ts, "kind": e.kind, "payload": e.payload}
            print(json.dumps(row, ensure_ascii=False))
    else:
        print(f"sense day ({day or 'today'})  {start} → {end}  n={len(events)}")
        for e in events:
            print(f"{e.ts}  {e.kind:24}  {summarize_event(e.kind, e.payload)}")
        if len(events) >= lim:
            msg = f"(capped at {lim}; use `sense recap` for a compiled day summary)"
            print(msg, file=sys.stderr)
    return 0


def cmd_recap(
    db_path: Path,
    *,
    day: str | None,
    as_json: bool,
    share: bool = False,
) -> int:
    if not db_path.is_file():
        print(f"db: missing ({db_path})", file=sys.stderr)
        return 1
    try:
        with Store(db_path) as store:
            recap = compile_day_recap(store, day)
    except ValueError as exc:
        print(f"sense recap: {exc}", file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps(recap.to_dict(), ensure_ascii=False, indent=2))
    elif share:
        print(format_day_recap_share(recap))
    else:
        print(format_day_recap(recap))
    return 0
