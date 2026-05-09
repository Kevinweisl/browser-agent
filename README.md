# browser-agent

Generic browser automation agent with self-correction. Built on Playwright + Claude / NIM-hosted LLMs.

> Origin: AI Coding Test 2026 — interview deliverable. Task 2 of three. Scored 10/10 on the held-out evaluation set.

## What this does

Takes a natural-language task description (e.g. *"go to the SEC EDGAR full-text search page and find the 10-K filed by Apple in 2024"*) and drives a real Chromium browser to completion, with two non-trivial behaviors:

- **Self-correction**: when an action fails, the planner re-plans rather than retrying blindly. K=2 LLM-vote validators reject false positives.
- **Self-maintenance**: when a CSS selector goes stale, a 6-tier locator ladder (cached → role → label → testid → text → CSS-no-class → vision-stub) finds an alternative; the working selector is cached, and a Healenium-style aria-fingerprint heals drifted entries.

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

## Web demo

A FastAPI + vanilla-JS UI for submitting NL tasks from a browser. Useful for screencast / live demos; the eval pipeline still owns correctness.

Start it locally:

```bash
pip install -e ".[dev]"
playwright install chromium
# .env populated as for the eval suite (NIM_*, DATABASE_URL, SEC_USER_AGENT)
uvicorn src.server.main:app --host 0.0.0.0 --port 8000
# or:
python -m src.server.main
```

Then visit `http://localhost:8000`. The UI exposes:

- a task input (NL description, optional starting URL, max_steps / max_seconds caps),
- the planner's step decomposition,
- the full per-step trajectory (locator tier, cache_hit / healed flags, validator decision, silent-failure signals, replan boundaries),
- final extracted content + cache / heal counters.

Limits (intentional, demo-grade):

- single-concurrency — one task at a time, gated by an in-process semaphore,
- in-memory task registry — restarting the server forgets all task IDs,
- 5-minute hard wall-clock cap per task,
- no auth — the deployed instance is treated as a throwaway.

## Deployment (Zeabur)

This repo deploys cleanly to [Zeabur](https://zeabur.com) using the
official Playwright Python image as the base — Chromium binaries are
already in place, no `playwright install` step needed at deploy time.

### Steps

1. Create a new Zeabur project, import this GitHub repo
2. Add a Postgres service (Zeabur auto-injects `DATABASE_URL` into the
   web service)
3. In the web service settings, set the following environment variables
   (copy from `.env.example`):
   - `NIM_NEMOTRON_API_KEY`, `NIM_MISTRAL_API_KEY` (required for K=2 vote)
   - `PLANNER_MODELS=nemotron`
   - `VALIDATOR_MODELS=nemotron,mistral`
   - `SEC_USER_AGENT="your-name your-email@example.com"`
4. Deploy. Zeabur uses the `Dockerfile` at the repo root automatically
   (also pinned via `zbpack.json`).
5. Once running, Zeabur exposes the service at `https://<your-project>.zeabur.app`

Health check path (`/healthz`) and region (`sin1` Singapore for Aliyun
Bangkok proximity) are configured in the Zeabur dashboard — they are not
keys in `zbpack.json` schema.

### Resource expectations

- ~1.5 GB image (Playwright base + deps)
- ~512 MB RAM idle, peaks 1.2-2 GB during a task (headless Chromium)
- Single-task concurrency by design — no Playwright thrashing
- Per-task budget: max_seconds default 180s, hard cap 300s

### Local docker test

```bash
docker build -t browser-agent .
docker run --rm -p 8000:8000 \
    -e DATABASE_URL=postgresql://... \
    -e NIM_NEMOTRON_API_KEY=... \
    -e NIM_MISTRAL_API_KEY=... \
    -e SEC_USER_AGENT="..." \
    browser-agent
```

Visit `http://localhost:8000`.

## Open issues

- Selector cache is currently Postgres-backed (`shared/db.py`). For a lightweight standalone deploy, a file-based or SQLite-backed alternative would be cleaner. Tracked as future work.

## License

MIT — see `LICENSE`.
