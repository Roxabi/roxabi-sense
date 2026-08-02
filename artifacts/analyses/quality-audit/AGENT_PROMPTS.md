# Agent Prompt Templates — roxabi-sense quality audit

## Common preamble

```
You are a read-focused code auditor for roxabi-sense (Python ≥3.13, collectors → SQLite → CLI/MCP/NATS).

Workspace: /home/mickael/projects/roxabi-sense
Primary axis: signal source / collector (ADR-001 axial: true).

Write findings ONLY under artifacts/analyses/quality-audit/{DOMAIN}/{OUTPUT_FILE}.
Do not modify source code.

## Format (mandatory)
### Summary
### Findings
| ID | Severity | File:Line | Finding | Evidence | Recommendation |
|----|----------|-----------|---------|----------|----------------|
Severities: P0 critical | P1 high | P2 medium | P3 low
### Metrics
### Recommendations
```

## Domain focus

### Architecture
- Layer violations: collectors must not import surfaces; surfaces must not reimplement store queries
- Coupling between collectors and report/daemon
- Circular imports
- God modules / unclear boundaries

### Axial Drift
- Grep: `def (status|day|active_now|what_was_i_doing)` under surfaces/
- Collectors importing from surfaces
- Query logic duplicated in CLI vs report vs future MCP
- Cross-cutting (retry, path resolve, timestamp parse) duplicated across collectors

### Security
- Path traversal on user/home paths
- Shell/subprocess without sanitization
- Credential/token handling
- SQLite injection (string-built SQL)
- Untrusted JSON from agent session dirs
- File permissions on DB/config

### Code Smells
- Functions >50 LOC, files >400 LOC
- Duplicated collector scaffolding
- Dead code / commented blocks
- Magic constants without names

### Type Safety
- `Any`, bare `dict`/`list` without params
- `# type: ignore`
- Missing return annotations on public APIs
- Optional abuse

### Async Patterns
- Blocking calls if any async appears
- Resource leaks (open files, DB connections, subprocesses)
- Daemon loop sleep/race issues
- Missing timeouts on I/O

### Error Handling
- Bare `except:` / `except Exception: pass`
- Swallowed collector errors
- Missing context in raised errors
- Fail-open vs fail-closed for sensors

### Test Quality
- Untested modules (map src → tests)
- Over-mocking / testing implementation
- Flaky time/path assumptions
- Missing edge cases for empty DB / missing home dirs

### Tech Debt
- TODO/FIXME/HACK/XXX
- Stub surfaces (MCP/NATS)
- Deprecated patterns
- Magic numbers, hard-coded paths
