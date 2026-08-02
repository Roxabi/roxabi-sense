# Architecture — P3 report + surfaces

**Partition:** `src/roxabi_sense/report/**/*.py`, `src/roxabi_sense/surfaces/**/*.py`  
**Domain:** Architecture (layering, aggregation placement, surface thinness, N×M risk)  
**Date:** 2026-07-31  
**ADRs:** 001-axis-of-decomposition, 002-presence-and-idle

### Summary

Cross-collector aggregation is correctly placed in `report/`: day recap, focus/away segments, meeting overlay, enrich side-tables, and pure `derive_presence` all sit above the store and do not reimplement collectors. Dependency direction is clean (`report` → `store` only; no imports from `collectors` or `surfaces`).

The secondary-adapter layer is incomplete by design for phase 3+: `surfaces/` is a package stub (docstring-only `__init__.py`). The live surface is root `cli.py` (outside this partition’s paths but the only consumer of report APIs). That is currently thin enough for status/recap, but **MCP / NATS are not scaffolded under `surfaces/`**, and there is **no shared `active_now` / timeline / redaction / NATS-envelope module** yet. Shipping phase 3a/3b by cloning CLI formatting or inventing private queries would reintroduce the N×M drift ADR-001/002 reject.

**Verdict:** report layer healthy; surfaces layer structural gap = highest architectural risk for P3 (not a runtime bug today).

### Findings

| ID | Severity | File:Line | Finding | Evidence | Recommendation |
|----|----------|-----------|---------|----------|----------------|
| A-P3-01 | P1 | `src/roxabi_sense/surfaces/__init__.py:1` | `surfaces/` is empty of adapters; ADR tree maps CLI/MCP/NATS here, but only a package docstring exists | File is one line: *“Outbound surfaces: CLI helpers, MCP, optional NATS publisher.”* No `cli.py`/`mcp.py`/`nats.py` under package. Grep for `mcp\|nats` under `src/roxabi_sense/surfaces` → none. Real surface lives at `cli.py` (package root). | Before MCP/NATS: land thin modules under `surfaces/` (`mcp.py`, `nats.py`, optional `cli_format.py`) that only open Store + call report/store APIs. Keep `cli.py` as argparse entry that delegates. Do not grow private SM logic in new surface files. |
| A-P3-02 | P1 | `src/roxabi_sense/report/` (package) | Missing shared query products for planned MCP tools (`active_now`, `what_was_i_doing`, `agent_sessions`) | Architecture target lists MCP tools in `docs/ARCHITECTURE.md:64-71`. Goal phase 3.1–3.2 requires same queries as CLI. Current report exports only day recap + presence (`report/__init__.py:11-19`). No `active_now` / time-window timeline compile; `sense day` is raw store dump in CLI only (`cli.py:197-223`). | Add report (or store-query) functions: e.g. `active_now(store) → Focus+sessions DTO`, `events_slice` / timeline summary with stable shapes; CLI and MCP both call them. Avoid MCP-private SQL or event walks. |
| A-P3-03 | P1 | `src/roxabi_sense/report/presence.py:54-165` | Presence SM is single and pure (good), but NATS `activity`/`stale` mapping and MCP redaction are absent — future surfaces risk forking presence semantics | `derive_presence` + `presence_from_store` are the ADR-002 entry; docstring says *“CLI/MCP entry”* (`presence.py:153`). No `presence_to_nats`, no coarse redactor, no hysteresis helper in report/surfaces. ADR-002:91–102 requires shared derive + surface-only format/redact. | When implementing NATS: one mapper `Presence → {activity\|stale, sources, confidence, degraded}` next to `derive_presence`. When implementing MCP: config-gated redaction of titles/cwd/media on DTOs, never tool-arg escalation. Both call `presence_from_store` only. |
| A-P3-04 | P2 | `src/roxabi_sense/report/day.py:134-228` + `presence.py:168-180` | Human formatters live inside `report/` (presentation mixed with aggregation) | `format_day_recap` (~95 LOC text layout) and `format_presence_lines` exported as public report API (`__init__.py:16-17`). ADR-001:69 mitigation: shared queries in store/report; **adapters only format**. CLI also has private `_summarize` for raw day (`cli.py:243-292`). | Keep pure compile (`compile_day_recap`, `derive_presence`, segment builders) in report. Prefer moving text layout to `surfaces/` (or `report/format_text.py` clearly marked presentation) so MCP JSON/redact paths don’t inherit CLI line-breaking rules. Do not duplicate formatters per surface. |
| A-P3-05 | P2 | `docs/architecture/index.md:25-30` | Living architecture docs omit `report/` as a layer while code depends on it for all cross-collector SM | Layer table lists collectors, store, surfaces only. ADR-001:68 requires aggregation above collectors; ADR-002:83 placement: `report/` or `store/`. Code reality: full compile stack in `report/`. | Document layer: `collectors → store → report (compile) → surfaces`. Update index + backend-patterns module tree so agents don’t dump aggregation into `cli.py` or future MCP. |
| A-P3-06 | P2 | `src/roxabi_sense/cli.py:103-108` (boundary) | MCP stub is a CLI exit path, not a surface module — no adapter seam yet | `sense mcp` prints *not implemented* and returns 2. No `surfaces.mcp` import graph. Increases chance phase 3a implements tools inline in `cli.py` or a one-off script. | Scaffold `surfaces/mcp.py` with tools that wrap report/store only; `cli.py` invokes `surfaces.mcp.run()`. Same pattern for NATS publisher owned by daemon but formatting in `surfaces/nats.py` or `report/` mapper. |
| A-P3-07 | P3 | `src/roxabi_sense/report/presence.py:33-34` vs `segments.py:49-50` | Duplicated `parse_ts` in report package (and again in collectors) | Identical ISO/Z parsers in `presence.parse_ts` and `segments.parse_ts`; meeting imports segments’ copy. Third copy in `collectors/idle_facts.py:17`. Not N×M business logic, but weak shared util story. | Single `util.time.parse_ts` (or store helper); report modules import one. Low priority. |
| A-P3-08 | P3 | `src/roxabi_sense/report/day.py:74` | Day compile caps at 50_000 events with no truncated flag on `DayRecap` | `_DAY_EVENT_LIMIT = 50_000`; `events_for_day(..., limit=_DAY_EVENT_LIMIT)` without comparing count vs cap in recap metadata. Surfaces would show incomplete day as complete. | Set `truncated: bool` / `event_limit` on `DayRecap` when `len(events) == limit`; surface can warn. Architectural: keep limit policy in report, not per surface. |
| A-P3-09 | P3 | `src/roxabi_sense/report/__init__.py:1` | Package docstring calls report “Compiled surfaces” — confuses primary secondary-axis vocabulary | Docstring: *“Compiled surfaces over the event store”*. ADR vocabulary: surfaces = CLI/MCP/NATS; report = compile/aggregation. | Rename concept in docstring to “compiled views / aggregates over the event store”. |

