---
title: "ADR-004: Meeting sessions as a parallel attention track"
description: Continuous in_call/tab_open sessions compiled from desktop+focus titles; not focus replacement; not presence state
status: accepted
date: 2026-08-03
relates_to:
  - 001-axis-of-decomposition
  - 002-presence-and-idle
---

## Status

Accepted

## Context

Operators need honest **call time** in day recap while **multitasking** (Discord, Slack, terminal open during Meet). The previous model only reclassified **input-idle / away** gaps when a Meet-like title was last seen. That produced:

1. Fragmented “meeting” slices (only when keyboard idle ≥ threshold)
2. Multitask during a real call attributed solely to other apps — call duration missing
3. Leftover Meet lobby / `landing` / bare “Google Meet” tabs after hangup still tagged as meeting

Focus dwell must stay a first-class track (ADR-001: facts from focus collector; surfaces must not invent a second focus model). Presence (`active` | `idle` | `offline`) remains input/liveness (ADR-002) — **not** “in a conference call.”

Evidence already exists: `desktop_snapshot` lists background Meet windows with titles like `Camera and microphone recording`; meeting-titled `focus` events mark join/leave UI moments.

## Options considered

### A. Idle-only meeting annotation (status quo)

- **Pros:** Simple; no new product surface
- **Cons:** Wrong for multitask calls; residual tabs pollute totals
- **Rejected** as sole model

### B. New `meeting` collector writing transition events

- **Pros:** Query-time cheap; explicit store history
- **Cons:** Classifies interpretation as facts; dual-write risk with title thrash; premature until a *new signal* (calendar, conference pipe) exists
- **Rejected** for now — keep interpretation compile-time

### C. Fold “in call” into presence SM (`active|idle|offline|meeting`)

- **Pros:** One status field
- **Cons:** Collides with ADR-002 liveness semantics; NATS/MCP coarse activity becomes ambiguous
- **Rejected**

### D. Chosen — parallel session track in `report/`

- **Pros:** Axial-clean; focus unchanged; one compile product for recap and future live status; phases separate call vs leftover tab
- **Cons:** Hour buckets can sum > wall clock; end-of-call is **window-signal**, not human hangup memory
- **Accepted**

## Decision

### 1. Parallel track

| Track | Meaning | Source |
|-------|---------|--------|
| **Focus dwell** | Foreground attention (app/title/agent cwd) | `focus` events → `focus_segments` |
| **Meeting sessions** | Conference surface open (call or leftover tab) | `desktop_snapshot` + meeting-titled `focus` only |
| **Away / idle** | Input silence (ADR-002) | `idle` transitions or degraded gap |

Meeting sessions **do not** replace focus. Multitask during a call appears on **both** tracks (by design).

### 2. Phases (API + operator vocabulary — one name)

| Phase | Operator meaning | Typical evidence |
|-------|------------------|------------------|
| `in_call` | Actively in a call (or strong in-call chrome) | Room URL `xxx-yyyy-zzz`, `Meet – …`, camera/mic recording, screen share |
| `tab_open` | Meeting UI still open **after** call (or lobby without in-call chrome) | `meet.google.com/landing`, bare `Google Meet` |

**Forbidden:** counting `tab_open` in any “meeting / call duration” total.

### 3. Session rules (binding)

1. **Open / extend** on meeting evidence (`in_call` or `tab_open`).
2. **Non-meeting focus does not end** a session (multitask).
3. **Desktop snapshot with no meeting window clears** the session (hard stop).
4. **Phase change** (`in_call` ↔ `tab_open`) or `(provider, call_id)` change **splits** sessions.
5. **End of `in_call`** = first sample that leaves strong in-call evidence (phase flip or clear) — **window-signal**, not calendar end. Optional grace hysteresis is a future config (default **0** until measured).
6. Empty `windows: []` from a failed probe must **not** be emitted by collectors (prefer omit snapshot) — false clear risk.

### 4. Field contract (`DayRecap` / JSON)

| Field | Contract |
|-------|----------|
| `meeting_sessions` | Ordered list of continuous spans `{start,end,duration_s,provider,label,phase,call_id?}` |
| `meeting_total_s` | **Σ duration of sessions with `phase == "in_call"` only** |
| `meeting_tab_open_s` | **Σ duration of sessions with `phase == "tab_open"` only** |
| away `presence == "meeting"` | Idle/away gap **overlapping** an `in_call` session (overlay for focus cut-out). **Must not** be assumed equal to `meeting_total_s` |

Breaking note: prior builds used idle-reclassified sums for `meeting_total_s` and a looser “any Meet chrome” notion. Consumers must treat the new definition as authoritative.

### 5. Single evidence stream

One ordered sample stream drives **both**:

- session builder
- idle→meeting away annotation (overlap with open `in_call` sessions)

No second title-scan algorithm with different hysteresis.

### 6. Surfaces

CLI, MCP, future HTTP/NATS **format** `report/` products only. Live “in call now?” = open session at `now` from the **same** session compiler — never re-regex titles in surfaces.

Meeting is an **environment annotation**, not a fourth presence state. Prefer `annotations.meeting = in_call | tab_open | none` for live APIs later.

### 7. Providers

Meet has full phase fidelity. Zoom/Teams may stay coarse (`in_call` only until strong lobby signals exist). Prefer **unknown / omit** over confident false `in_call`.

### 8. Non-goals

- Meeting transcription / Claap replacement
- Calendar join times as truth
- OCR / screenshots of Meet UI
- Storing classified meeting events as collector facts (until a new raw signal appears)

## Consequences

### Positive

- Multitask calls report honest call duration without erasing focus facts
- Leftover tabs visible as `tab_open`, not inflated call time
- Axial fit: collectors emit windows/titles; `report/` owns interpretation
- Live status can reuse the same product later without dual models

### Negative / debt

- Parallel tracks: hour charts can sum above 3600s (document, don’t “fix” exclusivity)
- End time tracks window titles/snapshots — may end slightly before remembered hangup
- Sparse `desktop_snapshot` (fingerprint-stable) holds last known meeting state until next sample
- Regex title zoo is brittle; confined to `report/meeting.py`

### Neutral

- Spec + tests lock AC; hysteresis remains optional config later

## Implementation order

1. This ADR accepted  
2. Spec AC + field contract tests  
3. Shared samples → sessions → annotate away by overlap  
4. Recap / JSON fields (`meeting_tab_open_s`, phase `tab_open`)  
5. Later: live annotation on `active_now` / presence snapshot (same functions)  
6. Later: optional grace hysteresis; Zoom/Teams phase parity  

## References

- ADR-001 Axis of Decomposition  
- ADR-002 Presence and idle  
- Spec: `artifacts/specs/meeting-sessions-parallel-spec.md`  
- Advisory 2026-08-03 (architect / product / backend)
