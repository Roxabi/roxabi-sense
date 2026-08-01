---
title: "ADR-003: Store schema version and edge→cloud sync protocol"
description: Versioned local SQLite + language-agnostic export envelope for multi-plane query
status: accepted
date: 2026-08-01
relates_to:
  - 001-axis-of-decomposition
  - 002-presence-and-idle
---

## Status

Accepted (design + edge schema_version; full exporter + CF = #30)

## Context

roxabi-sense captures attention facts on the workstation (collectors → SQLite). Multi-plane query (local stdio MCP today; Cloudflare D1/R2 + remote MCP later) must not:

1. Break existing local DBs on package upgrade
2. Let cloud invent a divergent event model
3. Export window titles / media / paths by default (ADR-002)
4. Make cloud a substitute for collectors (epic #24 non-goal)

As of this ADR the edge store used `CREATE TABLE IF NOT EXISTS` only — no `schema_version`.

## Decision

### 1. Edge schema versioning

| Item | Rule |
|------|------|
| Package constant | `SCHEMA_VERSION` (int) in `store/migrate.py` |
| Persistence | `meta.schema_version` (string form of int) |
| Missing key | Treat as **0** (pre-versioned DBs) |
| On open | Apply ordered migrations until package version |
| Newer DB than package | **Fail closed** (`SchemaVersionError`) — upgrade the package |
| Corrupt version meta | Fail closed with message (do not silently reset) |

Migrations are pure SQLite steps in a dict keyed by target version. Version **1** is the baseline (`events` + `meta` as currently defined). Future DDL changes add version 2, 3, …

### 2. Sync shape: versioned event stream + cursor (not CRDT)

**Primary direction:** edge → cloud. Cloud is a **query replica**, never the write authority for collectors.

**Envelope** (language-agnostic JSON):

```json
{
  "sync_protocol_version": 1,
  "host_id": "<meta.machine>",
  "schema_version": 1,
  "cursor": { "after_local_id": 0 },
  "events": [
    {
      "local_id": 42,
      "ts": "2026-08-01T12:00:00Z",
      "kind": "focus",
      "payload_class": "coarse",
      "payload": {}
    }
  ],
  "meta": { "machine": "laptop" }
}
```

| Field | Meaning |
|-------|---------|
| `sync_protocol_version` | Envelope major; cloud rejects unknown major |
| `host_id` | Machine id (`meta.machine`) |
| `schema_version` | Edge DDL version that produced the batch |
| `cursor.after_local_id` | Exclusive lower bound; resume from last acked `events.id` |
| `events[].local_id` | Edge `events.id` (monotone per host) |
| `payload_class` | `coarse` (default) or `full` (operator opt-in) |

**Cloud row identity:** `(host_id, local_id)` unique. **Conflict policy:** idempotent upsert (re-push same id replaces payload). No multi-writer merge V1.

**Rejected:** bidirectional multi-device CRDT for raw attention facts.

### 3. Privacy — what leaves the machine

Default export `payload_class=coarse` applies the **same** sensitive-key policy as MCP coarse redaction in `SenseQuery` (titles, media tracks, absolute paths/URLs, etc.). Full detail only via operator config — not tool-arg escalation (ADR-002).

Product bans remain: no OCR, screenshots, keylogging, clipboard dumps.

### 4. Retention hooks (policy; implementation later)

- Edge may prune old events by age/count (config).
- Only prune rows with `id` strictly below last **acked** export cursor (minus safety margin) once an exporter exists.
- Cloud retention is independent of edge.

### 5. Version skew matrix

| Artifact | Skew rule |
|----------|-----------|
| `schema_version` | Old package must not open newer DB |
| `sync_protocol_version` | Cloud rejects unknown major; minor additive fields OK |
| Event `kind` payloads | Prefer additive keys; update redaction allowlist when adding sensitive fields |
| `SenseQuery` JSON | Portable read contract; CF reimplements against replica |

### 6. Non-goals

- Cloud replacing collectors or requiring online capture
- NATS as full-history sync (NATS stays coarse `activity`/`stale`)
- Fat plugin embedding the DB
- Live exporter / CF Workers in this ADR’s first land (tracked by #30)

## Consequences

### Positive

- Safe local upgrades with an explicit version
- CF implementers have a frozen envelope without reading Python
- Privacy default matches MCP coarse
- Axial: sync is an **export surface**, not a second collector axis

### Negative / debt

- Exporter and prune code still to build (#30 / follow-ups)
- Kind-level payload schemas remain informal JSON — discipline via tests + redaction list
- Concurrent CLI open during migration relies on SQLite locking

## Options considered

| Option | Verdict |
|--------|---------|
| A. Push SenseQuery snapshots only | Allowed as a thin intermediate mode; not permanent protocol |
| B. Versioned event stream + cursor | **Accepted** |
| C. Bidirectional CRDT | Rejected for V1 |

## Implementation pointers

- Edge: `src/roxabi_sense/store/migrate.py`, `Store` open path
- Read contract: `src/roxabi_sense/query.py`
- Issue trail: #29 (this), #30 (CF), #31 (local stdio permanent)
