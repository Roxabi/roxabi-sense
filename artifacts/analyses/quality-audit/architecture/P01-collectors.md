# Architecture audit — P1 collectors

**Partition:** `src/roxabi_sense/collectors/**/*.py`  
**Date:** 2026-07-31  
**Axis (ADR-001):** signal source / collector  
**Refs:** ADR-001, ADR-002, `docs/ARCHITECTURE.md`, `docs/standards/backend-patterns.md`, AGENTS.md

### Summary

P1 collectors are **architecturally healthy on the primary axis**. They emit typed store facts only; there are **no** imports from `surfaces/`, `report/`, `cli`, or policy planes; **no** Discord messaging, job dispatch, Sentinelle policy, or NATS publish inside collectors; **no** circular import cycles involving this package.

Main debt is **contract clarity**, not layer collapse:

1. `base.Collector` is a stub Protocol that nothing implements or type-checks against; the real surface of `FocusAtspiCollector` and runtime helpers (`IdleWatch`) exceeds or sits outside `tick`.
2. **Runtime infrastructure** (`idle_watch`) lives under `collectors/` while the sibling AT-SPI runtime correctly lives under `atspi/` — asymmetric package boundaries.
3. Soft coupling: default focus probe → `roxabi_sense.atspi`; dual `SessionRegistry` instances (agent_sessions vs shared util default); focus writes probe meta (telemetry mixed with fact path).

**Facts-only rule: held.** Layer graph is one-way `collectors → store | util | atspi | paths`. Daemon owns assembly, demotion, and liveness meta (ADR-002).

### Findings

| ID | Severity | File:Line | Finding | Evidence | Recommendation |
|----|----------|-----------|---------|----------|----------------|
| A-P1-01 | P2 | `collectors/base.py:10-15` | **Collector contract is dead code.** `Collector` Protocol defines only `name` + `tick(store) -> int` but is never imported. Assembly uses `list[Any]`; structural typing is accidental, not enforced. | `base.py` Protocol unused (repo grep: only definition). `daemon_collectors.py:97-101` returns `list[Any]`; `tick_all` accepts `list[Any]`. | Export `Collector` from `collectors/__init__.py`; type `build_poll_collectors` / `tick_all` as `list[Collector]`; either extend Protocol with optional methods or document Focus as “Collector + apply/tick_* extensions.” |
| A-P1-02 | P2 | `collectors/focus_atspi.py:84-111` | **Focus collector exceeds the base contract** without a documented sub-protocol: `tick`, `tick_focus`, `tick_desktop`, and `apply` are all first-class write paths used by daemon event mode. Reviewers cannot see the contract from `base.py`. | `tick` → full; `tick_focus` / `tick_desktop` / `apply` used from daemon/atspi path (`daemon_atspi.py:67` `focus.apply`). Protocol only lists `tick`. | Introduce `FocusCollector(Protocol)` with `apply`/`tick_*`, or document in `base.py` that event-driven collectors may expose `apply` while poll collectors stay `tick`-only. |
| A-P1-03 | P2 | `collectors/idle_watch.py:146-203` | **IdleWatch is a long-lived runtime (subprocess + thread), not a fact collector**, yet lives under `collectors/`. Mirrors AT-SPI which was deliberately moved to `atspi/` (`__init__.py` comment). Blurs primary-axis package meaning: “one signal source module” vs “daemon I/O helpers.” | Class starts Popen + daemon thread; no `tick`/`name`. Wired from `daemon.py:12,104`. Not exported from `collectors/__init__.py`. Contrast: `__init__.py:10` “Long-lived AT-SPI runtime lives in roxabi_sense.atspi (not a fact collector).” | Prefer `roxabi_sense.idle/` or keep under daemon-adjacent package; leave `idle_facts.append_idle_transition` as the pure writer used by both logind collector and watch path. Or document idle_watch as “platform adapter for idle signal” colocated with IdleCollector by exception. |
| A-P1-04 | P3 | `collectors/focus_atspi.py:132-134` | **Focus collector writes store meta** (`focus_probe_*`) on every finish. ADR-002 assigns **daemon liveness meta** to the coordinator; probe counters are telemetry, not facts. Soft layer bleed: collector mutates global meta keys. | `store.set_meta("focus_probe_count"…)`, `focus_probe_last_ms`, `focus_probe_last_mode` inside `_finish`. Daemon separately owns `last_tick`, `idle_watch`, `atspi_agent`. | Keep allowed if documented as “collector telemetry meta”; alternatively move counters to daemon after `apply`/`tick` return, or namespace keys under a clear prefix and never use them for presence. |
| A-P1-05 | P3 | `collectors/focus_atspi.py:15-16,238-243` | **Default path couples collectors → atspi.** Injected `probe`/`probe_focus` keep tests pure, but production default imports `probe_once` from the long-lived AT-SPI package. Acceptable DI; still a directed dependency collectors → platform runtime. | `from roxabi_sense.atspi import probe_once`; `_default_probe_*` call `probe_once`. Daemon event path avoids this (uses `apply`). | Keep injectable probes (already good). Optionally move default one-shot probe factory into daemon assembly so `FocusAtspiCollector` only depends on util + store for pure enrich/write. |
| A-P1-06 | P3 | `collectors/agent_sessions.py:31-37` vs `util/session_registry.py:69-90` | **Dual SessionRegistry instances.** Agent sessions collector constructs its own registry; focus enrich uses module-level `_default_registry` via `load_all_sessions`. Same signal source, two caches — not a cycle, but unclear ownership of the agent-session fact source. | AgentSessionsCollector: `SessionRegistry(...)` private. Focus: `sessions_loader or load_all_sessions` → `_default_registry`. | Share `default_registry()` in AgentSessionsCollector unless tests inject a path-specific registry; document “agent session SSOT = util.session_registry.” |
| A-P1-07 | P3 | `collectors/__init__.py:1-19` | **Package public surface incomplete.** Exports six tick collectors only; omits `Collector` protocol, `append_idle_transition`, `IdleWatch`, `WindowInfo`. Callers reach into submodules inconsistently (`daemon` imports IdleWatch/idle_facts by path). | `__all__` lists only Agent/Focus/Idle/Mpris/Process/Tmux. Daemon: `from roxabi_sense.collectors.idle_watch import IdleWatch`. | Expand `__all__` for intentional public API, or add `collectors/runtime.py` re-exports; keep private helpers unexported by design with a one-line package docstring. |
| A-P1-08 | P3 | `collectors/idle.py:19-50` + `idle_facts.py:35-67` | **Idle write split is correct (not a violation)** but easy to misread: `IdleCollector` is logind-only; Wayland authority writes via daemon → `append_idle_transition`. Contract “who may write kind=idle” is distributed across three modules + daemon, not stated in `base.py`. | ADR-002 priority table implemented: `want_logind_idle` demotes logind when Wayland healthy (`daemon_collectors.py:87-94`). Shared writer `idle_facts`. | Add module-level docstring cross-ref in `idle.py` / `idle_facts.py` (already partial); optional `IdleWriter` Protocol in base for the shared append function. No code merge of media into idle (good — anti-pattern avoided). |
| A-P1-09 | P0 | — | **Facts-only / anti-policy: PASS (no finding).** | Grep under `collectors/`: no `surfaces`, `discord` API, `factory.jobs`, NATS, Sentinelle, policy. No rewrite of `~/.claude` / `~/.grok` (read-only via SessionRegistry). Process name `"discord"` is presence fact only (`config.py` DEFAULT_PROCESS_NAMES). Mpris stores track metadata as local facts (surface redaction is ADR-002 surface concern). | Maintain greps in review: `from roxabi_sense.surfaces` inside collectors; NATS/publish inside collectors; composite presence inside IdleCollector. |
| A-P1-10 | P0 | — | **No circular dependencies involving collectors.** | Import graph: collectors → store, util, atspi, paths, collectors.idle_facts. Reverse importers: daemon*, tests only. store/report/surfaces/atspi do not import collectors (except daemon_atspi → FocusAtspiCollector). | Keep store free of collector imports; presence derivation stays in report/store (ADR-002). |

