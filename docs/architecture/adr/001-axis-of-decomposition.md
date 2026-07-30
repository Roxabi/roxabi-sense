---
title: "ADR-001: Axis of Decomposition"
description: Primary axis chosen for system variation — prevents N×M drift
axial: true
---

## Status

Accepted

## Context

roxabi-sense is a local workstation attention sensor. It varies along three axes:

| Axis | Instances (now) | Growth (12m) |
|------|-----------------|--------------|
| **Signal source / collector** (`agent_session`, `idle`, `focus`, `process`, …) | 1–4 collectors planned | +calendar/presence signals; platform variants stay behind interfaces |
| **Surface** (CLI, MCP, NATS, optional loopback status) | CLI stub; MCP/NATS later | +MCP tools; +NATS opt-in; maybe local status page |
| **Host / machine** (laptop, M₂ / roxabitower) | 1 primary workstation | 2–3 machines sharing same package, different config |

Without an explicit primary axis, each new surface tends to re-implement collection and query logic (N×M: collectors × surfaces), or each host gets a forked sensor.

Reference: Roxabi `axial-decomposition.md`.

## Options Considered

### Option: Surface (CLI / MCP / NATS) as primary

- **Pros:** Matches “product face” people see first
- **Cons:** Every new surface reimplements store queries and collector semantics → N×M
- **Drift signature:** `active_now` logic copied in CLI and MCP and NATS formatter

### Option: Host / machine as primary

- **Pros:** Maps to deploy units (laptop vs tower)
- **Cons:** Hosts differ by config (machine id, enabled collectors), not by domain logic; forking by host multiplies bugs
- **Drift signature:** `laptop/` vs `m2/` packages with divergent event models

### Option: Signal source / collector as primary

- **Pros:** Product value grows by adding a new fact source; store + surfaces compose over shared events
- **Cons:** Cross-collector features (e.g. “stale” aggregation) need a thin shared layer
- **Drift signature:** Low if surfaces stay adapters over store queries

## Decision

**Primary axis:** Signal source / collector  
**Reason category:** Composition  
**Rationale:** Extending the system means adding or improving a collector (agent sessions, idle, focus, process). Surfaces (CLI, MCP, NATS) and hosts (laptop, M₂) are secondary — they consume the same store / event model and must not each own collector logic or policy.

When extending the system:

- New **collector** instance → one package under `collectors/`, writes typed facts to the store; grows by 1 row
- New **surface** → thin adapter over store queries / coarse publish; does **not** reimplement collection
- New **host** → config + machine id; same binary, not a fork

## Consequences

### Positive

- Collectors stay pure facts (AGENTS.md hard rule) regardless of who reads them
- CLI, MCP, NATS share one SQLite truth — no divergent “what was I doing” implementations
- Focus/Wayland can lag without blocking agent-session collection
- Factory Sentinelle remains policy-side; sensor stays NATS-optional and coarse

### Negative (Expected Debt)

- **Cross-collector aggregation** (e.g. heartbeat / stale) lives above collectors — Mitigation: keep aggregation in store queries or a single daemon coordinator, not per surface
- **Surface-specific shaping** (MCP tool schema vs CLI table vs NATS envelope) will feel duplicated at the edge — Mitigation: shared query functions in `store/`; adapters only format
- **Host config sprawl** if machine-specific toggles grow — Mitigation: `~/.config/roxabi-sense/config.toml` only; no host-named packages

### Anti-pattern signal

Grep pattern: `def (status|day|active_now|what_was_i_doing)` under `src/roxabi_sense/surfaces/` **or** collector imports from `surfaces/`  
If query/business timeline logic lives in a surface, or collectors depend on CLI/MCP/NATS, drift along the wrong axis is starting.

Canonical structural greps for review:

- `src/roxabi_sense/surfaces/.*/collect`
- `from roxabi_sense.surfaces` inside `collectors/`

### Revisit triggers

- Sibling-fix rate > 3/week on any surface-local query of the same kind
- Third surface ships with a private SQLite schema or private collector
- 6-monthly axial review

## Mapping to tree

```
src/roxabi_sense/
  collectors/     # PRIMARY axis (one module/package per signal source)
  store/          # shared persistence + queries
  surfaces/       # SECONDARY (cli, mcp, nats adapters)
  cli.py          # entry; thin
```
