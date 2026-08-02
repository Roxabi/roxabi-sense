# Architecture audit — P2 store + package root

**Partition:** `store/**`, `cli.py`, `config.py`, `paths.py`, `daemon*.py`, `install_service.py`, `__init__.py`  
**Domain:** Architecture  
**Date:** 2026-07-31  
**Scope:** Store as single truth; CLI thin adapter; daemon orchestration boundaries; circular imports; collectors↔daemon↔store coupling.

---

### Summary

P2 layering is **sound and acyclic**: `store` is a dependency leaf; collectors and report depend only on `Store`/`Event`; daemon owns runtime orchestration; CLI is mostly a thin surface over `report` + store queries. No circular imports.

Main risks are **kind/meta catalog drift** (string constants split across collectors vs `STATUS_KINDS`/`TIMELINE_KINDS`), **CLI-local status assembly** that MCP will want to share, and **daemon helpers that exist only for tests** (`FocusEventGate`, `tick_one`). Store remains the single persistence truth; presence derivation correctly lives above collectors (`report.presence`, ADR-002).

**Verdict:** Healthy primary-axis architecture with medium tech-debt (catalogs, surface query extraction, dead gate code). No P0 layer violations.

---

### Findings

| ID | Severity | File:Line | Finding | Evidence | Recommendation |
|----|----------|-----------|---------|----------|----------------|
| A-P2-01 | P2 | `store/db.py:32-52` vs collectors `KIND`/`SNAPSHOT` | Event kind catalog is dual-owned: store lists surface-facing kinds; each collector defines its own string. Catalog includes kinds no collector currently writes. | `TIMELINE_KINDS` includes `"agent_session"` and `"media"` (`db.py:44,47`); `AgentSessionsCollector` only writes `agent_sessions_snapshot` (`agent_sessions.py:14,46`); `MprisCollector` only writes `media_snapshot` (`mpris.py:12,46`). CLI still has dead branches for `media` / `agent_session` (`cli.py:269-278`). | Single kind registry (e.g. constants module imported by collectors + store lists), or generate `STATUS_KINDS`/`TIMELINE_KINDS` from collector-exported kinds. Drop or document unused kinds. |
| A-P2-02 | P2 | `cli.py:138-194` | `cmd_status` assembles status DTO (meta + count + latest kinds + presence) in the CLI surface — fine for one surface, but ADR-001/002 want shared query functions for MCP/NATS. | Status JSON body built inline (`cli.py:169-176`); presence correctly via `presence_from_store` (`cli.py:163`); latest kinds via `STATUS_KINDS` (`cli.py:191`). No `store`/`report` helper returns the full status snapshot. | Extract `status_snapshot(store, thresholds) -> dict` into `report/` (or thin `store` query module) before MCP ships; CLI/MCP only format. |
| A-P2-03 | P2 | `cli.py:243-292` | Day timeline presentation logic (`_summarize`) is CLI-private (~50 LOC of kind-aware formatting). Acceptable as edge formatting, but kind knowledge is duplicated with report/enrich. | `_summarize` switches on every kind string (`cli.py:243-292`); report has separate kind filters (`enrich.py`, `segments.py`). | Keep as format-only; if MCP needs raw day lines, move summarize next to report or share a minimal `format_event_line`. Do not reimplement queries. |
| A-P2-04 | P2 | `daemon_collectors.py:27-49` | `FocusEventGate` is dead in production; rate-limit now lives on AT-SPI agent (`probe_min_s`). Class retained + tested but never wired into `run_daemon`. | Defined `daemon_collectors.py:27-49`; only used in `tests/test_daemon_loop.py:113-115`. Daemon path uses queues + agent (`daemon.py:155-169`). | Delete or document as reserved; avoid two rate-limit models. Prefer agent-side gate as SSOT. |
| A-P2-05 | P2 | `daemon_collectors.py:138-154` + `daemon.py:19-28` | `tick_one` is production-unused; re-exported from `daemon` only for tests (`# noqa: F401`). Blurs public daemon API with test harness. | `tick_one` only called from tests (`test_daemon_loop.py`); production uses `tick_all` + `apply_probe_result`. | Import `tick_one` from `daemon_collectors` in tests; stop re-exporting test-only symbols from `daemon.__all__`. |
| A-P2-06 | P2 | meta keys across `daemon.py`, `daemon_atspi.py`, `daemon_collectors.py`, `focus_atspi.py` | Meta key vocabulary is free-form strings with no registry (liveness + probe health). Presence reads a subset (`last_tick`, `idle_watch`). | Writers: `last_tick`, `daemon_started`, `machine`, `idle_watch`, `atspi_agent`, `atspi_trace`, `last_focus_source` (`daemon.py:63-67`, `daemon_atspi.py:30-31,89,106`); plus `focus_probe_*` from collector (`focus_atspi.py:132-134`). Readers: `presence_from_store` (`presence.py:154-155`). | Optional `META_*` constants in store (or ADR-002 appendix). Document which keys are liveness vs debug. |
| A-P2-07 | P3 | `store/db.py:15-29` | Schema is `CREATE IF NOT EXISTS` only — no version/migration path. Fine for V1 append-only events; will block column/index evolution. | `SCHEMA` script (`db.py:15-29`); no `meta['schema_version']` write. | When schema changes: store version in `meta`, migrate on open. |
| A-P2-08 | P3 | `store/db.py:180-186` | `latest_by_kinds` issues N sequential `last_by_kind` queries (one per kind). | Loop at `db.py:182-185`. | Single SQL with window/`GROUP BY kind` if status path gets hot; low priority at current scale. |
| A-P2-09 | P3 | `daemon_collectors.py:23-24` vs `store/db.py:55-56` | Duplicate UTC stamp helpers with different implementations (`time.strftime` vs `datetime.now(UTC)`). | `_utc_stamp` (`daemon_collectors.py:23-24`); `_utc_now` (`db.py:55-56`). | Prefer store-owned timestamp helper (or shared util) so daemon meta and events share one clock format. |
| A-P2-10 | P3 | `cli.py:12` + package layout | CLI is both process entrypoint **and** surface adapter; lives at package root while `surfaces/` is empty stub. Matches standards map but diverges from ADR tree “surfaces only”. | `cli` imports daemon (`cli.py:12`); `surfaces/__init__.py` is docstring-only. | Accept for single binary; when MCP lands, put MCP under `surfaces/` and keep CLI as entry that may call surface modules. Do not move daemon into surfaces. |
| A-P2-11 | P3 | `daemon.py:116-121` vs `build_collectors` | `collect_once` never starts Wayland idle watch — only logind idle when backend allows. Different fact set than long-lived daemon (documented by process model, easy to misread). | `collect_once` → `build_collectors` (`daemon.py:287-291`, `daemon_collectors.py:116-121`); IdleWatch only in `run_daemon` (`daemon.py:97-121`). | Document in CLI help / PURPOSE: `once` = poll collectors; idle Wayland requires daemon. |
| A-P2-12 | — (positive) | import graph | **No circular imports.** Dependency direction is strict. | `store` imports nothing from package; collectors/report → store; daemon_* → config/store/collectors/atspi; cli → config/daemon/report/store; install_service → paths. Grep: no `from roxabi_sense.cli` / `surfaces` inside collectors or store. | Maintain: collectors must not import daemon/cli/report/surfaces. |
| A-P2-13 | — (positive) | `store/db.py:71-241` + collectors | **Store is single persistence truth.** All facts go through `Store.append` / `set_meta`; CLI/report only read. | Collectors take `Store` (`base.py:13-14`); daemon holds one `Store` for lifetime (`daemon.py:37`); CLI opens short-lived readers (`cli.py:162,202,231`). WAL + `busy_timeout=5000` (`db.py:81-83`) supports multi-process readers. | Keep writers on daemon/`once` path; surfaces stay readers. Avoid second DB. |
| A-P2-14 | — (positive) | `report/presence.py:54-165` | Presence is shared pure function above collectors (ADR-002), not reimplemented in CLI. | `derive_presence` + `presence_from_store`; CLI calls them (`cli.py:163`, offline path `cli.py:149-156`). | MCP/NATS must call same functions. |
| A-P2-15 | — (positive) | `daemon_collectors.py:81-94` + `handle_idle_msg` | Idle single-writer demotion matches ADR-002: Wayland healthy → logind collector dropped from poll set. | `want_logind_idle(..., wayland_healthy=True)` returns False for `auto` (`daemon_collectors.py:87-94`); rebuild on ready (`daemon.py:185-188`); shared writer `append_idle_transition`. | Keep; do not dual-write idle from both sources when Wayland ready. |
| A-P2-16 | — (positive) | `daemon.py` / `daemon_atspi.py` / `daemon_collectors.py` | Daemon split respects size gate: loop vs AT-SPI msgs vs collector assembly. | File roles clear; `run_daemon` ~250 LOC orchestration; helpers extracted. | Further extract respawn state machine only if loop grows again. |

