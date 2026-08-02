# Structural layer check (importlinter N/A)

**Domain:** Axial Drift  
**Date:** 2026-07-31  
**Scope:** `src/roxabi_sense/**`  
**Method:** Manual greps + import graph (no `.importlinter` config; `docs/standards/backend-patterns.md` notes import layers off until collectors/store/surfaces stabilize)  
**Canon:** ADR-001 Axis of Decomposition — primary axis = signal source / collector

---

### Summary

**Verdict: axial-clean (no P0/P1 layer violations).** Collectors write store facts only; shared presence/timeline aggregation lives in `report/` + `store/`; the sole live surface (`cli.py`) is a thin adapter. ADR-001 anti-pattern greps all return empty. Residual risk is structural/process: `surfaces/` is an empty package, CLI sits at package root, and MCP/NATS are stubs — N×M drift cannot appear until a second surface ships, at which point `_summarize` and any private query helpers must stay out of the new surface.

| Check (ADR-001) | Result |
|-----------------|--------|
| `def (status\|day\|active_now\|what_was_i_doing)` under `surfaces/` | **0 matches** (package has only `__init__.py`) |
| `from roxabi_sense.surfaces` inside `collectors/` | **0 matches** |
| `src/roxabi_sense/surfaces/.*/collect` | **0 matches** |
| Collectors depend on CLI/MCP/NATS | **None** (collectors → `store` + `util`/`atspi` only) |
| Query/timeline logic in CLI vs store/report | CLI is adapter; ownership in `store` + `report` |
| Host-axis forks (`laptop/` vs `m2/` packages) | **None** (`config.machine` string only) |

---

### Findings

| ID | Severity | File:Line | Finding | Evidence | Recommendation |
|----|----------|-----------|---------|----------|----------------|
| AD-001 | P3 | `src/roxabi_sense/surfaces/__init__.py:1` | `surfaces/` is a hollow package; live CLI surface is package-root `cli.py`, so ADR greps under `surfaces/` cannot catch surface-local query drift | Package docstring only: `"Outbound surfaces: CLI helpers, MCP, optional NATS publisher."` — no `surfaces/cli.py`, `mcp/`, or `nats/`. Entry is `pyproject.toml` → `roxabi_sense.cli:main`. ADR-001 tree maps adapters to `surfaces/` + thin `cli.py`. | When MCP ships, implement under `surfaces/mcp/` (or move CLI helpers under `surfaces/cli/`) and keep `cli.py` as entry only. Then enable importlinter contracts. |
| AD-002 | P3 | `src/roxabi_sense/cli.py:243-292` | Day human summarization (`_summarize`) is surface-local payload presentation; second surface could reimplement the same kind→text map (ADR drift signature for surface-primary) | `cmd_day` (L197-223) calls `store.day_bounds` / `store.events_for_day` then `_summarize` for text mode only; JSON path dumps raw payload. No MCP/NATS copy yet. | Accept as adapter formatting for now. Prefer shared `report.format_event_summary(kind, payload)` if MCP `what_was_i_doing` needs human lines; JSON tools should reuse store events as-is. |
| AD-003 | P3 | `src/roxabi_sense/cli.py:12,40-42,87-88` | CLI surface can trigger collection (`--collect`, `once`, `daemon`) via `daemon.collect_once` / `run_daemon` — not collector-internal import, but read surface coupled to collect orchestration | Imports: `from roxabi_sense.daemon import collect_once, run_daemon`. Status path optionally calls `collect_once(cfg)` before query. ADR greps target `surfaces/.*/collect`; root CLI is the analogue. | Fine for host CLI. **Gate for MCP:** tools must query store/report only — never call collectors or `collect_once`. Keep collect as explicit daemon/`sense once` ops. |
| AD-004 | P3 | `report/presence.py:33-34`, `report/segments.py:49-50`, `collectors/idle_facts.py:17-18` | Cross-cutting `parse_ts` (and sibling `_to_z`/`to_z`) triplicated across report + collectors | Identical: `datetime.fromisoformat(ts.replace("Z", "+00:00"))` in presence, segments, idle_facts; `_to_z`/`to_z` also in `store/db.py:59-60`, `idle_facts.py:13-14`, `segments.py:53-54`. Not wrong-axis N×M, but axial review cross-cut signal. | Optional: single `util/time.py` (`parse_ts`, `to_z`). Low priority until a third copy appears. |
| AD-005 | P3 | `src/roxabi_sense/cli.py:103-108` | MCP surface is a stub; NATS absent — axial health is single-surface today; N×M risk deferred | `if args.cmd == "mcp":` prints not-implemented, returns 2. No `surfaces/mcp`, no NATS publisher. ARCHITECTURE.md lists target tools `active_now` / `what_was_i_doing` / `sense_status`. | Ship MCP as thin wrappers over `presence_from_store`, `compile_day_recap`, `Store.events_*` — do not open private SQL or re-read `~/.claude`/`~/.grok` from MCP. |
| AD-006 | — (pass) | `collectors/**` | Collectors do not import surfaces, CLI, report, or daemon | All collector modules import `roxabi_sense.store.Store` (+ util/atspi/internal). Grep `from roxabi_sense.surfaces` / `roxabi_sense.cli` in `collectors/` = 0. | Keep: collectors → store (+ util) only. |
| AD-007 | — (pass) | `cli.py:138-240`, `report/*`, `store/db.py` | Query / timeline / presence logic is not CLI-owned | Presence: `report.presence.derive_presence` / `presence_from_store`. Recap: `report.day.compile_day_recap`. Day events: `Store.events_for_day`, `Store.day_bounds`. Status kinds: `STATUS_KINDS` owned by store (`store/db.py:32-40`). CLI `cmd_*` open Store + format. | Maintain ownership; MCP/NATS must import `report` + `store`, not fork. |
| AD-008 | — (pass) | `surfaces/` | Surfaces do not import collector internals (package empty) | No modules under `surfaces/` beyond `__init__.py`. Daemon (not a surface) imports collectors by design (`daemon_collectors.py:9-18`, `daemon.py:11-12`). | Daemon/coordinator importing collectors is expected host wiring, not surface drift. |
| AD-009 | — (pass) | tree | No host-primary package forks | Single package; `config.machine: str = "laptop"` (`config.py:55`); no `laptop/` / `m2/` domain trees. | Keep host variance in config only. |

