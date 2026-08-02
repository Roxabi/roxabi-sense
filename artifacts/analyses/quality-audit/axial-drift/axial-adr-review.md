# Axial ADR Review — ADR-001 (signal source / collector)

**Agent:** axial-adr-review  
**Date:** 2026-07-31  
**Scope:** whole tree under `src/roxabi_sense/` (not a PR diff)  
**ADR:** [`docs/architecture/adr/001-axis-of-decomposition.md`](../../../../docs/architecture/adr/001-axis-of-decomposition.md) (`axial: true`)  
**Primary axis:** signal source / collector  
**Non-primary:** surfaces (CLI / MCP / NATS), host / machine  

Tag: findings classified as `target-axis-trap` when they enable N×M (collectors × surfaces), surface-owned queries, host forks, or parallel collector paths without a shared base.

---

### Summary

**Verdict: mostly on-axis.** The living design matches ADR-001 composition intent: collectors emit facts, `store/` owns persistence + day/kind queries, `report/` owns cross-collector aggregation (`derive_presence`, day recap), CLI mostly formats. Canonical anti-pattern greps are clean (no `collectors → surfaces`, no query defs under `surfaces/`).

**No P0.** Risk is **latent N×M** before MCP/NATS land: status snapshot assembly and timeline text shaping still live in `cli.py` rather than a single store/report query used by all surfaces. Cross-collector scaffolding (timestamps, snapshot fingerprint, dual `SessionRegistry`, dual `tmux list-panes`) is the main on-axis smell — not surface drift, but multiplies bugs along the primary axis.

**Host axis:** clean — `machine` is config meta only (`config.py` / `meta.machine`); no host-named packages.

| Area | On-axis? | Notes |
|------|----------|--------|
| Collectors pure facts | Yes | No policy / Discord / NATS in collectors |
| Store shared queries | Partial | Day bounds + kind filters OK; no `status_snapshot` / `active_now` |
| Report aggregation | Yes | Correct “above collectors” layer (ADR-001 consequences + ADR-002) |
| CLI thin adapter | Partial | Recap/presence delegated; status body + `_summarize` still surface-local |
| surfaces/ package | Stub | Empty `__init__` — MCP/NATS not yet a second implementation |
| Host forks | No | Config string only |

---

### Findings

