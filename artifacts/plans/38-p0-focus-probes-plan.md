---
title: "Plan: P0 focus probes"
issue: 38
issues: [38, 39, 40, 41, 42]
spec: artifacts/specs/38-p0-focus-probes-spec.md
complexity: 7/10
tier: F-full
generated: 2026-08-01
status: approved
---

## Summary

Implement Shape 1: package `collectors/focus/` with FocusProbe protocol, session select, AT-SPI/X11/noop probes, daemon demote/recover, and doctor capabilities. One PR stack closes #38–#42.

## Architecture

**Data flow:** [existing](../visuals/38-p0-focus-probes-data-flow.html)  
**File map:** [below waves]

### Target layout

```
src/roxabi_sense/collectors/focus/
  __init__.py
  protocol.py
  session.py
  select.py
  collector.py
  probes/__init__.py
  probes/atspi.py
  probes/x11.py
  probes/noop.py
src/roxabi_sense/collectors/focus_atspi.py  # re-export alias
src/roxabi_sense/daemon_focus.py            # select + demote helpers
src/roxabi_sense/daemon.py                  # wire demote
src/roxabi_sense/daemon_atspi.py            # last_focus_path + backend source
src/roxabi_sense/doctor.py                  # capabilities
```

## Bootstrap Context

- Ref: `collectors/focus_atspi.py` (enrich pattern), `daemon.py` idle demote, `doctor.py` Check model
- Gate: collectors/ root ≤12 files → subpackage required
- Worktree: `.claude/worktrees/38-p0-focus-probes` · branch `feat/38-p0-focus-probes`

## Agents

