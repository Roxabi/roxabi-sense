"""Meeting-window detection from desktop/focus titles (compile-time only).

Facts stay in collectors; this module only annotates idle/away segments when a
Meet / Zoom / Teams window was open (often background + input-idle).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from roxabi_sense.report.segments import AwaySegment, parse_ts
from roxabi_sense.store import Event

# Chrome Meet tab / AT-SPI titles observed in production.
_MEET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"meet\.google\.com", re.I),
    re.compile(r"\bGoogle\s*Meet\b", re.I),
    # "Meet – …" / "Meet - …" (en-dash or hyphen); avoid bare word "meet"
    re.compile(r"(?:^|[\s|])Meet\s*[–—-]\s*\S", re.I),
    re.compile(r"Camera and microphone recording", re.I),
    re.compile(r"Desktop content shared", re.I),
    re.compile(r"is sharing your screen", re.I),
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

# Strip browser chrome from labels for display.
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
    app: str | None = None
    active: bool | None = None


def match_meeting_title(title: str, app: str | None = None) -> MeetingHint | None:
    """Return a MeetingHint if title/app looks like an active meeting surface."""
    t = (title or "").strip()
    a = (app or "").strip()
    if not t and not a:
        return None
    blob = f"{a} {t}".strip()
    for pat in _MEET_PATTERNS:
        if pat.search(t) or pat.search(blob):
            return MeetingHint(
                provider="meet",
                label=_clean_label(t) or "Google Meet",
                title=t,
                app=a or None,
            )
    for pat in _ZOOM_PATTERNS:
        if pat.search(t) or pat.search(blob):
            return MeetingHint(
                provider="zoom",
                label=_clean_label(t) or "Zoom",
                title=t,
                app=a or None,
            )
    for pat in _TEAMS_PATTERNS:
        if pat.search(t) or pat.search(blob):
            return MeetingHint(
                provider="teams",
                label=_clean_label(t) or "Microsoft Teams",
                title=t,
                app=a or None,
            )
    return None


def meeting_hint_from_windows(windows: list[Any]) -> MeetingHint | None:
    """Scan a desktop_snapshot windows list; prefer active meeting windows."""
    if not windows:
        return None
    passive: MeetingHint | None = None
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
            return hint
        if passive is None:
            passive = hint
    return passive


def annotate_away_with_meetings(
    away: list[AwaySegment],
    events: list[Event],
) -> list[AwaySegment]:
    """
    Reclassify idle/away segments as presence=meeting when a meeting window was open.

    Uses last-known desktop_snapshot windows at segment start, any snapshot during
    the gap, and focus titles (last before / during the gap).
    """
    if not away:
        return away

    snaps: list[tuple[datetime, list[Any]]] = []
    focus_hints: list[tuple[datetime, MeetingHint]] = []
    for e in events:
        if e.kind == "desktop_snapshot":
            wins = e.payload.get("windows") if isinstance(e.payload, dict) else None
            if isinstance(wins, list):
                snaps.append((parse_ts(e.ts), wins))
        elif e.kind == "focus" and isinstance(e.payload, dict):
            h = match_meeting_title(
                str(e.payload.get("title") or ""),
                str(e.payload.get("app") or ""),
            )
            if h is not None:
                focus_hints.append((parse_ts(e.ts), h))

    snaps.sort(key=lambda x: x[0])
    focus_hints.sort(key=lambda x: x[0])

    out: list[AwaySegment] = []
    for seg in away:
        hint = _hint_for_segment(seg, snaps, focus_hints)
        if hint is None:
            out.append(seg)
            continue
        out.append(
            replace(
                seg,
                presence="meeting",
                meeting_label=hint.label,
                meeting_provider=hint.provider,
            )
        )
    return out


def _hint_for_segment(
    seg: AwaySegment,
    snaps: list[tuple[datetime, list[Any]]],
    focus_hints: list[tuple[datetime, MeetingHint]],
) -> MeetingHint | None:
    t0 = parse_ts(seg.start)
    t1 = parse_ts(seg.end)

    # Focus during gap wins (user had Meet focused at least briefly).
    for ts, h in focus_hints:
        if t0 <= ts <= t1:
            return h

    # Snapshots during gap.
    last_before: list[Any] = []
    for ts, wins in snaps:
        if ts <= t0:
            last_before = wins
            continue
        if ts <= t1:
            h = meeting_hint_from_windows(wins)
            if h is not None:
                return h
            last_before = wins
            continue
        break

    h = meeting_hint_from_windows(last_before)
    if h is not None:
        return h

    # Last meeting focus before idle (e.g. joined Meet then left focus on terminal).
    last_focus: MeetingHint | None = None
    for ts, fh in focus_hints:
        if ts <= t0:
            last_focus = fh
        else:
            break
    return last_focus


def _clean_label(title: str) -> str:
    t = _STRIP_SUFFIX.sub("", (title or "").strip())
    for _ in range(3):
        nxt = _STRIP_MEET_STATE.sub("", t).strip()
        if nxt == t:
            break
        t = nxt
    # "meet.google.com is sharing your screen." → keep short
    if re.search(r"is sharing your screen", t, re.I):
        return "Meet (screen share)"
    if re.fullmatch(r"meet\.google\.com/\S+", t, re.I):
        return t.split()[0] if t else "Google Meet"
    return t[:80] if t else "meeting"
