# Code Quality Audit Summary

**Project:** roxabi-sense  
**Playbook:** multi-agent-audit-playbook.md@1.1  
**Date:** 2026-07-31  
**Scale:** ~55 Python files · ~18 agents · 8 waves  
**Verify wave:** pyright 0/0 · pytest 89 passed (1.72s) · cocoindex/ccc N/A  

---

## Executive Summary

**Overall health: good foundation, pre-MCP readiness debt.**  
roxabi-sense is a small, well-layered local attention sensor. The primary axis (signal source / collector) is clean: collectors emit store facts only, presence and day recap live in `report/`, CLI is mostly a thin adapter, import graph is acyclic, and ADR-001 anti-pattern greps are empty. No P0 findings under the local single-user threat model.

**Security posture: solid for local same-user.**  
No `shell=True`, parameterized SQLite, fixed binary candidates for tmux/playerctl, title C0 sanitization on focus path, DB hardened to `0700`/`0600`. Residual risk is DoS/privacy (untrusted agent JSON size/symlinks, AT-SPI a11y privilege + full env inheritance, optional title firehose in trace, path overrides via env/config) — not remote RCE.

**Test coverage: strong core, uneven on the collector axis.**  
Store, report (presence/day/meeting), focus enrich, and agent sessions are well tested. Three production collectors (`mpris`, `tmux_sessions`, `idle_watch`), `load_config`/`paths`, and daemon AT-SPI/idle message glue lack dedicated tests. Two host-coupled tests hit real HOME/systemctl. One AT-SPI “test” never executes production code.

**Key debt:** MCP/NATS surfaces empty (CLI stub only); package still labeled `0.0.1`/scaffold while daemon+collectors ship; `run_daemon` and `agent_worker` are god units; latent N×M if MCP copies CLI status/`_summarize` instead of shared report APIs; sensor hardening (IdleWatch stop, queue bounds, corrupt-row crash).

**Axial status: clean today; protect before second surface.**  
No collectors→surfaces, no host package forks, single presence SM and day compiler. Soft spots: status snapshot + raw day summary still CLI-local; dual SessionRegistry and dual tmux probes on the primary axis.

---

## Critical Issues (P0)

**None.**

All domains report zero P0. Positive P0-class gates (facts-only collectors, no circular imports) hold.

---

## High Priority (P1)

