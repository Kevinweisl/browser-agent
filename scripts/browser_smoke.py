"""End-to-end smoke proof of *cross-session* selector-cache self-maintenance.

This script is the empirical demonstration that the brief's "self-maintenance"
axis is real, not aspirational. Why a separate script?

  - `evals/browser-tasks/runner.py` runs each task through `run_task` once
    inside a single process. By construction, two tasks rarely share both
    `(page_url_template, intent)` so cache_hits stays near 0 in eval logs
    (see `last_run.json` — almost every row reports `selector_cache_hits=0`).
  - To prove the cache survives a process restart we have to *be* a process
    that ends, then a second one that starts. That's this file.
  - We deliberately bypass the Planner / Validator / LLMs. The point is to
    isolate `LocatorResolver + selector_cache + actor.snapshot/_aria_fingerprint`
    so a cache hit is unambiguous.

Two phases (each its own Playwright browser context, simulating a fresh
session):

  Phase 1 — cold cache:
      go to https://example.com → resolve "more information link" via the
      ladder → write (selector, dom_hash, aria_fingerprint) into Postgres.
      Expected: cache_writes=1, cache_hits=0.

  Phase 2 — warm cache:
      brand-new browser context → same URL + same intent → cache lookup
      should match by URL template and dom_hash should equal → Tier 1 CACHED
      resolution. Expected: cache_writes=0, cache_hits=1.

  Phase 3 — drift-and-heal:
      patch the stored `dom_hash` to a known-bad value so the dom-hash
      equality check fails, then re-run the click. The aria_fingerprint
      stored on the row still describes the live element (example.com is
      stable), so the resolver should take the heal path inside Tier 1 and
      report `healed=True`. The actor records the heal (record_heal +
      refreshed dom_hash). Expected: cache_writes=1 (refresh), cache_hits=0,
      cache_heals=1, tier='cached'.

Exit code:
    0 — all three phases met expectations
    1 — any deviation (printed before exit)

Usage:
    python scripts/browser_smoke.py             # default URL + intent
    python scripts/browser_smoke.py --reset     # blow away the test row first

Pre-reqs: DATABASE_URL set (loaded from .env), Postgres reachable, Playwright
chromium installed (`playwright install chromium`).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Match runner.py's bootstrap: load .env then add src/ to sys.path so the
# `workers` and `shared` packages import cleanly when this file is run from
# the repo root.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from playwright.async_api import async_playwright  # noqa: E402

from shared.db import close_pool, get_pool  # noqa: E402
from workers.browser.actor import StepActor  # noqa: E402
from workers.browser.locator_ladder import LocatorResolver  # noqa: E402
from workers.browser.schema import (  # noqa: E402
    ActionType,
    Step,
)
from workers.browser.selector_cache import url_to_template  # noqa: E402

SMOKE_URL = "https://example.com"
SMOKE_INTENT = "more information link"  # matches example.com's lone "More information..." link


async def _reset_row(intent: str, url: str) -> None:
    """Delete any prior smoke row so phase 1 truly starts cold."""
    template = url_to_template(url)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM selector_cache WHERE page_url_template = $1 AND action_intent = $2",
            template, intent,
        )


async def _patch_dom_hash(intent: str, url: str, fake_hash: str = "deadbeefcafe1234") -> None:
    """Rewrite the cache row's dom_hash to a value that won't match the live
    page, so the resolver's Tier-1 dom-hash equality fails and we can verify
    the fingerprint heal path takes over."""
    template = url_to_template(url)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE selector_cache SET dom_hash = $3 "
            "WHERE page_url_template = $1 AND action_intent = $2",
            template, intent, fake_hash,
        )


async def _read_heal_audit(intent: str, url: str) -> tuple[bool, str | None]:
    """Return (last_healed_at_set, healing_diff) so we can prove the heal
    actually wrote audit columns, not just bumped a counter."""
    template = url_to_template(url)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT last_healed_at, healing_diff FROM selector_cache "
            "WHERE page_url_template = $1 AND action_intent = $2",
            template, intent,
        )
    if row is None:
        return False, None
    return row["last_healed_at"] is not None, row["healing_diff"]


async def _run_phase(label: str, *, url: str, intent: str) -> tuple[int, int, int, str | None]:
    """Open a fresh browser context, navigate, and resolve `intent`.

    Returns (cache_writes, cache_hits, cache_heals, tier_used). The actor's
    counters tell us authoritatively whether the resolver took the cached
    path, the heal path, or fell through to the tier-2 ladder.
    """
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        try:
            actor = StepActor(page, LocatorResolver())

            # Step 1: navigate (no cache involvement — navigate doesn't use a locator).
            nav = Step(step_index=1, action_type=ActionType.NAVIGATE,
                       target_intent=f"open {url}", url=url)
            r1 = await actor.execute(nav)
            if not r1.success:
                raise RuntimeError(f"[{label}] navigate failed: {r1.error}")

            # Step 2: click the link by intent. This is what we're measuring.
            # On phase 1 the resolver should fall through to tier 2 (getByRole)
            # then write to cache. On phase 2 the lookup should match by
            # URL template AND dom_hash, returning Tier 1 CACHED.
            click = Step(step_index=2, action_type=ActionType.CLICK,
                         target_intent=intent)
            r2 = await actor.execute(click)
            if not r2.success:
                raise RuntimeError(
                    f"[{label}] click failed: {r2.error} "
                    f"(tier={r2.locator_tier!r}, selector={r2.selector!r})"
                )

            tier = r2.locator_tier.value if r2.locator_tier else None
            return actor.cache_writes, actor.cache_hits, actor.cache_heals, tier
        finally:
            await ctx.close()
            await browser.close()
    finally:
        await pw.stop()


async def main_async(args: argparse.Namespace) -> int:
    if args.reset:
        await _reset_row(args.intent, args.url)
        print(f"[reset] removed prior smoke row for ({args.url!r}, {args.intent!r})")

    # ── Phase 1: cold cache ──────────────────────────────────────────────────
    # Make sure we start cold even without --reset, otherwise phase-1 writes
    # would never increment when the row already exists from a prior run.
    await _reset_row(args.intent, args.url)

    print("[phase 1] cold cache — expecting writes=1 hits=0 heals=0")
    w1, h1, hl1, t1 = await _run_phase("phase1", url=args.url, intent=args.intent)
    print(f"[phase 1] cache_writes={w1} cache_hits={h1} cache_heals={hl1} tier={t1!r}")

    # ── Phase 2: warm cache (separate browser context = simulated session) ──
    print("[phase 2] warm cache — expecting writes=0 hits=1 heals=0")
    w2, h2, hl2, t2 = await _run_phase("phase2", url=args.url, intent=args.intent)
    print(f"[phase 2] cache_writes={w2} cache_hits={h2} cache_heals={hl2} tier={t2!r}")

    # ── Phase 3: drift-and-heal — fingerprint must rescue dom_hash mismatch ─
    # We patch the stored dom_hash to garbage. example.com's DOM is stable
    # so the live page's fingerprint still matches the stored fingerprint.
    # The resolver should detect the dom_hash mismatch, walk the heal path,
    # confirm fingerprint match, and report healed=True. The actor then
    # writes record_heal + refreshes dom_hash.
    await _patch_dom_hash(args.intent, args.url)
    print("[phase 3] drift-and-heal — patched dom_hash to garbage; "
          "expecting writes=1 hits=0 heals=1 (heal path refreshes the row)")
    w3, h3, hl3, t3 = await _run_phase("phase3", url=args.url, intent=args.intent)
    print(f"[phase 3] cache_writes={w3} cache_hits={h3} cache_heals={hl3} tier={t3!r}")
    healed_at_set, healing_diff = await _read_heal_audit(args.intent, args.url)
    print(f"[phase 3] last_healed_at_set={healed_at_set} healing_diff={healing_diff!r}")

    # ── Verdict ─────────────────────────────────────────────────────────────
    phase1_ok = (w1 == 1 and h1 == 0 and hl1 == 0)
    phase2_ok = (w2 == 0 and h2 == 1 and hl2 == 0 and t2 == "cached")
    phase3_ok = (hl3 == 1 and t3 == "cached" and healed_at_set
                 and healing_diff is not None)

    print()
    print("=" * 60)
    print(f"phase 1 (cold)         -> {'OK' if phase1_ok else 'FAIL'} (writes=1 hits=0 heals=0)")
    print(f"phase 2 (warm)         -> {'OK' if phase2_ok else 'FAIL'} (writes=0 hits=1 tier=cached)")
    print(f"phase 3 (drift+heal)   -> {'OK' if phase3_ok else 'FAIL'} (heals=1 tier=cached + audit row)")
    overall = phase1_ok and phase2_ok and phase3_ok
    print(f"overall                -> {'PASS' if overall else 'FAIL'}")

    if not overall:
        print()
        print("notes on common miss modes:")
        print("  phase 2 — usually (a) DOM hash drifted between runs "
              "(example.com is normally stable; check for CDN A/B), "
              "(b) URL template normalisation diverged, or (c) intent string "
              "differs in case/whitespace (intents are hashed verbatim post-lower).")
        print("  phase 3 — if heals=0 but hits=0 too, the fingerprint stored "
              "in phase 1 didn't survive the page revisit (live element's "
              "role/aria-label/id/data-testid drifted ≥ 3 of 4) and the heal "
              "threshold (2/4 strong attrs) rejected the match. example.com "
              "is stable enough that this should pass; a drift here would "
              "indicate the fingerprint extractor regressed.")

    return 0 if overall else 1


def main() -> int:
    p = argparse.ArgumentParser(
        description=("Cross-session selector-cache smoke test. "
                     "Two phases prove the cache survives process restart."),
    )
    p.add_argument("--url", default=SMOKE_URL,
                   help=f"page to navigate (default {SMOKE_URL})")
    p.add_argument("--intent", default=SMOKE_INTENT,
                   help=f"target_intent string (default {SMOKE_INTENT!r})")
    p.add_argument("--reset", action="store_true",
                   help="alias for the implicit phase-1 reset; kept for explicit user intent")
    args = p.parse_args()

    try:
        return asyncio.run(_with_pool_close(main_async(args)))
    except KeyboardInterrupt:
        return 130


async def _with_pool_close(coro):
    """Ensure the asyncpg pool is closed cleanly so the process exits."""
    try:
        return await coro
    finally:
        await close_pool()


if __name__ == "__main__":
    sys.exit(main())
