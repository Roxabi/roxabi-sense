---
title: "store schema version + edge→cloud sync protocol"
issue: 29
status: approved
tier: F-full
date: 2026-08-01
spec: artifacts/specs/29-schema-sync-protocol-spec.md
---

## Goal

Land ADR-003 + edge `schema_version` migrations (SC1–SC8). No CF code.

## Tasks

| ID | Task | Files | Verify |
|----|------|-------|--------|
| T1 | ADR-003 schema + sync protocol (Shape B) | `docs/architecture/adr/003-schema-version-and-sync.md` | written accepted |
| T2 | ARCHITECTURE multi-plane + pointer to ADR | `docs/ARCHITECTURE.md` | cites envelope |
| T3 | `SCHEMA_VERSION` + migrate on open + refuse newer | `store/db.py`, `store/migrate.py`, `store/__init__.py` | tests |
| T4 | Tests migrate / refuse-newer / version written | `tests/test_store_migrate.py` | pytest |
| T5 | Doctor shows schema_version (optional, small) | `doctor.py` | doctor text |
| T6 | Redaction SSOT if trivial extract | `query.py` or `util/redact.py` | existing tests green |

## Order

T1 → T2 (docs can parallel T3)  
T3 → T4 → T5  
T6 only if extract is ≤1 small module without behavior change  

## Out of scope

sync client, CF, prune implementation, NATS
