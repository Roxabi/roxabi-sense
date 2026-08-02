# Cross-domain validation (cocoindex N/A)

`ccc` is not initialized in this repo (`ccc init` never run). Validation used **ripgrep-equivalent** greps on 2026-07-31.

## Confirmed patterns

| Pattern | Signal | Files | Interpretation |
|---------|--------|-------|----------------|
| `parse_ts` / `to_z` triplication | exact | `report/segments.py`, `report/presence.py`, `collectors/idle_facts.py` | **confirmed-drift** DRY (not wrong-axis N×M) |
| `SessionRegistry` dual use | exact | `util/session_registry.py` default + `collectors/agent_sessions.py` private instance | **probable** dual registry |
| `shell=True` | 0 hits | — | security positive |
| `TODO`/`FIXME`/`HACK` in src | 0 hits | — | debt is structural, not markers |
| Collectors → surfaces | 0 hits | — | axial clean |
| pyright | 0/0 | — | type gate green |
| pytest | 89 passed | — | suite green |

## Rule application

Primary-agent DRY/timestamp findings retain severity (grep confirmed).  
Security RCE claims would need code path evidence — none raised as P0.