**Positive controls (no finding ID — keep):**

- **No collector reimplementation in report:** report modules only read `Event` / `Store` (`day.py:32`, `presence.py:10`, `enrich.py:8`, `meeting.py:15`, `segments.py:9`). Meeting title heuristics are explicitly compile-time (`meeting.py:1-4`).
- **Presence not CLI-private:** CLI calls `presence_from_store` / `derive_presence` + `format_presence_lines` (`cli.py:15-19`, `163-186`); offline-missing-DB path still uses shared `derive_presence` (`cli.py:149-156`).
- **Recap not CLI-private:** `cmd_recap` → `compile_day_recap` / `format_day_recap` only (`cli.py:226-239`).
- **Away/idle precedence in one place:** `away_segments` protocol-then-degraded (`segments.py:70-85`) matches ADR-002 §3.
- **Dependency DAG:** no `from roxabi_sense.surfaces` in collectors; no report → collectors; no circular report internals beyond acyclic day→enrich/meeting/segments.
- **Axial anti-pattern greps clean under surfaces:** no `def (status|day|active_now|…)` and no `collect` under `surfaces/` (package empty).

### Metrics

| Metric | Value |
|--------|-------|
| Report modules (`.py`, excl. `__pycache__`) | 6 (`__init__`, `day`, `enrich`, `meeting`, `presence`, `segments`) |
| Surfaces modules | 1 (`__init__.py` only) |
| Approx. LOC report | ~1.1k (`day` ~273, `segments` ~298, `meeting` ~223, `presence` ~181, `enrich` ~120, `__init__` ~20) |
| Report public exports | 8 (`DayRecap`, `Presence`, `compile_day_recap`, `derive_presence`, `presence_from_store`, `format_*` ×2) |
| Report → store imports | yes (all compile entrypoints) |
| Report → collectors imports | **0** |
| Surfaces → report/store | **n/a** (no surface code) |
| CLI → report (boundary consumer) | yes (status, recap) |
| Shared MCP/NATS helpers | **0** |
| Planned MCP tools without report DTO | ≥3 (`active_now`, `what_was_i_doing`, `agent_sessions`; status can reuse presence) |
| File length gate (≤300) | all report files under limit |
| Circular import risk | low |

### Recommendations

1. **Freeze aggregation in `report/`** — continue day/presence/segment/meeting/enrich here; never re-copy into MCP or NATS. Treat empty `surfaces/` as a **pre-phase-3 scaffold debt**, not as permission to put logic in `cli.py`.

2. **Scaffold surfaces before feature flesh (phase 3 gate):**
   - `surfaces/mcp.py` — stdio tools → Store + report only  
   - `surfaces/nats.py` (or daemon task + shared mapper) — `activity`/`stale` only, versioned envelope  
   - optional `surfaces/redact.py` — coarse/standard/full from config file  
   - `cli.py` stays entry + argparse; body stays ≤ store open + call + print

3. **Extend report with surface-agnostic DTOs before MCP:**
   - `active_now(store)` from last focus + agent session snapshot  
   - time-window timeline / `what_was_i_doing` (reuse segment logic where possible; do not re-walk raw events in MCP)  
   - `presence_to_activity_kind(Presence)` for NATS subject selection  
   Export JSON-friendly `to_dict` (already on `Presence` / `DayRecap`); apply redaction in surface layer.

4. **Clarify docs** — add `report/` to architecture layer diagram; fix report package docstring; state that formatters in report are transitional CLI helpers.

5. **Do not** implement NATS publish inside collectors or dual `derive_presence` in MCP (ADR-002 anti-patterns). Structural greps from ADR-001 remain the regression gate when surfaces fill in.

6. **Out of partition but related:** root `cli.py` `_summarize` is acceptable presentation for raw `sense day`; if MCP needs a human timeline, promote a structured summary builder into `report/` first, then format/redact per surface.

**Severity rollup:** 0×P0 · 3×P1 · 3×P2 · 3×P3 — all P1 items are **readiness / structural** for MCP·NATS, not current production correctness of recap/presence.
