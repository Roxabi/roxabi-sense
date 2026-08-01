---
title: store schema version + edge→cloud sync protocol
issue: 29
status: approved
tier: F-full
date: 2026-08-01
---

## Problem

Local SQLite is production truth for attention facts, but it has **no schema version** and **no migration story** (`CREATE TABLE IF NOT EXISTS` only). Multi-plane query (edge stdio MCP today, Cloudflare D1/R2 + remote MCP later) needs a **written contract** before #30 implements cloud storage — otherwise:

- CF implementers invent event shapes that diverge from `SenseQuery`
- Upgrades leave old DBs unreadable or silently wrong
- Sync ships titles/media to the cloud by accident (ADR-002 violation)
- Cloud is mistaken for a replacement for collectors (epic non-goal)

## Who

- **Primary:** edge daemon/store maintainers (local writers) and CF implementers of #30
- **Secondary:** operators enabling opt-in sync; agents consuming the same tool JSON offline and remote

## Constraints

- Primary axis remains collectors (ADR-001); sync is a **surface/export**, not a second capture plane
- Privacy: ADR-002 coarse default; cloud must not exceed coarse unless operator opts in
- Cloud **never** replaces local collectors; local SQLite remains SSOT for offline/dev (→ #31)
- `SenseQuery` JSON shapes are the portable read contract (already used by MCP)
- Events use append-only `id` + `ts` + `kind` + JSON `payload`; meta is key/value
- Stack: Python edge, CF Workers/D1/R2 later — protocol must be language-agnostic

## Out of Scope

- Implementing Cloudflare storage/API/auth (#30)
- NATS factory publish (separate phase; coarse only)
- Full bidirectional multi-device merge as V1 product
- Changing collector semantics or presence derivation
- Marketplace / fat plugin packaging
- OCR, screenshots, keylogging, clipboard dumps

## Premise Validity

**Success in 6 months:** Local store opens with an explicit `schema_version`; migrations are ordered and tested; a written edge→cloud sync contract (what leaves the machine, cursor, conflict policy, retention) is consumable by #30 without redesign; offline local MCP still works without cloud.

**Failure in 6 months:** #30 ships a CF schema that cannot read edge batches, or raw window titles leave the machine by default, or operators must be online for capture/query to work — observable within one CF milestone PR + one edge upgrade cycle.

**Simplest alternative:** Document “cloud reimplements SenseQuery against D1” in ARCHITECTURE only; skip schema_version and sync cursors until someone needs them.  
**Why not simplest:** Reader/writer skew on upgrade is already real once two processes (daemon + CF ingest) exist; without version + cursor, the first sync PR will invent ad-hoc tables and privacy holes under time pressure.

## Complexity

**Tier: F-full** — new multi-plane protocol, store evolution, privacy defaults, retention; multi-domain (store + export surface + future CF). Analyze + spec + plan before implement.

Signals: complexity comment 6; arch unknowns (conflict policy, retention); blocks #30; deliverable is research/spec + ADR (minimal code only if it freezes schema_version).