| Agent | Tasks | Files |
|-------|-------|-------|
| backend-dev-A | T1–T4 protocol+collector | focus/* |
| backend-dev-B | T5–T7 select+x11 | session, select, x11 |
| backend-dev-C | T8–T9 daemon | daemon_focus, daemon, daemon_atspi |
| backend-dev-D | T10 doctor | doctor.py |
| tester-A | T11–T14 tests | tests/ |
| doc-writer-A | T15 docs | README, ARCHITECTURE |

## Wave Structure

5 waves, sequential slices (deps). Max 2 parallel (BE+tester within slice).

| Wave | Trigger | Agents | Tasks |
|------|---------|--------|-------|
| 1 | start | backend-dev-A | T1→T4 S1 protocol |
| 2 | W1 | backend-dev-B + tester-A | T5–T7 select/x11 · T11 tests S1 |
| 3 | W2 | backend-dev-C + tester-A | T8–T9 daemon · T12 tests S2–S3 |
| 4 | W3 | backend-dev-D + tester-A | T10 doctor · T13 tests S4–S5 |
| 5 | W4 | doc-writer-A + tester-A | T14 full pytest · T15 docs · RED-GATE |

### Budget — per task

| Task | Class | Est. ops | Split? |
|------|-------|----------|--------|
| T1 protocol | bounded | 3 | — |
| T2 noop+atspi probes | judgmental | 6 | — |
| T3 collector | judgmental | 6 | — |
| T4 re-export + import sweep | bounded | 3 | — |
| T5 session | bounded | 3 | — |
| T6 select | judgmental | 5 | — |
| T7 x11 | judgmental | 6 | — |
| T8 daemon_focus | judgmental | 8 | — |
| T9 wire daemon | judgmental | 6 | — |
| T10 doctor | bounded | 4 | — |
| T11–T13 tests | judgmental | 12 | — |
| T14 pytest gate | bounded | 3 | — |
| T15 docs | bounded | 3 | — |

**Total estimated ops: ~70** (orchestrator may implement serially in one session)

## Micro-Tasks

### S1 — Protocol + collector (#38)

| ID | Description | File | Agent | Deps | Verify |
|----|-------------|------|-------|------|--------|
| T1 | Define FocusWindow, FocusProbe Protocol, SOURCE literals | `collectors/focus/protocol.py` | BE-A | — | import FocusProbe |
| T2 | NoopFocusProbe + AtspiFocusProbe (wrap probe_once / agent path) | `probes/noop.py`, `probes/atspi.py` | BE-A | T1 | unit |
| T3 | Move/adapt FocusAtspiCollector → FocusCollector; write `source` from probe | `collector.py` | BE-A | T2 | test_focus |
| T4 | Re-export FocusAtspiCollector alias; update __init__ | `focus_atspi.py`, `collectors/__init__.py` | BE-A | T3 | import |

### S2 — Select (#39)

| ID | Description | File | Agent | Deps | Verify |
|----|-------------|------|-------|------|--------|
| T5 | Detect session_type + desktop_family from env | `session.py` | BE-B | T1 | fixtures |
| T6 | `candidate_sources(session)` + `select_probe()` first ok | `select.py` | BE-B | T5,T2 | fixtures |

### S3 — X11 (#40)

| ID | Description | File | Agent | Deps | Verify |
|----|-------------|------|-------|------|--------|
| T7 | X11FocusProbe via xprop (injectable runner) | `probes/x11.py` | BE-B | T1 | mock subproc |

### S4 — Runtime fallback (#41)

| ID | Description | File | Agent | Deps | Verify |
|----|-------------|------|-------|------|--------|
| T8 | FocusRuntime: select, set_meta, demote, recover | `daemon_focus.py` | BE-C | T6,T7 | unit |
| T9 | Wire daemon.py: use FocusRuntime; rename last_focus_path | `daemon.py`, `daemon_atspi.py` | BE-C | T8 | test_daemon |

### S5 — Doctor (#42)

| ID | Description | File | Agent | Deps | Verify |
|----|-------------|------|-------|------|--------|
| T10 | capabilities block focus+idle on DoctorReport | `doctor.py` | BE-D | T8 | test_doctor |

### Tests + docs

| ID | Description | File | Agent | Deps | Verify |
|----|-------------|------|-------|------|--------|
| T11 | Fake probe + source field tests | `tests/test_focus_probe.py` | tester-A | T4 | pytest |
| T12 | Select env matrix + x11 mock | `tests/test_focus_select.py`, `test_focus_x11.py` | tester-A | T7 | pytest |
| T13 | Demote + doctor capabilities | `tests/test_focus_runtime.py`, update doctor tests | tester-A | T10 | pytest |
| T14 | Full quality gates | — | tester-A | T13 | pytest ruff pyright |
| T15 | README matrix + x11-utils; ARCHITECTURE touch | README, ARCHITECTURE | doc-A | T14 | — |

### RED-GATE

After T14: all Success Criteria checkboxes in spec satisfiable by tests or docs.

## Task Seeding Blueprint

### Wave 1
| Task | Agent instance | blockedBy | Subject |
|------|---------------|-----------|---------|
| T1 | backend-dev-A | — | protocol |
| T2 | backend-dev-A | T1 | probes |
| T3 | backend-dev-A | T2 | collector |
| T4 | backend-dev-A | T3 | compat |

### Wave 2
| Task | Agent instance | blockedBy | Subject |
|------|---------------|-----------|---------|
| T5 | backend-dev-B | T4 | session |
| T6 | backend-dev-B | T5 | select |
| T7 | backend-dev-B | T4 | x11 |
| T11 | tester-A | T4 | tests |

### Wave 3
| Task | Agent instance | blockedBy | Subject |
|------|---------------|-----------|---------|
| T8 | backend-dev-C | T6,T7 | runtime |
| T9 | backend-dev-C | T8 | daemon |
| T12 | tester-A | T7 | tests |

### Wave 4
| Task | Agent instance | blockedBy | Subject |
|------|---------------|-----------|---------|
| T10 | backend-dev-D | T8 | doctor |
| T13 | tester-A | T9,T10 | tests |

### Wave 5
| Task | Agent instance | blockedBy | Subject |
|------|---------------|-----------|---------|
| T14 | tester-A | T13 | gates |
| T15 | doc-writer-A | T14 | docs |

## Task IDs

<!-- Filled after plan approval if TaskCreate used; implement may run orchestrator-serial -->
- T1–T15: sequential in-session implement on worktree

## Consistency Report

- Spec slices S1–S5 ↔ tasks covered
- Success criteria ↔ T11–T15 verification
- No χ open