| ID | Severity | File:Line | Finding | Evidence | Recommendation |
|----|----------|-----------|---------|----------|----------------|
| AX-001 | P2 | `src/roxabi_sense/cli.py:138-194` | **issue (architecture):** Status snapshot is assembled in CLI, not as a shared store/report query — classic ADR-001 drift signature before MCP. `cmd_status` loads meta keys, `presence_from_store`, `count`, `last_event`, and `latest_by_kinds(STATUS_KINDS)` then shapes JSON/text. Second surface (`sense_status`, NATS health) will copy this shape unless extracted. Tag: `target-axis-trap`. | ```138:194:src/roxabi_sense/cli.py``` — missing-db path also inlines `derive_presence` + body assembly; happy path builds `body = {db, events, last_tick, …, presence}` only here. Architecture targets MCP `sense_status` / `active_now` over the same store. | Extract `status_snapshot(store, *, offline_threshold_s, idle_threshold_s) -> dict` into `report/` (or `store/` queries + report presence). CLI / future MCP / NATS only format/redact. Include `active_now` (focus + sessions from latest facts) in the same module when MCP ships. |
| AX-002 | P2 | `src/roxabi_sense/cli.py:243-292` | **issue (architecture):** Timeline event summarization is CLI-private (`_summarize` kind switch). `cmd_day` text path depends on it; JSON path dumps raw payloads. MCP `what_was_i_doing` or a third surface will reimplement the same kind→summary map (N×M). Tag: `target-axis-trap`. | ```243:292:src/roxabi_sense/cli.py``` — large per-kind switch (agent_sessions_snapshot, tmux, process, idle, media, focus, desktop…). Recap has separate human formatting in `report/day.py` (`format_day_recap`); two presentation compilers already diverge by design, but raw-timeline summary should be shared. | Move `summarize_event(kind, payload) -> str` (or structured short-form) next to store/report. CLI `day` and MCP timeline tool call it; keep `format_day_recap` for compiled view only. |
| AX-003 | P2 | `src/roxabi_sense/collectors/agent_sessions.py:31-37` vs `util/session_registry.py:69-90` | **issue (architecture):** Parallel session-registry path — agent-session collector builds a **private** `SessionRegistry()` while focus enrich uses module `_default_registry` via `load_all_sessions`. Same signal source (Grok/Claude session files) loaded through two cache instances → double mtime checks / divergent cache state on one poll tick. Tag: `target-axis-trap` (primary-axis parallel path). | ```31:37:src/roxabi_sense/collectors/agent_sessions.py``` creates `SessionRegistry(...)` when `registry is None`. Focus: ```78:78:src/roxabi_sense/collectors/focus_atspi.py``` `sessions_loader or load_all_sessions` → ```89:90:src/roxabi_sense/util/session_registry.py``` `_default_registry`. | Default `AgentSessionsCollector` to `default_registry()` (or inject one shared registry from daemon). One cache for all consumers of agent session facts. |
| AX-004 | P2 | `collectors/tmux_sessions.py:13-48` + `util/agent_link.py:15-125` | **issue (architecture):** Parallel-path tmux probing — `TmuxSessionsCollector` and `list_tmux_agent_panes` each resolve `/usr/bin/tmux` and run `list-panes -a` with different format strings. Same OS signal, two implementations, no shared base. Poll ticks pay double subprocess when both focus enrich and tmux collector run. Tag: `target-axis-trap`. | ```13:21:src/roxabi_sense/collectors/tmux_sessions.py``` `_TMUX_CANDIDATES` + list-panes fmt with session/window. ```15:17:src/roxabi_sense/util/agent_link.py``` separate `_TMUX`; ```92:125``` list-panes fmt pane_pid/command/path. | Shared `util/tmux.py` (or collector helper): resolve binary once, raw pane rows once per tick; snapshot collector + agent_link project different views. Optionally pass panes from daemon tick into focus enrich to avoid a second call when snapshot already ran. |
| AX-005 | P2 | `collectors/{agent_sessions,process_presence,mpris,tmux_sessions,focus_atspi,idle}.py` | **suggestion (architecture):** Cross-cutting snapshot write pattern (fingerprint → skip → `store.append(*_snapshot)`) is copy-pasted across collectors; `collectors/base.py` is Protocol-only. Expected some parallelism on the primary axis, but change-detection/write is identical scaffolding, not signal-specific. Tag: `target-axis-trap` (mild). | Fingerprint blocks: agent_sessions L40-47, process_presence L24-31, mpris L33-47, tmux L23-31, focus desktop_fp L137-142, idle key tuple L37-40. `base.py` L10-15 only defines `tick`. | Optional thin helper: `append_if_changed(store, kind, payload, last_fp) -> tuple[int, str\|None]` or small `SnapshotCollector` mixin. Keep probe logic per collector. |
| AX-006 | P2 | `store/db.py:55-60`, `report/presence.py:33-34`, `report/segments.py:49-54`, `collectors/idle_facts.py:13-18`, `daemon_collectors.py:23-24` | **issue (architecture):** Timestamp parse/format helpers triplicated (cross-cutting concern along every axis). `parse_ts` ×3, `_to_z`/`_utc_now` ×3+, plus `time.strftime` UTC stamp in daemon. Risk: subtle Z/offset mismatches between idle facts, presence age, and recap segments. Tag: `target-axis-trap` (cross-cutting). | Identical `fromisoformat(ts.replace("Z", "+00:00"))` in presence L33-34, segments L49-50, idle_facts L17-18. `_to_z` in store L59-60, segments L53-54, idle_facts L13-14. Daemon `_utc_stamp` uses `strftime` (no subsecond, different path). | Single `roxabi_sense.util.time` (or export from store): `parse_ts`, `to_z`, `utc_now`. Daemon and idle_facts import it; report re-exports if needed for tests. |
| AX-007 | P2 | `report/segments.py:19-21,57-59` vs `util/proc.py:25-33` | **issue (architecture):** App identity normalized twice on different axes — write-time `resolve_app_name` (Unnamed→comm via /proc) vs recap-time `norm_app` (`unnamed`→`ghostty` alias). If AT-SPI still emits `Unnamed`/`unnamed` without pid resolution, recap hardcodes ghostty while other code treats terminal as unnamed. Dual truth for the same focus app label. Tag: `target-axis-trap`. | Write: ```181:181:src/roxabi_sense/collectors/focus_atspi.py``` `resolve_app_name`. Recap: ```19:21:src/roxabi_sense/report/segments.py``` `_APP_ALIASES` + ```194:194``` `norm_app` on every focus segment. agent_link treats both `ghostty` and `unnamed` as terminals (L14). | Prefer write-time truth only; drop recap alias or share one `normalize_app()` used at write and (defensively) at read. Avoid host-specific app guesses in report. |
| AX-008 | P3 | `src/roxabi_sense/cli.py` vs `surfaces/__init__.py:1` + ADR tree map | **nit (architecture):** ADR maps `surfaces/` as secondary adapters and `cli.py` as thin entry; package `surfaces/` is an empty docstring stub while all surface behavior lives in package-root `cli.py`. Not N×M yet, but MCP/NATS will land under `surfaces/` while CLI stays outside — split surface axis. | ADR-001 L89-96 tree; `surfaces/__init__.py` only documents intent; `cli.py` ~297 LOC owns argparse + three commands + summarizer. | When MCP ships: either move CLI command bodies to `surfaces/cli.py` (entry re-exports) or document that package-root CLI + `surfaces/{mcp,nats}` all call `report`/`store` only. Prefer shared query module over relocating for its own sake. |
| AX-009 | P3 | `report/segments.py:16`, `report/presence.py:13-14`, `config.py:44-50`, `collectors/idle.py:24` | **nit (architecture):** Idle/offline thresholds defaulted in four places (`300.0` / `120.0`). ADR-002 wants one config value for notify + degraded gap; recap `IDLE_GAP_S` is a separate constant not wired from `SenseConfig`. If operator changes `idle_threshold_s`, status uses config but day recap gap stays 300 unless edited. Tag: cross-cutting config (not host fork). | `IDLE_GAP_S = 300.0` hard-coded; `compile_day_recap` uses it without store/config. CLI passes `cfg.idle_threshold_s` into presence only. | Thread `idle_threshold_s` into `compile_day_recap` / `away_segments` from config (default 300). Single source in `config` or shared constants module. |
| AX-010 | P3 | `src/roxabi_sense/` (tree) | **praise (architecture):** No host-axis fork; no collector→surface imports; idle dual-writer avoided via shared `append_idle_transition` + daemon demotion. Positive controls against wrong primary axis. | Grep: no `from roxabi_sense.surfaces` under collectors; `machine: str = "laptop"` config only; `handle_idle_msg` + `IdleCollector` both call `append_idle_transition` (`idle_facts.py`); `want_logind_idle` demotes logind when wayland healthy. | Keep: new surfaces must import `report.presence` / `report.day` / store queries only. New hosts = config + machine id. |