| ID | Finding | Domains | Evidence (key) | Recommendation |
|----|---------|---------|----------------|----------------|
| **S1** | **MCP / surfaces structural gap** — `surfaces/` is docstring-only; `sense mcp` returns 2; no shared `status_snapshot` / `active_now` / timeline DTOs; NATS absent (config skeleton only). Latent N×M if second surface forks CLI. | Architecture P3, Tech-debt P2/P3, Axial ADR (latent) | `surfaces/__init__.py`; `cli.py:103-108`; ARCHITECTURE MCP tools list | Before MCP: extract `status_snapshot` + `summarize_event` into `report/`; scaffold `surfaces/mcp.py` over store/report only; never call collectors from MCP. |
| **S2** | **Product honesty: version + README still “scaffold”** while collectors, daemon, recap ship; dual `__version__` / pyproject `0.0.1`. | Tech-debt P2 | `__init__.py`, README status banner | Bump version on next release; derive `__version__` from metadata; rewrite status to phases 1–2 done. |
| **S3** | **`run_daemon` god function (~250 LOC)** — signals, dual respawn, queues, poll/desktop deadlines in one body; file at QG length gate (~299). | Code-smells | `daemon.py:36-285` | Extract drain/respawn/poll helpers or `DaemonRuntime`; target ≤80 LOC loop body. |
| **S4** | **`agent_worker.py` oversized (490 LOC, only QG exemption)** — walk/coalesce/filter/stdin/main; highest latent defect surface; pyright-excluded. | Code-smells, Type-safety, Tech-debt P4 | `tools/file_exemptions.txt`; worker module | Split sibling modules under system-python worker dir; keep process isolation (no `gi` in uv env). |
| **S5** | **`IdleWatch.stop` incomplete** — no stdout close, no thread join, kill without wait; `ready` race on respawn vs old reader `finally`. | Async-patterns | `idle_watch.py:205-249` vs `FocusAtspiAgent.stop` | Mirror AT-SPI host stop protocol; generation-safe `ready` flag. |
| **S6** | **Unbounded `atspi_q` / `idle_q` + blocking `tick_all`** starves event drain and delays SIGTERM during poll. | Async-patterns | `daemon.py` queues + poll path | Cap/coalesce queues; drain before heavy poll; optional stop-check between collectors. |
| **S7** | **Unguarded `json.loads` on store event rows** — corrupt payload crashes all readers (status/day/recap). | Error-handling, Security (P3 sibling) | `store/db.py:238` | Catch `JSONDecodeError`; mark/skip corrupt row; never crash surface. |
| **S8** | **`load_config` fail-closed with traceback** — bad TOML / type coercion aborts CLI unmapped. | Error-handling, Test-quality (untested) | `config.py:73` | Wrap load+coerce; stderr + exit 2; add `test_config` matrix. |
| **S9** | **Focus backend interface missing** — daemon/collectors AT-SPI-concrete end-to-end; ARCHITECTURE calls for interface before Cosmic/Wayland. | Architecture P4 | `daemon.py` imports `FocusAtspi*`; `source: "atspi"` in facts | Introduce `FocusBackend` protocol; pass `source`/probe meta; keep AT-SPI as one impl. |
| **S10** | **Test trust gaps on primary axis** — (a) false AT-SPI test reimplements `window_from_src`; (b) untested `MprisCollector`, `TmuxSessionsCollector`, `IdleWatch`; (c) host-coupled `once` + `systemctl daemon-reload`. | Test-quality, Code-smells T01 | `test_atspi_agent.py:33-60`; collector map; `test_idle_transitions` / `test_install_service` | Fix/remove mirror test; unit-test three collectors + pure IdleWatch helpers; hermetic once/install. |
| **S11** | **Type holes at assembly/IPC** — `list[Any]` collectors (unused `Collector` Protocol); `Event.payload: dict[str, Any]`; dual `apply` list types force `type: ignore`. | Type-safety, Architecture P1 | `daemon_collectors.py:97-144`; `store/db.py` Event; `focus_atspi.apply` | Wire `list[Collector]`; normalize windows at IPC boundary; start TypedDicts for stable facts. |

---

## Medium Priority (P2)

Deduplicated clusters (same root cause → one entry):

| Cluster | Domains | Summary | Action |
|---------|---------|---------|--------|
| **Latent N×M: CLI owns status + `_summarize`** | Axial ADR, Arch P2, Code-smells | `cmd_status` and kind→text map live in `cli.py`; MCP will copy unless extracted | Extract to `report/` before MCP (ties to S1) |
| **Primary-axis parallel paths** | Axial ADR, Arch P1/P4, Code-smells | Dual `SessionRegistry`; dual tmux `list-panes`; fingerprint-and-skip ×5 collectors | Shared registry; `util/tmux.py`; optional `append_if_changed` |
| **Timestamp / threshold / kind catalog drift** | Axial, Arch P2, Code-smells, Tech-debt | `parse_ts`/`to_z` ×3+; idle 300s in 4 places; `TIMELINE_KINDS` includes unwritten `agent_session`/`media` | `util.time`; single constants; prune or implement dead kinds |
| **App identity dual truth** | Axial ADR, Arch P4 | Write-time `resolve_app_name` vs recap `unnamed→ghostty` | One normalize at write; drop host-specific recap alias |
| **Security hardening (local)** | Security P1+P2, P3+P4 | Agent JSON no size/symlink guard; env path overrides; `--limit -1` unbounded; install-service unquoted ExecStart + PATH which; AT-SPI full env inherit; trace raw titles | Size cap + skip symlinks; clamp limit; quote unit; minimal agent env; sanitize+chmod 0600 trace |
| **Daemon dead / dual models** | Arch P2, Code-smells, Tech-debt | `FocusEventGate` tested not wired; `tick_one` test-only re-export; unused `source` param | Delete or wire one rate-limit SSOT |
| **Observability of fail-open sensors** | Error-handling, Async | Collector errors print-only; idle callback `pass`; probe_once silent empty; AT-SPI `type=error` not in meta | Meta counters + status visibility; log idle callback |
| **Poll-path wall time** | Async-patterns | Logind multi-subprocess; 5s one-shot AT-SPI; full `/proc`+tmux every focus enrich | Cache session; prefer long-lived agent; TTL cache tree/panes |
| **Store lifecycle** | Arch P2, Tech-debt | No schema version/migrations; no retention/prune (desktop_snapshot growth) | `schema_version` + retention stub before first breaking change |
| **Package / util purity** | Arch P1/P4 | IdleWatch under collectors (vs atspi isolation); `agent_link` is domain join in `util/` | Document or move; split pure util vs focus enrich |
| **Report presentation mix** | Arch P3, Tech-debt | `format_*` in report; idle gap hard-coded not from config | Thread `idle_threshold_s` into recap; mark formatters transitional |
| **Binary / FHS portability** | Tech-debt P1/P4 | Hard-coded tmux/playerctl/python paths | Shared `resolve_bin` + env overrides (`SENSE_ATSPI_PYTHON` mirror idle) |
| **Test suite DRY / structure** | Code-smells T01, Test-quality | No conftest; focus enrich paste; overlapping daemon modules; zero parametrize | `conftest` store fixture; collapse mocks; merge daemon tests |

