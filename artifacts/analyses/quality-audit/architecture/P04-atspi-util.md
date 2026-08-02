# Architecture — P4: atspi + util + tools

**Partition:** `src/roxabi_sense/atspi/**`, `src/roxabi_sense/util/**`, `tools/**`  
**Domain:** Architecture (layering, isolation, purity, coupling)  
**Date:** 2026-07-31  
**Scope:** read-only audit; no source edits

### Summary

P4 is mostly well-shaped for a workstation sensor: **AT-SPI runtime is process-isolated** (system Python + `gi` in `agent_worker.py`, JSON-lines protocol, host package never imports Atspi), and **tools do not import the product package**. Layer direction is clean: `atspi` and `util` do not import collectors/store/report/surfaces.

The main architectural gap is the **missing focus backend interface** that ARCHITECTURE / ADR-001 call for (“Focus — AT-SPI or Cosmic/Wayland path… isolate behind interface”). Daemon and collector types are **AT-SPI-concrete** end-to-end (`FocusAtspiAgent`, `FocusAtspiCollector`, `source: "atspi"` in store payloads). Secondary issues: **`util` is not uniformly pure** (`agent_link` is domain join + tmux I/O; `resolve_app_name` carries AT-SPI “Unnamed” knowledge), and **tmux discovery is duplicated** with `TmuxSessionsCollector`. Tools coupling is effectively zero (CI/dev only).

No P0 (no reverse deps, no circular imports in this partition).

### Findings

