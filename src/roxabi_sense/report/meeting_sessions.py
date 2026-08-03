"""Continuous meeting sessions (parallel track to focus) — ADR-004."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from roxabi_sense.report.meeting import (
    MeetingHint,
    MeetingPhase,
    meeting_samples,
)
from roxabi_sense.store import Event
from roxabi_sense.util.time import to_z


@dataclass(frozen=True)
class MeetingSession:
    """Continuous meeting surface span (does not replace focus dwell)."""

    start: str
    end: str
    duration_s: float
    provider: str
    label: str
    phase: MeetingPhase
    call_id: str | None = None


def meeting_sessions(
    events: list[Event],
    *,
    horizon: datetime,
) -> list[MeetingSession]:
    """
    Build continuous meeting sessions from the shared evidence stream.

    - Non-meeting focus does **not** end a session (multitask during call).
    - Desktop snapshot without a meeting window clears the session.
    - Phase changes (`in_call` ↔ `tab_open`) split segments.
    """
    return sessions_from_samples(meeting_samples(events), horizon=horizon)


def sessions_from_samples(
    samples: list[tuple[datetime, MeetingHint | None]],
    *,
    horizon: datetime,
) -> list[MeetingSession]:
    """Pure session fold over samples (shared with tests / future live path)."""
    if not samples:
        return []

    out: list[MeetingSession] = []
    cur: MeetingHint | None = None
    cur_start: datetime | None = None

    def close(end: datetime) -> None:
        nonlocal cur, cur_start
        if cur is None or cur_start is None or end <= cur_start:
            cur = None
            cur_start = None
            return
        out.append(
            MeetingSession(
                start=to_z(cur_start),
                end=to_z(end),
                duration_s=(end - cur_start).total_seconds(),
                provider=cur.provider,
                label=cur.label,
                phase=cur.phase,
                call_id=cur.call_id,
            )
        )
        cur = None
        cur_start = None

    for ts, hint in samples:
        if cur is None:
            if hint is not None:
                cur = hint
                cur_start = ts
            continue
        if hint is None:
            close(ts)
            continue
        # Split only on phase/provider change, or known call_id conflict.
        # None→id (or id→None) upgrades in place — ADR-004 / spec edge.
        if hint.phase != cur.phase or hint.provider != cur.provider:
            close(ts)
            cur = hint
            cur_start = ts
            continue
        if (
            cur.call_id
            and hint.call_id
            and cur.call_id != hint.call_id
        ):
            close(ts)
            cur = hint
            cur_start = ts
            continue
        if hint.call_id and not cur.call_id:
            cur = hint
        elif hint.phase == "in_call" and len(hint.label) > len(cur.label):
            cur = hint

    if cur is not None and cur_start is not None:
        close(horizon if horizon > cur_start else cur_start)

    return out


def format_meeting_sessions(
    sessions: list[MeetingSession],
    *,
    in_call_s: float,
    tab_open_s: float,
    fmt_dur,
    local_hm,
    limit: int = 12,
) -> list[str]:
    """Human-readable meeting block — same phase names as JSON (ADR-004)."""
    if not sessions:
        return []
    head = f"Meetings (parallel to focus; in_call={fmt_dur(in_call_s)}"
    if tab_open_s > 0:
        head += f" tab_open={fmt_dur(tab_open_s)}"
    head += ")"
    lines = ["", head]
    for m in sessions[:limit]:
        label = m.label if len(m.label) <= 48 else m.label[:47] + "…"
        cid = f" {m.call_id}" if m.call_id else ""
        lines.append(
            f"  {local_hm(m.start)}–{local_hm(m.end)}  {fmt_dur(m.duration_s):>8}  "
            f"[{m.phase}] {m.provider}{cid} · {label}"
        )
    if len(sessions) > limit:
        lines.append(f"  … +{len(sessions) - limit} more")
    return lines