---

## Low Priority (P3)

**Themes (not every finding):**

- Magic numbers / display caps (timeouts, PID[:8], recap `[:12]` slices, GLib 50/80 ms) — name constants when next touched.
- Dead API surface: `claude_history` field, `file_signature`, weak re-exports, dead `_summarize` kind branches.
- Meta key free-form strings (no registry); focus collector writes probe meta.
- Report/CLI privacy: full cwd/media in recap text; C1/bidi not stripped; no MCP redaction yet (correct until MCP).
- Schema/docs: architecture index omits `report/` layer; report docstring says “compiled surfaces”; deploy unit comment stale; many docs standards still TODO scaffold.
- Type hygiene: free `str` enums, bare `dict` in CLI/`sum_by`, Literal `type: ignore`, tools outside pyright.
- Files near 300 LOC gate (`daemon`, `cli`, `segments`) — extract before MCP/NATS growth.
- Empty `test_cli_scaffold.py`; testing standards doc unfinished.
- Acceptable by design: AT-SPI worker broad `except` (GI flaky tree); Linux `/proc`; no asyncio (keep Store single-threaded).

---

## Axial Drift Summary

| Axis | Violations | N×M Traps | Confirmations |
|------|------------|-----------|---------------|
| **Primary: signal source / collector** | **0** layer violations | **Latent:** dual SessionRegistry; dual tmux probe; fingerprint copy-paste; status/`_summarize` still surface-local | Collectors → store/util/atspi only; no surfaces/report/cli imports; facts-only (no Discord/jobs/NATS/policy); idle dual-write avoided via demotion + shared writer |
| **Secondary: surfaces** | Package empty; live surface = root `cli.py` | **High risk on status + day text** if MCP clones CLI; **low** on presence + recap (already shared) | ADR greps under `surfaces/` clean (no modules); presence_from_store + compile_day_recap used by CLI |
| **Non-primary: host/machine** | **0** | None | `config.machine` string only; no `laptop/` / `m2/` packages |
| **importlinter** | **N/A** (no config; intentional until surfaces stabilize) | — | Manual import DAG acyclic |

**Axial verdict:** axial-clean for current single-surface tree. Hard gate before MCP: extract shared query products; ban MCP→collectors and MCP-private SQL.

---

## Metrics Dashboard

Counts are **as reported by domain agents** (pre-dedup). Unique issues after merge are lower (~55–70 actionable clusters).

