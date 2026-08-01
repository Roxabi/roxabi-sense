---
title: "store schema version + edge→cloud sync protocol"
description: "Shapes for versioned local SQLite and edge→cloud export without breaking offline SenseQuery"
issue: 29
tier: F-full
status: approved
date: 2026-08-01
---

## Source

GitHub #29 (epic #24 child 5): *Prepare multi-plane data without breaking local SQLite: schema version + sync protocol design.*

## Problem

Today the edge store:

1. Applies a static `SCHEMA` string with `CREATE TABLE IF NOT EXISTS` only (`store/db.py`) — **no `schema_version`**, no ordered migrations, no reader/writer skew policy.
2. Appends opaque JSON payloads per `kind` — rich enough for local recap (titles, media, cwd) but **unsafe to mirror wholesale** to a cloud plane (ADR-002).
3. Has no **export cursor**, host identity for multi-machine, or retention/prune contract — yet #30 will need to land CF storage + HTTP/MCP without inventing these under fire.

`SenseQuery` already defines transport-agnostic **read** JSON (status, active_now, timeline, sessions, recap) with coarse redaction. That is a portable **query** contract, not a **sync** or **storage evolution** contract.

## Outcome

Success without prescribing implementation details:

- Local open is version-aware: old DBs upgrade safely; unknown future versions fail closed with a clear error.
- CF implementers can implement a replica ingest + query plane from a written envelope (fields, privacy class, cursor, conflicts) without reading Python.
- Operators can enable edge→cloud knowing **what leaves the machine** by default (coarse) and how to opt into fuller detail.
- Offline / local stdio MCP keeps working with zero cloud dependency (#31).

## Appetite

**1–2 focused cycles for this issue** (research + ADR + minimal edge versioning if needed). Full sync client + CF tables are **#30**, not this PR.

## Current code map

| Area | Path | Note |
|------|------|------|
| Schema DDL | `src/roxabi_sense/store/db.py` `SCHEMA` | `events(id,ts,kind,payload)` + `meta(key,value)` |
| Migrations | — | **none** |
| Meta keys | daemon / collectors | `last_tick`, `machine`, `idle_watch`, `atspi_agent`, probe keys… |
| Read contract | `src/roxabi_sense/query.py` `SenseQuery` | Coarse drop keys for titles/media/paths |
| Privacy ADR | `docs/architecture/adr/002-presence-and-idle.md` | MCP coarse; NATS no titles |
| CF sketch | `docs/ARCHITECTURE.md` | “reimplement SenseQuery against D1/R2” |

Illustrative local volume (operator machine at analysis time): large event count, multi-kind firehose (focus-heavy) — retention matters before cloud bill.

## Shapes

### Shape A — Query-snapshot export only

Cloud never stores raw events. Edge periodically (or on demand) pushes **redacted SenseQuery products** (status, day_recap, coarse timeline summaries) as documents.

**Trade-offs:**
- Pro: Smallest privacy surface; no event schema coupling; matches tool HTTP mapping already listed in ARCHITECTURE.
- Pro: Easy CF model (KV/D1 docs keyed by `machine` + `day`).
- Con: Cloud cannot re-derive new queries offline from full history; product frozen to precomputed views.
- Con: Still need `schema_version` on edge for local evolution, but cloud schema is view-schema not event-schema.

**Rough scope:** M for protocol + edge snapshot pusher later; S for docs-only now.

### Shape B — Versioned event stream + cursor (recommended)

1. **Edge store evolution:** `meta.schema_version` (integer); ordered migrations module applied on `Store.__init__`; package embeds `SCHEMA_VERSION` constant.
2. **Sync envelope (language-agnostic):** batches of events + meta deltas, each event: `{host_id, local_id, ts, kind, payload_class, payload}`.
3. **Privacy:** default `payload_class=coarse` applies the same key allowlist/redaction as MCP coarse **at export time** (not only at query time). `full` only if operator config enables cloud full detail.
4. **Direction:** edge → cloud primary. Cloud is a **query replica**, never the write authority for collectors.
5. **Cursor:** per host `last_exported_local_id` (monotone `events.id`); resume after failure.
6. **Conflicts:** cloud rows keyed by `(host_id, local_id)` — last-write-wins on re-push same id (idempotent upsert). No multi-writer merge V1.
7. **Retention:** edge prune by age/count (config); cloud retention separate; prune must not break cursor (export high-water mark, then delete older than watermark only after ACK).

**Trade-offs:**
- Pro: CF can reimplement any SenseQuery method over mirrored events; matches long-term epic.
- Pro: Explicit versioning + skew policy before two writers of “truth” exist.
- Con: Larger design surface; must version envelope + schema separately (`schema_version` vs `sync_protocol_version`).
- Con: Payload evolution per `kind` still needs discipline (document allowlists).

**Rough scope:** M for ADR+minimal migrations now; L for full #30 implement.

### Shape C — Bidirectional multi-device CRDT / merge

Cloud and multiple edges co-edit presence history; sync both ways with vector clocks.

**Trade-offs:**
- Pro: Multi-machine “one brain” narrative.
- Con: Far beyond product need; collectors are host-local; conflicts on focus/idle are meaningless to merge.
- **Rejected for V1** (and likely forever for raw attention facts).

## Fit Check

| Constraint | A | B | C |
|------------|---|---|---|
| ADR-001 collectors primary | ✓ | ✓ | ✓ |
| ADR-002 coarse default | ✓ | ✓ | risky |
| Cloud ≠ collectors | ✓ | ✓ | weak |
| Offline local SSOT | ✓ | ✓ | weak |
| Enables #30 flexible query | weak | **strong** | strong |
| Appetite (this issue) | ✓ | ✓ | ✗ |

**Recommendation: Shape B**, with Shape A allowed as a **thin intermediate** (optional “push day_recap only” mode) but not the permanent protocol.

### Versioning strategy (reader/writer skew)

| Artifact | Owner | Rule |
|----------|-------|------|
| `schema_version` (int) | edge package | Writer upgrades DB on open; reader with older code **refuses** DB with higher version (fail closed + message to upgrade package) |
| `sync_protocol_version` (int) | envelope | Cloud rejects unknown major; minor additive fields OK |
| Event `kind` payload | collectors | Additive keys preferred; redaction allowlist versioned with protocol |
| `SenseQuery` tool JSON | query plane | Semver via package; CF tracks documented shapes in ADR/spec |

### What leaves the machine (default)

| Class | Includes | Excludes |
|-------|----------|----------|
| **coarse** (default export) | `ts`, `kind`, app ids, presence-related booleans, session counts/agent names, idle source/threshold, machine id | `title`, `title_raw`, media tracks, absolute cwd/paths, URLs |
| **full** (opt-in) | local store fidelity | Still never OCR/keylogs/clipboard (product ban) |

Align with `_COARSE_DROP_KEYS` in `query.py` — single redaction function shared by MCP and export.

### Non-goals (restate)

- Cloud replacing collectors
- NATS as the sync plane for full history (NATS stays coarse heartbeats)
- Marketplace fat-plugin embedding DB

## Risks

| Risk | Mitigation |
|------|------------|
| Migration bugs corrupt production DB | migrations in transaction; backup note in docs; tests per migration |
| Export leaks titles | redaction unit tests; default coarse; doctor/config surface for detail |
| Cursor + prune race | only prune `id < last_acked_export_id - safety_margin` |
| CF invents parallel schema | this ADR is SSOT; #30 must cite it |
| Scope creep into #30 | this issue lands **contract + edge schema_version**; not Workers code |

## Files impacted (when implementing)

| File | Change |
|------|--------|
| `src/roxabi_sense/store/db.py` | `SCHEMA_VERSION`, migrate on open |
| `src/roxabi_sense/store/migrate.py` (new) | ordered migrations |
| `docs/architecture/adr/003-*.md` | accepted decisions |
| `docs/ARCHITECTURE.md` | multi-plane + sync pointer |
| `tests/test_store*.py` | version + migrate |
| later #30 | CF ingest of envelope |

## Recommended deliverables for #29 implement

1. **ADR-003** accepted: schema versioning + sync protocol (Shape B).
2. **Spec artifact** with acceptance criteria for edge migrate + envelope fields.
3. **Minimal code (optional but preferred):** write `schema_version` on open; refuse newer DB; no full sync client yet.
4. **Follow-up issues** if needed: edge export client; prune CLI; #30 CF.

## Unresolved (for spec, not blockers)

- Exact retention defaults (days) — propose 90d local / configurable.
- Auth for #30 (token / Access) — out of band; envelope assumes authenticated transport.
- Whether meta keys export wholesale or allowlist — propose allowlist (`machine`, schema, presence-related).
