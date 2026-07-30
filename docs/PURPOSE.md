# Purpose — roxabi-sense

## One sentence

Record **where workstation attention was**, from **cheap structured signals**, so humans and agents can answer “what was I doing?” without screen capture.

## Problem

| Approach | Failure mode |
|---|---|
| Screenpipe-class (frames + OCR + audio) | Heavy, private, redundant with Claap + agent JSONL |
| Nothing | No focus timeline; only scattered session files |
| Putting capture inside factory | Couples user-session OS access to M₁ hub lifecycle |

## Job to be done

1. **Timeline** — which app/window had focus, when I was idle.
2. **Agent presence** — open Claude/Grok sessions (path, cwd, last activity) already on disk.
3. **Coarse presence** — Slack/Discord *running* or focused (not message bodies from clients).
4. **Surfaces** — CLI for me; MCP for agents; optional NATS facts for factory Sentinelle.

## Explicit non-goals

- Replacing Claap for meetings  
- Replacing `~/.claude` / `~/.grok` as conversation stores  
- Ops policy, Discord alerts, job dispatch (factory Sentinelle)  
- Vision, keylogging, full clipboard, Electron client scrapes  

## Success criteria (product)

- Day slice query in &lt;1 s over a week of data on laptop disk  
- Daemon idle CPU negligible; storage ≪ continuous video/OCR  
- Works with factory **down** (local + MCP)  
- When NATS enabled: only coarse `activity` / `stale` (or equivalent) facts, no raw title firehose by default  

## Name

**sense** = sensing layer (attention), not “sentinel” (ops brain) and not “screen”.  
Repo: `Roxabi/roxabi-sense`.
