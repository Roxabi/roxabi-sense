# Quality Audit Strategy — roxabi-sense

**Playbook:** `roxabi-plugins/playbooks/multi-agent-audit-playbook.md` v1.1  
**Target:** roxabi-sense (local attention sensor)  
**Scale:** ~55 `.py` files → **18 agents**, wave size 4, est. ~12–20 min  
**Started:** 2026-07-31  
**Primary axis (ADR-001):** Signal source / collector

## Domains

| Domain | Focus |
|--------|-------|
| Axial Drift | Wrong-axis duplication (N×M), surfaces reimplementing queries, collectors → surfaces |
| Architecture | Layering collectors/store/surfaces/report/daemon, coupling, circular deps |
| Security | Secrets, path traversal, command injection, untrusted paths (`~/.claude`/`~/.grok`), SQLite |
| Code Smells | God modules, long functions, DRY across collectors |
| Type Safety | `Any`, missing hints, `type: ignore`, pyright debt |
| Async Patterns | Blocking I/O, races, resource leaks (sense is mostly sync — flag false async) |
| Error Handling | Bare excepts, swallowed errors, silent collector failures |
| Test Quality | Coverage gaps, flaky patterns, mocking |
| Tech Debt | TODOs, FIXMEs, stubs (MCP/NATS), magic numbers, deprecated APIs |

## Partitioning (adapted to sense)

| ID | Patterns | Description |
|----|----------|-------------|
| P1 | `src/roxabi_sense/collectors/**/*.py` | Collectors (primary axis) |
| P2 | `src/roxabi_sense/store/**`, `cli.py`, `config.py`, `paths.py`, `daemon*.py`, `install_service.py`, `__init__.py` | Store + package root / daemons |
| P3 | `src/roxabi_sense/report/**`, `surfaces/**` | Report + surfaces (secondary adapters) |
| P4 | `src/roxabi_sense/atspi/**`, `util/**`, `tools/**` | AT-SPI, utils, tools |
| T1 | `tests/**/*.py` | All tests |

## Execution waves (scaled)

```
Wave 1:  Axial Drift (structural greps + axial-adr-review + ccc probes)
Wave 2:  Architecture P1–P4
Wave 3:  Security (P1+P2) + (P3+P4)
Wave 4:  Code Smells (src) + (tests)
Wave 5:  Type Safety + Async + Error Handling (full src each)
Wave 6:  Test Quality T1 + Tech Debt src
Wave 7:  Cocoindex cross-domain validation
Wave 8:  Synthesis → AUDIT-SUMMARY.md
```

## Hard rules (project)

- Facts only in collectors — no Discord, no job dispatch, no Sentinelle policy
- No OCR / screenshots / keylogging as product direction
- Read `~/.claude` / `~/.grok` read-only; never rewrite histories
- NATS payloads stay coarse (`activity` / `stale`)
- Surfaces must not own collector logic; collectors must not import surfaces

## Output root

`artifacts/analyses/quality-audit/`