| Domain | Issues | P0 | P1 | P2 | P3 |
|--------|-------:|---:|---:|---:|---:|
| Axial structural | 5 | 0 | 0 | 0 | 5 |
| Axial ADR review | 10 | 0 | 0 | 7 | 3 |
| Architecture P1 collectors | 8 | 0 | 0 | 3 | 5 |
| Architecture P2 store/core | 11 | 0 | 0 | 6 | 5 |
| Architecture P3 report/surfaces | 9 | 0 | 3 | 3 | 3 |
| Architecture P4 atspi/util | 10 | 0 | 1 | 4 | 5 |
| Security P1+P2 | 12 | 0 | 0 | 5 | 7 |
| Security P3+P4 | 18 | 0 | 0 | 4 | 14 |
| Code smells src | 32 | 0 | 2 | 18 | 12 |
| Code smells tests | 18 | 0 | 2 | 8 | 8 |
| Type safety | 18 | 0 | 3 | 9 | 6 |
| Async patterns | 13 | 0 | 2 | 7 | 4 |
| Error handling | 17 | 0 | 2 | 7 | 8 |
| Test quality | 23 | 0 | 6 | 11 | 6 |
| Tech debt P1 | 13 | 0 | 0 | 5 | 8 |
| Tech debt P2 | 16 | 0 | 3 | 7 | 6 |
| Tech debt P3 | 13 | 0 | 1 | 4 | 8 |
| Tech debt P4 | 14 | 0 | 0 | 6 | 8 |
| **Deduped summary (this doc)** | **~45 clusters** | **0** | **11** | **~13 clusters** | **themes** |

**Verify metrics**

| Check | Result |
|-------|--------|
| pyright | 0 errors, 0 warnings |
| pytest | 89 passed in 1.72s |
| cocoindex / ccc | **N/A** (not initialized); greps used for DRY/axial confirmation |
| Production modules | ~35 under `src/roxabi_sense` |
| Test modules | 17 (1 empty stub) |

---

## Recommended Actions

Prioritized; effort **S** (&lt;1d) · **M** (1–3d) · **L** (&gt;3d).

| # | Action | Effort | Closes |
|---|--------|--------|--------|
| 1 | Guard `Store._row` JSON + `load_config` errors (stderr/exit 2) | S | S7, S8 |
| 2 | Clamp CLI/store `limit` to positive max | S | SEC-04 |
| 3 | Fix `IdleWatch.stop` (parity with AT-SPI agent) | S | S5 |
| 4 | Delete/wire `FocusEventGate`; stop re-exporting test-only `tick_one`; drop unused `source` | S | P2 dead code |
| 5 | Replace false AT-SPI test; delete `test_cli_scaffold.py`; hermetic once/install | S | S10 |
| 6 | Unit tests: mpris, tmux_sessions, IdleWatch helpers, `load_config` | M | S10 |
| 7 | Extract `status_snapshot` + `summarize_event` into `report/` | M | S1, axial |
| 8 | Single `util.time` + share SessionRegistry + `util/tmux` | M | P2 DRY/axial |
| 9 | Wire `list[Collector]`; normalize AT-SPI windows at boundary | M | S11 |
| 10 | Split `run_daemon`; plan agent_worker modularization | M–L | S3, S4 |
| 11 | Agent JSON size cap + symlink refusal; minimal AT-SPI env; chmod 0600 trace | M | Sec P2 |
| 12 | Version/README honesty; kind catalog prune; schema_version stub | S–M | S2, TD store |
| 13 | Focus backend protocol + config-driven recap idle gap | M–L | S9, AX-009 |
| 14 | Implement MCP under `surfaces/` over report/store only (after 7) | L | S1 |
| 15 | Retention prune + queue bounds in daemon | M | TD, S6 |

---

## Technical Debt Score

### **74 / 100**

**Rationale (100 = pristine):**

| Factor | Impact |
|--------|--------|
| Axial cleanliness, acyclic layers, facts-only collectors | + strong |
| Shared presence/recap, WAL store, fail-open sensors | + strong |
| pyright clean (basic), 89 green tests on core paths | + good |
| Local security hygiene (no shell, parameterized SQL, DB modes) | + good |
| Empty MCP/NATS + scaffold versioning narrative | −6 |
| God daemon + exempted worker + incomplete IdleWatch stop | −6 |
| Untested primary-axis collectors + false AT-SPI test | −5 |
| Latent N×M (CLI status/summary), dual probes, time/threshold drift | −5 |
| No migrations/retention; path/env trust model | −4 |