---

### Metrics

| Metric | Value |
|--------|-------|
| importlinter config | **Absent** (intentional per backend-patterns) |
| ADR-001 anti-pattern greps | **0 / 0 / 0** violations |
| Collectors → surfaces/cli imports | **0** |
| Surfaces → collectors imports | **0** (surfaces empty) |
| Live outbound surfaces | **1** (`cli.py`); MCP stub; NATS none |
| Shared query modules | `store/db.py` (SQL + day bounds), `report/presence.py`, `report/day.py` + segments/enrich/meeting |
| CLI query ownership | Thin: status/day/recap → store/report; presentation: `_summarize` + print loops |
| Collectors (primary axis modules) | 6 exported collectors + idle_watch / idle_facts helpers |
| Host-axis package forks | **0** |
| Duplicated timeline compilers | **0** (single `compile_day_recap`) |
| Duplicated presence SMs | **0** (single `derive_presence`) |
| `parse_ts` definition sites | **3** (presence, segments, idle_facts) |

#### Observed import edges (acyclic)

```
collectors/*  → store, util, atspi (focus only); no report/cli/surfaces
report/*      → store, util; no collectors/cli
daemon*       → collectors, store, config, atspi
cli.py        → report, store, daemon, config  (entry / host surface)
store/*       → stdlib only
surfaces/*    → (empty package)
```

No cycles detected among these packages.

---

### Recommendations

1. **Do not add importlinter yet for wrong-axis relief** — structure already matches ADR-001 intent; enable contracts when `surfaces/mcp` (and optional NATS) land so contracts have real targets.
2. **MCP implementation contract (pre-merge checklist):**
   - Import `presence_from_store`, `compile_day_recap`, `Store` only for tools
   - No `collect_once`, no collector classes, no re-parse of agent histories
   - Coarse NATS later uses same presence SM (ADR-002)
3. **Optional tidy (non-blocking):** move CLI formatting helpers into `surfaces/cli/` or `report/`; unify `parse_ts`/`to_z` in `util/time.py`.
4. **Keep greps in CI review / axial review:**
   - `from roxabi_sense.surfaces` under `collectors/`
   - `from roxabi_sense.collectors` under `surfaces/` (allow only if packaging re-exports contracts — prefer none)
   - `def (active_now|what_was_i_doing|status|day)` under `surfaces/` that embed SQL or timeline compilation
5. **Document `report/` on ADR-001 tree** — ADR maps aggregation to `store/` or daemon coordinator; codebase correctly uses `report/` as the shared cross-collector layer. Update ADR mapping section so auditors do not flag report as “wrong axis.”

**Bottom line:** No axial drift violations requiring code fixes. Health is good; protect it when the second surface ships.
)