### Metrics

| Metric | Value |
|--------|-------|
| Python modules in partition | 10 (`__init__`, `base`, `agent_sessions`, `focus_atspi`, `idle`, `idle_facts`, `idle_watch`, `mpris`, `process_presence`, `tmux_sessions`) |
| Folder size gate (≤12 files) | **Pass** (10) |
| Approx. LOC (largest) | `idle_watch.py` ~250; `focus_atspi.py` ~244; both ≤300 file-length gate |
| Tick collectors (`name` + `tick`) | 6 (agent_sessions, focus_atspi, idle, mpris, process_presence, tmux) |
| Non-tick modules in package | 3 (`base`, `idle_facts`, `idle_watch`) |
| `Collector` Protocol references outside `base.py` | **0** |
| Imports from `surfaces` / `report` / `cli` | **0** |
| Collectors writing via `store.append` | All six tick collectors + `idle_facts` |
| Collectors calling `store.set_meta` | **1** (`focus_atspi`) |
| Circular import cycles | **0** found |
| Facts-only violations (policy/Discord API/jobs/NATS) | **0** |
| ADR-002 idle dual-write default | **Avoided** (daemon demotes logind when Wayland healthy) |
| HeartbeatCollector / composite idle-in-media | **Absent** (good) |

**Directed dependency sketch (collectors partition):**

```
surfaces / report / cli     (must not be imported — holds)
         ▲
         │ (no edge)
collectors ──► store
     │
     ├──► util (session_registry, agent_link, proc, titles)
     ├──► atspi (focus default probe only)
     └──► paths (idle_watch XDG)
daemon* ──► collectors (assembly + IdleWatch + Focus.apply)
```

### Recommendations

1. **Revive or delete `base.Collector` (P2).** Prefer revive: type assembly/`tick_all` against it; document Focus extensions (`apply`, `tick_focus`, `tick_desktop`) as an optional structural subtype. Delete only if Protocol debt is intentional “structural duck typing forever.”
2. **Clarify runtime vs fact modules (P2).** Either move `IdleWatch` next to daemon/`atspi`-style package, or write an explicit package rule: “platform adapters that feed a kind may live under `collectors/` if the only write path is shared facts helpers.” Align with the AT-SPI split already documented in `__init__.py`.
3. **Single idle-write narrative (P3).** Point all writers at `idle_facts.append_idle_transition` (already true) and list authorities in one docstring table (wayland-idle / logind) matching ADR-002.
4. **Share SessionRegistry (P3).** Default `AgentSessionsCollector` to `default_registry()` so focus enrich and snapshots share mtime cache; keep constructor injection for tests.
5. **Meta ownership (P3).** Document which meta keys collectors may set (`focus_probe_*`) vs daemon-only (`last_tick`, `idle_watch`, `atspi_agent`). Do not derive presence from collector-owned meta.
6. **Keep holding the line (P0 gates for PR review):**
   - No `from roxabi_sense.surfaces` under collectors
   - No NATS/Discord/jobs/policy in collectors
   - No composite `idle = input AND !media` inside IdleCollector
   - No periodic `kind=heartbeat` collector
   - Focus failure must not block agent-session tick (daemon already separates poll lists)

**Overall grade (architecture / P1):** **B+** — correct primary-axis placement and facts-only discipline; contract documentation and package boundary hygiene lag the implementation.