| ID | Severity | File:Line | Finding | Evidence | Recommendation |
|----|----------|-----------|---------|----------|----------------|
| A-P4-01 | P1 | `src/roxabi_sense/daemon.py:10-11`, `daemon.py:38-43`, `daemon_collectors.py:119-120` | **No focus backend interface** — phase-2 isolation is incomplete. Architecture requires AT-SPI *or* Cosmic/Wayland behind an interface; only AT-SPI concrete types are wired. | Daemon imports `FocusAtspiAgent` + `FocusAtspiCollector` directly; `build_collectors` always appends `FocusAtspiCollector()`. No `Protocol`/`ABC` for “focus window list / focus events” outside the generic `Collector` tick protocol. | Introduce `FocusBackend` (or probe/agent protocol) under `collectors/` or `atspi/` consumers: start/stop, event callback, one-shot probe. Keep `FocusAtspi*` as one implementation. Daemon depends on the protocol; config selects backend. |
| A-P4-02 | P2 | `src/roxabi_sense/collectors/focus_atspi.py:158-161`, `focus_atspi.py:225` | **Platform identifier leaks into stored facts.** Focus/desktop payloads hardcode `"source": "atspi"` even though enrich/dedup is backend-agnostic once windows are dicts. | `_maybe_write_focus` sets `body["source"] = "atspi"`; `_desktop_payload` same. Collector docstring says probing lives in `atspi`, but store events bake the probe technology into the fact model. | Pass `source` from backend message or constructor (default `"atspi"`). Prefer generic `source: "focus"` + optional `probe: "atspi"` if NATS/report only need coarse provenance. Align with event model in `docs/ARCHITECTURE.md` if multi-backend ships. |
| A-P4-03 | P2 | `src/roxabi_sense/util/agent_link.py:1-18`, `agent_link.py:92-125`, `agent_link.py:229-272` | **`util/agent_link` is domain join logic, not a pure util.** It owns process-tree matching, tmux pane listing, title scoring, and Grok/Claude session attach — product “enrich” that only focus needs. | Module docstring: “Join window focus → Grok/Claude session”. Imports `subprocess` + hard-coded `/usr/bin/tmux`; `find_agent_link` is the focus enrich core used only from `focus_atspi._enrich`. | Move to `collectors/focus_enrich.py` or `join/agent_link.py` (or keep under collectors as private helpers). Leave `util` for pure/low-level helpers (`proc`, `titles`). |
| A-P4-04 | P2 | `src/roxabi_sense/util/agent_link.py:15-18`, `collectors/tmux_sessions.py:13-21` | **Duplicated platform coupling: two independent tmux discovery paths.** | `agent_link._TMUX` candidates + `list_tmux_agent_panes`; `TmuxSessionsCollector` redefines `_TMUX_CANDIDATES` and its own `list-panes` format. Different pane fields; no shared adapter. | Extract one `util/tmux.py` or collector-private helper: resolve binary once, list panes with a shared format, filter in callers. Avoid focus enrich and tmux collector drifting. |
| A-P4-05 | P2 | `src/roxabi_sense/util/proc.py:25-33` | **AT-SPI-specific app name knowledge lives in “low-level /proc helpers”.** | `resolve_app_name` docstring: “Map AT-SPI 'Unnamed' to real process name”; treats `Unnamed`/`unknown`/`Unknown` as empty. Module claims “no agent/tmux join logic” but still carries a11y-layer naming. | Keep `read_comm`/`children_map` pure. Move Unnamed→comm mapping next to focus enrich (or backend normalize before collector). Report already aliases `unnamed`→`ghostty` in `report/segments.py:19-21` — consolidate. |
| A-P4-06 | P3 | `src/roxabi_sense/collectors/focus_atspi.py:15`, `focus_atspi.py:238-243` | **Default probe path couples collector module to AT-SPI package at import/default.** Injection exists (`probe` / `probe_focus` callables) and is the right pattern, but defaults call `probe_once` so any import of default collector pulls atspi host agent. | `_default_probe_desktop` / `_default_probe_focus` call `probe_once("desktop"|"focus")`. Daemon event path uses `apply()` (good); poll/boot path uses defaults. | Keep injectable probes; optionally lazy-import `probe_once` inside defaults, or factory `FocusAtspiCollector.from_atspi()` so a future non-AT-SPI focus collector does not share this module name/defaults. |
| A-P4-07 | P3 | `src/roxabi_sense/util/session_registry.py:13-14`, `session_registry.py:69-74` | **Global default registry + hard-coded home agent paths in util.** Acceptable shared cache, but purity/configurability is weak; module-level singleton shared across collectors/focus enrich. | `_GROK_SESSIONS = Path.home() / ".grok" / ...`; `_default_registry = SessionRegistry()`; `default_registry()` returns singleton. | Prefer DI (already used by `AgentSessionsCollector(registry=...)`). Document singleton thread-safety assumptions for daemon. Paths belong in `paths.py` or config, not only hard-coded in util. |
| A-P4-08 | P3 | `src/roxabi_sense/atspi/agent_worker.py:1-19`, `tools/file_exemptions.txt:1-2` | **Worker is intentionally oversized (file-length exemption ~510 lines) and fully self-contained** — good isolation, but all a11y policy (event filter, multi-ACTIVE rules, name throttle, walk) lives in one script with no package-level structure. | Exemption: `src/roxabi_sense/atspi/agent_worker.py # 510 lines`. Worker imports only stdlib + `gi`; cannot share typed helpers with host package. | Keep process isolation. If growth continues, split worker into sibling modules loaded only under system Python (same dir, no hatch package imports), or document that host `script.py` owns config contract only. Not a layer violation. |
| A-P4-09 | P3 | `src/roxabi_sense/cli.py:109-110` | **CLI surface reaches into atspi diagnostic API** (`atspi-trace`). Fine for operator tooling; minor surface→platform coupling. | `cmd == "atspi-trace"` imports `default_trace_path`, `summarize_trace` from `atspi.trace_log`. | Acceptable. If MCP grows diagnostics, route through a thin report/diagnostic module so surfaces do not proliferate atspi imports. |
| A-P4-10 | P3 | `tools/license_check.py` (whole), `tools/*.sh` | **Tools coupling: none to product layers** (positive). Partition tools are repo quality/dev scripts only. | `tools/**/*.py` = `license_check.py` only; no `roxabi_sense` imports. Shell: file/folder gates, worktree, `qg.conf`. | Keep tools free of `src` imports. Do not move runtime collectors into `tools/`. |

**Positive architecture notes (no finding ID):**