---

### Metrics

| Metric | Value |
|--------|-------|
| P2 files audited | 11 modules (`store/`×2, cli, config, paths, daemon×3, install_service, `__init__`) |
| Store LOC (`db.py`) | ~242 |
| CLI LOC | ~297 |
| Daemon orchestration LOC | ~300 (`daemon.py`) + ~155 (`daemon_collectors`) + ~108 (`daemon_atspi`) |
| Circular import cycles | **0** |
| Store imports of collectors/daemon/cli/report | **0** |
| Collector imports of surfaces/cli/daemon | **0** |
| CLI commands | 8 (`status`, `day`, `recap`, `daemon`, `once`, `install-service`, `mcp` stub, `atspi-trace`) |
| Shared status query for multi-surface | Partial (presence yes; full status DTO no) |
| Kind strings in `TIMELINE_KINDS` with no live writer | 2 (`agent_session`, `media`) |
| Production-dead helpers in daemon_collectors | 2 (`FocusEventGate`, `tick_one`) |
| Meta keys written (approx.) | 10+ free-form strings |
| Schema migration / version | None |
| Severity counts | P0: 0 · P1: 0 · P2: 6 · P3: 5 · positive notes: 5 |

**Import DAG (intended / observed):**

```
paths ──► config
store (leaf)
collectors ──► store (+ atspi/util for focus)
report ──► store
daemon_collectors ──► collectors, config, store
daemon_atspi ──► atspi, collectors.focus_atspi, config, store
daemon ──► daemon_*, collectors (focus/idle_watch), config, store
cli ──► config, daemon, report, store, install_service, paths
install_service ──► paths
```

---

### Recommendations

1. **Before MCP (highest leverage):** extract a shared `status_snapshot` / store-backed status query so CLI and MCP never fork presence + meta assembly (closes A-P2-02; aligns ADR-001 “shared query functions”).
2. **Kind registry:** align collector `KIND`/`SNAPSHOT` with `STATUS_KINDS`/`TIMELINE_KINDS`; remove or implement `agent_session` / transition `media` (A-P2-01).
3. **Prune test-only daemon API:** drop production-dead `FocusEventGate` / stop re-exporting `tick_one` from `daemon` (A-P2-04, A-P2-05).
4. **Meta vocabulary:** optional constants + short doc for liveness keys vs probe debug keys (A-P2-06).
5. **Keep current strengths:** store leaf, no collector→surface imports, presence pure function, idle demotion on Wayland ready, WAL multi-reader — treat regressions as P1 on review.
6. **Out of scope for P2 but related:** report aggregation stays above store (correct); do not push recap compile into CLI or collectors.

**Confidence:** high for import/layer claims (full-tree grep + file reads); medium for “kinds never written” (based on current collector append sites + tests, not runtime DB inspection).
)
