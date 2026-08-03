---
title: "Meeting sessions — parallel track"
status: draft
date: 2026-08-03
adr: docs/architecture/adr/004-meeting-sessions-parallel-track.md
---

# Spec — Meeting sessions (parallel to focus)

## Problem

Day recap treated “meeting” as idle gaps with Meet chrome nearby. Real calls with multitask were under-counted; leftover Meet tabs after hangup were over-counted.

## Users

| Who | Need |
|-----|------|
| Operator (CLI `sense recap`) | Honest call duration + residual tab visibility |
| Agents (MCP `day_recap` JSON) | Stable field contract for `meeting_total_s` / sessions |

## Constraints

- Facts only in collectors (titles/windows as observed)
- No OCR, screenshots, meeting transcription
- Focus logging unchanged
- Presence SM (ADR-002) unchanged — meeting is not a presence state
- Primary axis: collector; interpretation in `report/` (ADR-001, ADR-004)

## Decision summary

See **ADR-004**. Parallel continuous sessions with phases `in_call` | `tab_open`; totals only sum `in_call`.

## Acceptance criteria

| # | Criterion |
|---|-----------|
| AC1 | Multitask focus (Discord/Slack/terminal) **does not** end an open `in_call` session while desktop still lists Meet in-call chrome |
| AC2 | Desktop snapshot with **no** meeting window ends the session |
| AC3 | `meeting_total_s == sum(s.duration_s for s in meeting_sessions if s.phase == "in_call")` |
| AC4 | Leftover lobby/landing/bare Google Meet → phase `tab_open`; **not** included in `meeting_total_s` |
| AC5 | Idle away gaps overlapping `in_call` may be `presence=meeting`; pure away otherwise; sum of those gaps **need not** equal `meeting_total_s` |
| AC6 | Human recap shows one vocabulary: phases `in_call` / `tab_open` (no dual names like `ended_open` + `meet_window`) |
| AC7 | Non-meeting media titles with “Audio playing” alone are **not** meetings |
| AC8 | Surfaces do not reimplement title regex (CLI/MCP call `report/` only) |

## Out of scope (this slice)

- Live `active_now` meeting annotation (child slice; same compiler)
- Hysteresis / grace after last in_call sample (default 0)
- Zoom/Teams full `tab_open` parity
- Store schema / meeting collector events

## Edges

| Edge | Expected |
|------|----------|
| Title thrash in_call↔tab_open | Split sessions (strict); debounce later |
| Sparse desktop snapshots | Hold last meeting state until next sample |
| call_id appears mid-session | Upgrade label/id; do not split solely for that |
| NBSP in “Google Meet” | Classifies as `tab_open` |
| Empty `windows: []` | Clears session — collector must not emit empty on probe fail |

## Test map

| AC | Test |
|----|------|
| AC1–2 | `test_meeting_sessions_survive_multitask_and_split_tab_open` |
| AC3 | `test_meeting_total_contract` |
| AC4–5 | annotate lobby + recap totals |
| AC7 | classify negative “Audio playing” alone |

## Ship note

Reinstall tool after land: `uv tool install -e '.[mcp]' --force` so operator CLI matches package.
