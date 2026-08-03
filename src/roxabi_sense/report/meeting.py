"""Meeting title classification + shared evidence samples (compile-time).

Phases and session rules: ADR-004. Continuous sessions live in
``meeting_sessions``; idle annotation reuses the same ``in_call`` spans.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal

from roxabi_sense.report.segments import AwaySegment, parse_ts
from roxabi_sense.store import Event

# Operator + API vocabulary (one name — ADR-004).
MeetingPhase = Literal["in_call", "tab_open"]

_IN_CALL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Camera and microphone recording", re.I),
    re.compile(r"Microphone recording", re.I),
    re.compile(r"Desktop content shared", re.I),
    re.compile(r"is sharing your screen", re.I),
    re.compile(r"(?:Meet|meet\.google).{0,120}Audio playing", re.I),
    re.compile(
        r"meet\.google\.com/(?!landing\b)[a-z0-9]{3}-[a-z0-9]{4}-[a-z0-9]{3}",
        re.I,
    ),
    re.compile(r"(?<!\bGoogle\s)Meet\s*[–—-]\s*\S", re.I),
)

_TAB_OPEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"meet\.google\.com/landing", re.I),
    re.compile(r"\bGoogle\s*Meet\b", re.I),
    re.compile(r"meet\.google\.com", re.I),
)

_ZOOM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"zoom\.us", re.I),
    re.compile(r"\bZoom Meeting\b", re.I),
    re.compile(r"^Zoom\b", re.I),
)

_TEAMS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"teams\.microsoft\.com", re.I),
    re.compile(r"\bMicrosoft Teams\b", re.I),
    re.compile(r"\|\s*Microsoft Teams\b", re.I),
)

_CALL_ID = re.compile(r"\b([a-z]{3}-[a-z]{4}-[a-z]{3})\b", re.I)

_STRIP_SUFFIX = re.compile(
    r"\s*-\s*(?:Google Chrome|Chromium|Brave|Firefox|Microsoft Edge).*$",
    re.I,
)
_STRIP_MEET_STATE = re.compile(
    r"\s*-\s*(?:"
    r"Camera and microphone recording|"
    r"Microphone recording|"
    r"Desktop content shared|"
    r"Audio playing|"
    r"High memory usage.*"
    r")\s*$",
    re.I,
)


@dataclass(frozen=True)
class MeetingHint:
    provider: str  # meet | zoom | teams
    label: str
    title: str
    phase: MeetingPhase = "in_call"
    call_id: str | None = None
    app: str | None = None
    active: bool | None = None


def match_meeting_title(title: str, app: str | None = None) -> MeetingHint | None:
    """Return a MeetingHint if title/app looks like a meeting surface."""
    t = (title or "").replace("\xa0", " ").strip()
    a = (app or "").strip()
    if not t and not a:
        return None
    blob = f"{a} {t}".strip()

    if any(p.search(t) or p.search(blob) for p in _IN_CALL_PATTERNS):
        return MeetingHint(
            provider="meet",
            label=_clean_label(t) or "Google Meet",
            title=t,
            phase="in_call",
            call_id=_extract_call_id(t),
            app=a or None,
        )
    if any(p.search(t) or p.search(blob) for p in _TAB_OPEN_PATTERNS):
        return MeetingHint(
            provider="meet",
            label=_clean_label(t) or "Google Meet",
            title=t,
            phase="tab_open",
            call_id=_extract_call_id(t),
            app=a or None,
        )
    for pat in _ZOOM_PATTERNS:
        if pat.search(t) or pat.search(blob):
            return MeetingHint(
                provider="zoom",
                label=_clean_label(t) or "Zoom",
                title=t,
                phase="in_call",
                app=a or None,
            )
    for pat in _TEAMS_PATTERNS:
        if pat.search(t) or pat.search(blob):
            return MeetingHint(
                provider="teams",
                label=_clean_label(t) or "Microsoft Teams",
                title=t,
                phase="in_call",
                app=a or None,
            )
    return None


def meeting_hint_from_windows(windows: list[Any]) -> MeetingHint | None:
    """Scan desktop windows; prefer active, then in_call over tab_open."""
    if not windows:
        return None
    active_hint: MeetingHint | None = None
    best_passive: MeetingHint | None = None
    for w in windows:
        if not isinstance(w, dict):
            continue
        title = str(w.get("title") or "")
        app = str(w.get("app") or w.get("app_id") or "")
        hint = match_meeting_title(title, app)
        if hint is None:
            continue
        hint = replace(hint, active=bool(w.get("active")))
        if w.get("active"):
            if active_hint is None or (
                hint.phase == "in_call" and active_hint.phase != "in_call"
            ):
                active_hint = hint
            continue
        if best_passive is None or (
            hint.phase == "in_call" and best_passive.phase != "in_call"
        ):
            best_passive = hint
    return active_hint or best_passive


def meeting_samples(
    events: list[Event],
) -> list[tuple[datetime, MeetingHint | None]]:
    """
    Single evidence stream (ADR-004 §5).

    - desktop_snapshot: always (None = no meeting window → clear)
    - focus: only when title matches a meeting surface (non-meeting focus omitted)
    """
    samples: list[tuple[datetime, MeetingHint | None]] = []
    for e in events:
        if e.kind == "desktop_snapshot" and isinstance(e.payload, dict):
            wins = e.payload.get("windows")
            if isinstance(wins, list):
                samples.append((parse_ts(e.ts), meeting_hint_from_windows(wins)))
        elif e.kind == "focus" and isinstance(e.payload, dict):
            h = match_meeting_title(
                str(e.payload.get("title") or ""),
                str(e.payload.get("app") or ""),
            )
            if h is not None:
                samples.append((parse_ts(e.ts), h))
    samples.sort(key=lambda x: x[0])
    return samples


def annotate_away_with_meetings(
    away: list[AwaySegment],
    sessions: list[Any],
) -> list[AwaySegment]:
    """
    Mark idle/away as presence=meeting when overlapping an **in_call** session.

    ``sessions`` must come from the same compile pass as ``meeting_total_s``
    (ADR-004). Overlap annotation need not sum to ``meeting_total_s``.
    """
    if not away:
        return away
    in_call = [
        (parse_ts(s.start), parse_ts(s.end), s)
        for s in sessions
        if getattr(s, "phase", None) == "in_call"
    ]
    if not in_call:
        return list(away)

    out: list[AwaySegment] = []
    for seg in away:
        t0, t1 = parse_ts(seg.start), parse_ts(seg.end)
        hit = next(
            (s for a0, a1, s in in_call if t0 < a1 and a0 < t1),
            None,
        )
        if hit is None:
            out.append(seg)
            continue
        out.append(
            replace(
                seg,
                presence="meeting",
                meeting_label=getattr(hit, "label", None),
                meeting_provider=getattr(hit, "provider", None),
            )
        )
    return out


def _extract_call_id(title: str) -> str | None:
    m = _CALL_ID.search(title or "")
    return m.group(1).lower() if m else None


def _clean_label(title: str) -> str:
    t = _STRIP_SUFFIX.sub("", (title or "").strip())
    for _ in range(3):
        nxt = _STRIP_MEET_STATE.sub("", t).strip()
        if nxt == t:
            break
        t = nxt
    if re.search(r"is sharing your screen", t, re.I):
        return "Meet (screen share)"
    if re.fullmatch(r"meet\.google\.com/\S+", t, re.I):
        return t.split()[0] if t else "Google Meet"
    return t[:80] if t else "meeting"