Score reflects a **healthy phase-1/2 sensor** not yet production-hardened for multi-surface or multi-host install. Score would approach **85+** after S1 extract + S5/S7/S8/S10 quick hardening and collector tests.

---

## Top 10 Quick Wins

High impact, low effort (prefer P0/P1/security/correctness):

1. **Catch corrupt event JSON in `Store._row`** — stop CLI/daemon crash on bad row.
2. **Guard `load_config`** — friendly exit 2 instead of traceback.
3. **`limit = max(1, min(limit, MAX))`** on day/query paths.
4. **Complete `IdleWatch.stop`** — copy FocusAtspiAgent teardown + safe `ready`.
5. **Log idle-watch callback errors** (parity with AT-SPI host).
6. **Delete `FocusEventGate` + dead branches** (`claude_history`, unused kinds in CLI when pruning).
7. **Type `tick_all` / `build_*` as `list[Collector]`** — one-line contract revival.
8. **Fix/remove `test_window_from_src_logic`** — green must mean production ran.
9. **Hermeticize install-service + once tests** (monkeypatch systemctl; all collectors off).
10. **Centralize `parse_ts` / `to_z`** in `util.time` — prevents Z/offset drift.

---

## Verification

| Item | Result |
|------|--------|
| **pyright** | 0 errors, 0 warnings (`typeCheckingMode: basic`; `agent_worker.py` excluded) |
| **pytest** | **89 passed** in 1.72s |
| **cocoindex / ccc** | **N/A** — index not initialized in this repo; structural greps substituted for DRY/axial confirmation |
| **Agents** | ~18 domain agents across 8 waves (axial ×2, arch ×4, sec ×2, smells ×2, types, async, errors, test-quality, tech-debt ×4, cocoindex N/A, synthesis) |
| **Duration** | Est. playbook 12–20 min multi-agent; synthesis wave 8 |

---

## Report Index

All paths under `artifacts/analyses/quality-audit/`:

| Domain | Report |
|--------|--------|
| Strategy | [STRATEGY.md](STRATEGY.md) |
| Axial structural | [axial-drift/importlinter-report.md](axial-drift/importlinter-report.md) |
| Axial ADR | [axial-drift/axial-adr-review.md](axial-drift/axial-adr-review.md) |
| Architecture P1 | [architecture/P01-collectors.md](architecture/P01-collectors.md) |
| Architecture P2 | [architecture/P02-store-core.md](architecture/P02-store-core.md) |
| Architecture P3 | [architecture/P03-report-surfaces.md](architecture/P03-report-surfaces.md) |
| Architecture P4 | [architecture/P04-atspi-util.md](architecture/P04-atspi-util.md) |
| Security P1+P2 | [security/P01-P02.md](security/P01-P02.md) |
| Security P3+P4 | [security/P03-P04.md](security/P03-P04.md) |
| Code smells src | [code-smells/P01-P04.md](code-smells/P01-P04.md) |
| Code smells tests | [code-smells/T01.md](code-smells/T01.md) |
| Type safety | [type-safety/P01-P04.md](type-safety/P01-P04.md) |
| Async patterns | [async-patterns/P01-P04.md](async-patterns/P01-P04.md) |
| Error handling | [error-handling/P01-P04.md](error-handling/P01-P04.md) |
| Test quality | [test-quality/T01.md](test-quality/T01.md) |
| Tech debt P1 | [tech-debt/P01.md](tech-debt/P01.md) |
| Tech debt P2 | [tech-debt/P02.md](tech-debt/P02.md) |
| Tech debt P3 | [tech-debt/P03.md](tech-debt/P03.md) |
| Tech debt P4 | [tech-debt/P04.md](tech-debt/P04.md) |
| **This summary** | [AUDIT-SUMMARY.md](AUDIT-SUMMARY.md) |