- **AT-SPI process isolation is strong:** `agent_worker.py` never imports `roxabi_sense.*`; host `agent.py` spawns system Python, JSON stdin/stdout, stderr discarded (`agent.py:107-115`). Mirrors idle_watch subprocess pattern without embedding `gi` in the uv env.
- **Host atspi package is thin:** `script.py` (env/config only), `agent.py` (subprocess lifecycle), `trace_log.py` (diagnostic JSONL outside recap store — `trace_log.py:1`, note “Not used by recap”).
- **No upward deps from P4 packages:** greps show `atspi/` and `util/` do not import collectors, store, report, daemon, cli, or surfaces.
- **Collector still owns facts:** `FocusAtspiCollector.apply` / `_finish` write store rows; agent only emits raw window lists / events (`focus_atspi.py:2-4`). Correct primary-axis placement for enrich+write, even if backend interface is missing.

### Metrics

| Metric | Value |
|--------|------:|
| P4 Python modules (`atspi` + `util`) | 9 (atspi: 5 incl. `__init__`; util: 5 incl. `__init__`) |
| P4 tools Python modules | 1 (`license_check.py`) |
| tools shell/config helpers | 8 (checks, exemptions, worktree, `qg.conf`) |
| Approx. LOC atspi | ~850 (`agent_worker` ~490, `agent` ~197, `trace_log` ~145, `script` ~31) |
| Approx. LOC util | ~560 (`agent_link` ~273, `session_registry` ~178, `proc` ~79, `titles` ~55) |
| Reverse imports (atspi/util → collectors/store/report/surfaces) | **0** |
| Circular imports within P4 | **0** (util → util only; atspi → atspi + `paths` for trace) |
| Focus backend Protocol / ABC | **0** (only `Collector` tick protocol in `collectors/base.py`) |
| Collectors importing `roxabi_sense.atspi` | 1 (`focus_atspi`) |
| Collectors importing `roxabi_sense.util` | 2 (`focus_atspi`, `agent_sessions`) + report uses `util.titles` |
| Hardcoded `source: "atspi"` in store path | 2 sites in `focus_atspi` (+ worker emits same in protocol) |
| Dual tmux binary resolvers | 2 (`agent_link`, `tmux_sessions`) |
| File-length exemption | 1 (`agent_worker.py`) |
| Findings by severity | P0: 0 · P1: 1 · P2: 4 · P3: 5 |

### Recommendations

1. **(P1) Define a focus backend boundary before a second platform lands.**  
   Sketch:  
   - `FocusProbe` / `FocusEventSource` protocol: `start(on_message)`, `stop()`, optional `request_probe(mode)`.  
   - Message contract stays JSON-shaped dicts (`type`, `windows`, `mode`, `reason`) already used by `handle_atspi_msg`.  
   - Rename product collector over time toward `FocusCollector` + backend-supplied `source` string; keep `FocusAtspiAgent` as AT-SPI adapter under `atspi/`.

2. **(P2) Stop baking `atspi` into the fact schema by default.**  
   Facts should remain “which app/title had focus”; probe technology is metadata. Enables Cosmic/Wayland focus without store migration drama.

3. **(P2) Split `util` into pure vs domain.**  
   - Pure: `proc` (comm/cwd/tree only), `titles`.  
   - Domain: `agent_link`, possibly session path constants.  
   Reduces improper leakage of platform/product policy into shared helpers and clarifies what collectors may import.

4. **(P2) Single tmux adapter.**  
   One binary resolve + `list-panes` wrapper shared by focus enrich and `TmuxSessionsCollector`.

5. **(P3) Preserve worker isolation; do not “fix” by importing gi into the uv package.**  
   Current exemption and system-Python design are correct relative to PEP 668 / distro Atspi. Growth should stay in the worker process.

6. **(tools) No action.**  
   Quality-gate and license tools are correctly decoupled from runtime architecture.

7. **Verification greps for later waves / axial checks:**  
   - `from roxabi_sense.util.agent_link` (should stay focus-only until moved).  
   - `"source": "atspi"` under `collectors/`.  
   - `from gi` / `Atspi` outside `agent_worker.py` (must remain zero).  
   - `from roxabi_sense` under `tools/` (must remain zero).
