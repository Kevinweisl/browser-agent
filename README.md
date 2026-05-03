# browser-agent

Generic browser automation agent with self-correction. Built on Playwright + Claude / NIM-hosted LLMs.

> Origin: AI Coding Test 2026 — interview deliverable. Task 2 of three. Scored 10/10 on the held-out evaluation set.

## What this does

Takes a natural-language task description (e.g. *"go to the SEC EDGAR full-text search page and find the 10-K filed by Apple in 2024"*) and drives a real Chromium browser to completion, with two non-trivial behaviors:

- **Self-correction**: when an action fails, the planner re-plans rather than retrying blindly. K=2 LLM-vote validators reject false positives.
- **Self-maintenance**: when a CSS selector goes stale, a 7-tier locator ladder (text → role → label → testid → CSS → XPath → coordinate) finds an alternative; the working selector is cached.

## Architecture

Three-component loop:

```
   user task ──▶ Planner (LLM) ──▶ Actor (Playwright) ──▶ Validator (LLM)
                    ▲                                         │
                    └────────── replan on validation fail ────┘
```

Selectors that work get cached in Postgres (`shared/db.py` + `selector_cache` table) keyed by `(url_template, intent)` so repeat runs are faster and more stable.

## Evaluation

`evals/browser-tasks/` holds 10 tasks across SEC EDGAR, Reuters, and a small finance-pack subset (earnings deck IR pages, Berkshire 10-K cross-validation). Final eval result: **10/10**, see `evals/browser-tasks/last_run.json`.

The path to 10/10 was iterative: 6/10 → 7/10 → 10/10 across 4 fix rounds documented in commit history (look for `fix(d6)` commits). Notable fixes:
- v3 → v4: chained-filter locator ladder + WeakKey semaphore (parallel safety)
- v4 → final: SEC `httpx` fallback + JSON-aware oracles + Berkshire URL hygiene

## Quick start

```bash
pip install -e ".[dev]"
playwright install chromium

# Set env vars in .env: DATABASE_URL (for selector cache), NIM endpoints
export DATABASE_URL="postgresql://..."
export NIM_BASE_URL_NEMOTRON="..."

# Run the eval suite
python -m evals.browser-tasks.runner
```

## Open issues

- Selector cache is currently Postgres-backed (`shared/db.py`). For a lightweight standalone deploy, a file-based or SQLite-backed alternative would be cleaner. Tracked as future work.

## License

MIT — see `LICENSE`.
