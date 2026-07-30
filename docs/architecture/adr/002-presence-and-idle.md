---
title: "ADR-002: Presence state and idle authority"
description: How liveness, input idle, and active|idle|offline are derived without dual truth or event firehose
status: accepted
date: 2026-07-30
relates_to:
  - 001-axis-of-decomposition
---

## Status

Accepted

## Context

roxabi-sense records **where workstation attention was** from cheap structured signals (PURPOSE). Operators need:

1. Honest **away** in day recap (not last-focused app eating multi-hour gaps)
2. Honest **live status**: human input idle vs daemon offline vs degraded sensors
3. Thin exports later (MCP / NATS) that **must not** reimplement presence logic

Constraints from ADR-001:

- Primary axis = **collectors** (pure facts)
- Cross-collector aggregation (heartbeat / stale / presence SM) lives **above** collectors: store queries and/or a **single daemon coordinator**

Current facts:

- Focus is event-driven (AT-SPI); logind `IdleHint` is often stuck false without lock screen (SSH-friendly Cosmic setup)
- Day recap can **infer** away via ≥5 min gaps on `focus`/`desktop_snapshot` (degraded compile-only path)
- `meta.last_tick` already marks daemon progress; docs previously illustrated a periodic `kind=heartbeat` event — **rejected** as store firehose

## Decision

### 1. Separate three concerns

| Concern | Meaning | Storage |
|---------|---------|---------|
| **Daemon liveness** | Process is running and ticking | `meta` only (`last_tick`, optional watch health keys) |
| **Input idle** | No keyboard/mouse for ≥ threshold | Fact events `kind=idle` **on transitions only** |
| **Presence state** | Derived `active` \| `idle` \| `offline` for status/MCP/NATS | **Not** a peer collector fact; pure function output |

Media playing and process presence are **environment annotations**, never the sole driver of `active` for presence or NATS `activity`.

### 2. Idle write authority (single writer at runtime)

Prefer **one** runtime writer of `kind=idle`:

| Priority | Writer | When |
|----------|--------|------|
| 1 | Wayland `ext-idle-notify` (`get_input_idle_notification` preferred) via `idle_watch` subprocess | Watch healthy + graphical session |
| 2 | logind `IdleHint` / lock (existing collector) | Watch unavailable **and** config allows |
| 3 | *(none)* | No writer; recap may still use **degraded gap** at compile time |

Rules:

- When Wayland watch is healthy, **do not** dual-write competing logind idle transitions (demote or disable logind idle writer).
- Payload must include at least: `idle: bool`, `source` (`wayland-idle` \| `logind` \| …), `threshold_s`, and on enter: `idle_since` = **last activity evidence** (not merely event timestamp). For a 300s notify timeout, `idle_since ≈ event_ts - threshold` or last activity tracked by the watch.
- Shared config: `collectors.idle_threshold_s` (default **300**). Same value for notify timeout and degraded gap.

### 3. Idle read / recap precedence

When compiling a day:

1. Prefer protocol `idle` transitions with known `source` for away segments  
2. Else **degraded-gap**: silence ≥ threshold on activity kinds (`focus`, `desktop_snapshot`) starting at last activity  
3. Tag every away segment: `wayland-idle` \| `logind` \| `degraded-gap`  
4. Never attribute long protocol/degraded away dwell to the last focused app

### 4. Presence derivation (single pure function)

Implement **one** function (name illustrative):

```text
derive_presence(meta, latest_facts) -> {
  state: active|idle|offline,
  authority, confidence, degraded: bool,
  last_tick_age_s, idle_watch, idle_since?, threshold_s,
  session_bound: bool
}
```

Placement: `report/` or `store/` query module — **not** `cli.py` / MCP / NATS private copies.

| state | Rule (summary) |
|-------|----------------|
| `offline` | Daemon liveness stale (`last_tick` older than offline threshold) |
| `idle` | Authoritative input idle true (or degraded inference only when explicitly mode=degraded) |
| `active` | Not offline and not input-idle |

CLI `sense status`, MCP `sense_status` / `active_now`, and NATS mapping **call the same function** and only format/redact.

### 5. Liveness = meta, not event spam

- **Forbidden:** periodic `events` rows every 15–30s as “heartbeat”
- **Required:** update `meta.last_tick` (and watch health meta) on daemon progress  
- Optional later: sparse `presence` **transition** events if analytics need them — still not a keepalive firehose

### 6. Surfaces (secondary)

- **MCP:** default redaction **coarse** (no titles / title_raw / media tracks / absolute cwd); `full` only via operator config file, not tool-arg escalation  
- **NATS (opt-in):** subjects `activity` \| `stale` only; envelope versioned; **no titles**; include `sources[]`, `confidence`, `degraded`; multi-source hysteresis; media-alone must not yield confident `activity`  
- Sensor never encodes Sentinelle policy, Discord, or jobs

### 7. Focus field allowlist

Focus collector may store: `app`, `title` (optional), `pid`, `source`, agent link fields.  
**Forbidden product direction:** accessibility tree dumps, text content harvest, OCR, screenshots, raw key events.

## Options considered

### A. Dual-write logind + Wayland, filter only at read

- Pros: simple enable both  
- Cons: store lies; `last_by_kind("idle")` wrong; recap bugs  
- **Rejected** as default

### B. Periodic heartbeat events for “alive”

- Pros: easy timeline  
- Cons: store bloat; confuses activity kinds used for degraded gap; privacy trail  
- **Rejected**

### C. Presence SM only in CLI recap

- Pros: ships status fast  
- Cons: MCP/NATS reimplement → N×M axial drift  
- **Rejected**

### D. Chosen: single idle writer + meta liveness + pure derive_presence

- Pros: one truth, axial-clean, cheap store  
- Cons: need solid watch respawn + systemd graphical session  
- **Accepted**

## Consequences

### Positive

- Away/recap and live status share semantics  
- Collectors stay facts-only; coordinator owns liveness  
- Cosmic without lock screen can still detect input idle  
- Factory gets coarse signals without title firehose  

### Negative / debt

- systemd unit must be bound to **graphical-session** + Wayland env or idle_watch stays degraded  
- `install-service` must install a correct unit  
- Desktop snapshot volume still dominates DB growth (retention later; not solved by this ADR)  
- Long same-window reading with **zero** desktop/focus updates can still look idle under degraded mode if protocol down  

### Anti-patterns (do not)

- `HeartbeatCollector` that aggregates focus+idle+media into events every N seconds  
- Composite `idle = input AND !media` **inside** `IdleCollector`  
- NATS publish inside each collector  
- Treating `state=active` as “operator available for ops automation” without reading `confidence` / `degraded`

## Implementation order (binding for the goal)

1. Land recap baseline (phase 1)  
2. This ADR accepted  
3. Phase 2a: meta + `derive_presence` + status + install-service / unit  
4. Phase 2b: idle_watch + write demotion + recap tags  
5. Phase 3a MCP, then 3b NATS  

## References

- ADR-001 Axis of Decomposition  
- Goal: `artifacts/goals/2026-07-30-presence-idle-surfaces.md`  
- Panel: product / architect / devops / axial / adversarial 2026-07-30  
