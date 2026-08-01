---
title: "store schema version + edge→cloud sync protocol"
issue: 29
status: approved
tier: F-full
date: 2026-08-01
promoted-from: artifacts/analyses/29-schema-sync-protocol-analysis.md
shape: B
---

## Context

- Issue: [#29](https://github.com/Roxabi/roxabi-sense/issues/29) (epic #24 child 5)
- Frame: `artifacts/frames/29-schema-sync-protocol-frame.md` (approved)
- Analysis: `artifacts/analyses/29-schema-sync-protocol-analysis.md` (approved, Shape B)
- Blocks: #30 Cloudflare query plane
- Related: ADR-001 (axis), ADR-002 (privacy/presence), #31 local stdio permanent

## Goal

Freeze a **versioned edge store** story and a **language-agnostic edge→cloud sync contract** so local SQLite stays SSOT for capture while CF can build a query replica without inventing privacy or schema under time pressure.

## Users

| Who | Need |
|-----|------|
| Edge package | Safe DB open/migrate; fail closed on unknown future schema |
| CF implementer (#30) | Envelope + privacy + cursor rules without reading Python |
| Operator | Know what leaves the machine; opt-in full detail only |

## Expected Behavior

1. On first open of a DB after upgrade, store records `schema_version` and applies ordered migrations; existing DBs without the key migrate from version 0.
2. A package that is older than the DB’s `schema_version` refuses to open (clear error: upgrade `roxabi-sense`).
3. Written ADR + ARCHITECTURE describe: what leaves the machine (coarse default), sync direction (edge→cloud), conflict policy (idempotent upsert by host+local id), retention hooks.
4. Redaction for export reuses the same coarse key policy as MCP (`SenseQuery` / shared helper) — no second allowlist drift.
5. No cloud code, no live exporter client required in this issue; envelope is frozen for #30.

## Data Model & Consumers

### Edge (local SQLite)

| Table / key | Fields | Notes |
|-------------|--------|-------|
| `events` | `id`, `ts`, `kind`, `payload` JSON | Unchanged V1 layout |
| `meta` | `key`, `value` | Adds `schema_version` (string int) |
| package const | `SCHEMA_VERSION: int` | Writer capability |

### Sync envelope (logical; not stored as one table on edge)

```json
{
  "sync_protocol_version": 1,
  "host_id": "laptop",
  "schema_version": 1,
  "cursor": { "after_local_id": 0 },
  "events": [
    {
      "local_id": 42,
      "ts": "2026-08-01T12:00:00Z",
      "kind": "focus",
      "payload_class": "coarse",
      "payload": { "app": "ghostty", "source": "atspi" }
    }
  ],
  "meta": { "machine": "laptop" }
}
```

Cloud row identity: `(host_id, local_id)` unique. Upsert idempotent.

### Consumer summary

| Consumer | Fields | When | Status |
|----------|--------|------|--------|
| Edge daemon/CLI/MCP | full local store | always | this issue (migrate only) |
| Export path (later) | coarse/full envelope | opt-in sync | #30 / follow-up |
| CF SenseQuery port | mirrored events + meta | remote query | #30 |
| NATS | coarse activity/stale only | phase 4 | out of scope |

## Breadboard

| ID | Affordance | Handler | Data |
|----|------------|---------|------|
| S1 | Open store | `Store.__init__` → migrate | `meta.schema_version` |
| S2 | Migrate 0→N | `store/migrate.py` ordered steps | DDL + meta write |
| S3 | Refuse newer DB | open check | error + version pair |
| S4 | Document envelope | ADR-003 | protocol fields |
| S5 | Shared redaction | extract/reuse coarse keys | export + MCP |
| S6 | Retention policy text | ADR + ARCHITECTURE | prune after ACK (future code) |
| S7 | Doctor optional | show schema_version | operator visibility |

## Slices

| Slice | Demo | Affords | Out |
|-------|------|---------|-----|
| **1 — Version + migrate** | Fresh DB gets v=SCHEMA_VERSION; old DB migrates; tests pass | S1–S3, S7 | sync client |
| **2 — ADR + docs** | ADR-003 accepted; ARCHITECTURE multi-plane section cites envelope | S4–S6 | CF Workers |
| **3 — Redaction SSOT** | Single module/function used by MCP coarse (refactor if needed) | S5 | full exporter |

This issue ships slices 1–2 fully; slice 3 if low-risk refactor fits same PR, else note follow-up.

## Success Criteria

- [ ] SC1: `SCHEMA_VERSION` constant exists; new stores write `meta.schema_version` on open
- [ ] SC2: Opening an existing pre-version DB upgrades to current version without data loss
- [ ] SC3: Opening a DB with `schema_version` > package version fails with non-zero path / clear error (tests cover)
- [ ] SC4: ADR-003 documents Shape B: edge→cloud, coarse default, cursor, conflict upsert, non-goals
- [ ] SC5: ADR-003 / ARCHITECTURE state cloud does not replace collectors; local offline remains SSOT
- [ ] SC6: Privacy: default export class = coarse; lists dropped fields aligned with ADR-002 / MCP
- [ ] SC7: Versioning skew policy written (schema vs sync_protocol_version vs package)
- [ ] SC8: Unit tests for migrate + refuse-newer; full pytest suite green

## Edge Cases

| Case | Handling |
|------|----------|
| Missing `schema_version` meta | Treat as 0; run migrations |
| Corrupt meta value | Fail closed (treat as unreadable) or reset only if empty DB — **prefer fail closed with message** |
| Concurrent readers during migrate | Single writer daemon; CLI readers may see brief lock (SQLite busy_timeout already 5s) |
| Empty events table | Migrate still sets version |
| Future additive event keys | Allowed; redaction allowlist must drop new sensitive keys when added |

## Non-goals (this issue)

- Cloudflare Workers / D1 / R2 / auth
- Live `sense sync` CLI or background uploader
- Bidirectional multi-device merge
- Changing collector payloads or presence math
- Automatic prune implementation (policy only OK)

## Follow-ups (file if not in #30)

- Edge export client + cursor persistence (`meta.sync_cursor_*`)
- Prune command respecting ACK watermark
- #30 CF ingest + remote SenseQuery
- #31 ADR: permanent local stdio

## Pre-check

| Check | Result |
|-------|--------|
| Testable criteria | pass — SC1–SC8 binary |
| Dangling breadboard IDs | pass — S1–S7 in slices |
| Ambiguity budget | 0 χ |
| Slice coverage | pass |
| Edge completeness | pass |
