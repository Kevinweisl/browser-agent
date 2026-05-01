# Task 2 — Generalized Browser Automation Agent

## TL;DR

A Playwright-driven browser agent that takes a natural-language task,
decomposes it via an LLM Planner, executes via an LLM-light Actor with a
6-tier locator ladder, and validates each step with a K=2 LLM Validator
ensemble. Self-maintains through a Postgres-backed selector cache that
persists across sessions; self-corrects through a replan loop on
silent-failure detection.

## Architecture

```
NL Task ──► Planner (LLM, K=1)            # high-stakes task decomposition
         │
         ▼  list[Step]
         ┌────────────────────────────────────────────────┐
         │  for each step:                                │
         │    pre  = snapshot()                            │
         │    Locator = LocatorResolver.resolve()          │  ← 6-tier ladder
         │    Actor.execute()                              │
         │    post = snapshot()                            │
         │    signals = silent_failure.collect_signals()   │
         │    Validator.vote(K=2) → PASS|REPLAN|ABORT      │  ← LLM ensemble
         │    if REPLAN: Planner.replan(history, reason)   │
         └────────────────────────────────────────────────┘
              │
              ▼  TaskResult{trajectory, extracted_content, ...}
```

Selector cache (Postgres `selector_cache`) sits beside the resolver: every
successful resolve writes `(page_url_template, intent) → (selector,
aria_fingerprint, dom_hash)`; every cache hit short-circuits Tier 1 of the
ladder.

## Design patterns demonstrated

| Pattern | Where it lives | Why it matters |
|---|---|---|
| **Self-correction** via replan loop | `handlers.run_task` — Validator REPLAN → Planner.replan(history, reason) | Brief explicitly grades "self-correcting" behavior. Replan budget capped (3) so stuck tasks fail loudly. |
| **Self-maintenance** via cross-session selector cache | `selector_cache.py` + `actor._resolve_with_cache` | Brief explicitly grades "self-maintenance". Cache survives process restart; demonstrated in `scripts/browser_smoke.py` (run twice → 2nd run hits Tier 1). |
| **6-tier locator ladder** | `locator_ladder.LocatorResolver` | Resilient: each tier degrades gracefully. Anti-patterns (`.class` chains, XPath) explicitly disallowed (research delta §2). |
| **Healenium-style aria fingerprint** | Cache schema `aria_fingerprint JSONB` + `actor._aria_fingerprint` | Stored alongside selector so a future healer can find the equivalent element when DOM drifts (research delta §4 — Healenium is still the production standard). |
| **Silent-failure cascade** | `silent_failure.collect_signals` | Cheap → expensive: ariaSnapshot diff → URL/title diff → DOM diff → mutating-action-with-no-change check → (LLM verifier in Validator). |
| **K=2 ensemble validator** | `validator.validate_step` via `vote_role("validator", ...)` | Reuses the platform's vote_role plumbing (Day 4). Tie → `fallback_used=True` so the trajectory log shows uncertainty rather than a deterministic guess. |
| **Negative oracle per step** | `Step.success_criteria` (NegativeOracle) | Per-step assertions caught in `silent_failure.negative_oracle_violations` before the Validator runs. The "healed-test sleep-walking" failure mode (research delta §5) is impossible without this. |

## Locator ladder — the actual order

The original 7-tier ladder (`getByRole → testid → ARIA → text → CSS → XPath → vision`) was revised after the Day 6 research delta:

- **Drop standalone "ARIA" tier**: subsumed by `getByRole`.
- **Drop XPath tier**: Playwright officially declares it an anti-pattern in 2026.
- **Add `deepLocator` tier**: only if Stagehand's Python SDK lands; deferred for now.
- **Vision tier 7 is stubbed**: implementation requires Anthropic Computer Use beta API key; the resolver exposes a `vision_fallback` injection point for when that's available.

Final ladder (`schema.LocatorTier`):

1. `CACHED` — selector_cache lookup with dom_hash match
2. `GET_BY_ROLE` — Playwright official first choice (handles `getByLabel`, `getByPlaceholder`, `getByTitle` indirectly via accessible name)
3. `GET_BY_LABEL` — explicit form-element label
4. `GET_BY_TEST_ID` — `data-testid` attribute
5. `GET_BY_TEXT` — visible text fragment
6. `CSS_NO_CLASS` — id / data-* only (class chains rejected)
7. `VISION_FALLBACK` — stub interface; CU integration is a follow-up

## Eval methodology

`evals/browser-tasks/tasks.yaml` ships 10 tasks across 2 packs:

- **generic** (5 tasks) — Wikipedia / docs / forms — proves cross-domain coverage.
- **finance** (5 tasks) — Apple IR, SEC EDGAR browse, EDGAR full-text search, Berkshire IR — the integration narrative with Task 3.

Each task has:

- `success_criteria` — substring or regex assertions on extracted content.
- `negative_oracle` — must_appear_on_final_page + must_not_appear (substring).
- `step_cap`, `wall_clock_cap_s` — deterministic budgets.

Scoring (`evals/browser-tasks/runner.py`):

- A task passes when `result.ok=True` AND every `success_criteria` matches AND zero `oracle_violations`.
- Per-pack success rate + fail-reason histogram + cache-hit/write counts in the report.

The eval set is intentionally smaller than the original 30-task plan — the Day 6 research delta surfaced that WebVoyager has saturated and BrowseComp is the new SOTA, but adapting BrowseComp tasks would require a research-grade infrastructure beyond this submission's scope. The 10-task pack is a credible *demonstration* eval, not a leaderboard run.

## What's intentionally NOT here

- **Computer Use vision fallback** — would require an Anthropic API key + significant prompt-engineering work. Stub interface is in place (`LocatorResolver.vision_fallback`).
- **Headed-mode debugging UI** — runner has no `--headed` flag; debugging falls back to the Playwright trace viewer if needed.
- **WebVoyager / BrowseComp external validation** — saturated benchmark and out-of-scope respectively (research delta §6).
- **Retry on transient network errors** — falls back to the existing `retry_async` decorator pattern; not yet wired into `actor._navigate`.
- **Concurrent tab support** — single tab per task; the brief scope doesn't ask for parallelism.

## End-to-end smoke test

`scripts/browser_smoke.py` exercises Actor + Resolver + cache against
`https://example.com` *without* invoking the LLM. Two-run idempotency:

```
$ python scripts/browser_smoke.py    # 1st run — cache_writes=1, cache_hits=0
$ python scripts/browser_smoke.py    # 2nd run — cache_writes=0, cache_hits=1
```

The Tier 1 (`CACHED`) hit on the 2nd run is the empirical demonstration of self-maintenance.