---

### Metrics

| Metric | Value |
|--------|------:|
| Collectors (tick writers) | 6 (`agent_sessions`, `process_presence`, `idle` logind, `mpris`, `tmux`, `focus_atspi`) + event path idle_watch / AT-SPI agent |
| Surface implementations with query logic | 1 (`cli.py`); `mcp` stub returns 2; NATS absent |
| `surfaces/` modules with code | 0 (package docstring only) |
| Shared aggregation modules | `report/` (day, presence, segments, enrich, meeting) + `store/db.py` queries |
| Collectors importing surfaces | **0** |
| Query-like `def` under `surfaces/` | **0** |
| Host-named packages / forks | **0** |
| Distinct `parse_ts` implementations | **3** (presence, segments, idle_facts) |
| Distinct UTC stamp helpers | **4+** (store `_utc_now`/`_to_z`, segments `to_z`, idle_facts `_to_z`, daemon `_utc_stamp`) |
| Snapshot fingerprint patterns (parallel) | **5** collectors |
| Dual session registries | **2** (collector instance + module default) |
| Dual tmux `list-panes` call sites | **2** |
| Findings P0 / P1 / P2 / P3 | **0 / 0 / 7 / 3** |
| Latent N×M risk (MCP/NATS) | **High on status + day summary; low on presence + recap** |

**Structural greps (ADR-001 anti-patterns):**

| Pattern | Result |
|---------|--------|
| `from roxabi_sense.surfaces` in collectors | clean |
| `def (status\|day\|active_now\|…)` under `surfaces/` | clean (no modules) |
| `surfaces/.*/collect` | clean |
| `laptop/` / `m2/` packages | clean |

---

### Recommendations

1. **Before MCP (hard gate for N×M):** Extract surface-neutral APIs used by CLI today:
   - `status_snapshot(store, thresholds) -> dict` (AX-001)
   - `summarize_event` / short timeline rows (AX-002)
   - Keep `presence_from_store` + `compile_day_recap` as the only presence/day compilers (already good).
2. **Primary-axis hygiene (collectors):**
   - Share one `SessionRegistry` (AX-003).
   - Share one tmux pane probe; project to snapshot vs agent-link (AX-004).
   - Optional snapshot mixin for fingerprint writes (AX-005).
3. **Cross-cutting utils:** One time module (AX-006); one app-name normalize (AX-007); wire recap gap to `idle_threshold_s` (AX-009).
4. **Surface layout:** Document or relocate CLI under `surfaces/` when MCP lands so the secondary axis is one package with thin adapters only (AX-008).
5. **Do not:** Put collector logic in MCP/NATS; do not fork by host; do not reimplement `derive_presence` per surface (already protected).

**Residual risk if ignored:** Shipping MCP tools that re-query SQLite and re-derive “active/offline” or status JSON in a new module would realize the Option-Surface anti-pattern named in ADR-001. Presence path is safe; status and raw day summary are the soft spots.
)
