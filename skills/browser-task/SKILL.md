---
name: browser-task
description: |
  Drive a real Chromium browser (via Playwright) to complete a multi-step web
  task described in natural language — login, fill forms, navigate, click,
  extract page content, take screenshots. Use this when the user asks to
  "log into a website and download X", "scrape this dashboard that requires
  authentication", "fill out the form on this page", "navigate the site and
  click through the wizard", "get the content from this JavaScript-rendered
  page", "verify the news on Reuters about Apple's 10-K disclosures", or any
  task that needs an actual browser (cookies, JS execution, DOM interaction).
  Do NOT use for: simple HTTP GETs of static pages (use a regular fetch),
  parsing structured documents like SEC filings (use sec-extract-10k), or
  build/release pipelines (use build-and-release). Long-running (minutes),
  stateful (cookies + selector cache), self-correcting (6-tier locator ladder + Healenium-style heal).
allowed-tools: "Bash(playwright *)"
worker_target: browser
---

# browser-task

LLM-driven browser automation agent. Planner-Actor-Validator architecture
with Postgres-persisted selector cache for cross-session self-maintenance.

## Pattern: LLM-driven agent skill with self-correction

```
NL Task → Planner (LLM, K=1)
         → Actor (steps with 6-tier locator ladder)
         → Validator (LLM, K=2 ensemble — discrete PASS/REPLAN/ABORT)
         → Selector cache (Postgres-backed; cross-session)
```

Three subsystems:

| Subsystem | Role | Latency budget |
|---|---|---|
| **Planner** | Decompose NL task into ordered steps. Free-form output. | One-shot, ~5s |
| **Actor** | Execute each step. For each click/type/extract, walks 6-tier locator ladder until one resolves. | Per-step, ~1-3s |
| **Validator** | After each step, judge "did this advance the goal?" Discrete output (PASS/REPLAN/ABORT) → K=2 ensemble vote on this. | Per-step, ~2-5s |

## 6-tier locator ladder (Playwright-recommended)

For each user-visible action ("click the submit button"):
1. **Cached selector** from `selector_cache` table (if dom_hash matches OR aria-fingerprint heals)
2. `page.get_by_role(...)` — Playwright's official first choice
3. `page.get_by_label(...)` — form-element preferred
4. `page.get_by_test_id(...)` — automation-only
5. `page.get_by_text(...)` — visible text anchor
6. CSS without class chains (id / data-* only)
7. **Vision fallback (stub)** — Computer Use beta integration point; not enabled by default

Successful resolutions write back to `selector_cache` for next session.

## Anti-pattern: silent failure

Page navigation can succeed structurally while semantically failing (URL
unchanged, hidden modal, captcha). The validator looks for: URL change,
DOM-hash diff, viewport screenshot diff. If all three are no-ops on what was
supposed to be a state-changing action, the Validator votes REPLAN regardless
of "click succeeded" feedback from the locator.

## Input
```json
{
  "task": "Log into example.com with username X, navigate to the deposit page, take a screenshot",
  "secrets": {"username": "...", "password": "..."},   // injected via env, never logged
  "max_steps": 20,
  "max_minutes": 5
}
```

## Output
```json
{
  "ok": true,
  "steps_executed": 12,
  "trajectory": [{"step": "navigate", "selector_strategy": "cached", "result": "..."}],
  "screenshots": ["base64..."],
  "extracted_content": "...",
  "selector_cache_hits": 9,
  "selector_cache_writes": 3,
  "duration_ms": 47000
}
```

## Status

Production-ready. v7 eval = 10/10 across 10 generic + finance tasks; 3-run determinism = 30/30. See `evals/browser-tasks/last_run.json`.
