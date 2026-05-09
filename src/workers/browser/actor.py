"""Step Actor: executes one Step against a live Playwright Page.

Wires together:
  - LocatorResolver (7-tier ladder)
  - selector_cache (Postgres get/put)
  - PageSnapshot pre/post capture (URL, title, DOM hash, ariaSnapshot,
    text excerpt)

The Actor does NOT decide whether a step succeeded — that's the Validator.
The Actor's job is "make the action happen, faithfully record what changed".
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from . import selector_cache as cache
from .locator_ladder import LocatorResolver, aria_fingerprint_of
from .schema import (
    ActionType,
    PageSnapshot,
    Step,
    StepResult,
)
from .selector_cache import (
    CacheRecord,
    dom_hash_string,
    url_to_template,
)

# Hosts that REQUIRE a contact-email User-Agent per their fair-use policy
# (sec.gov: "no more than 10 req/s"; rejects default Chrome UA with HTTP 403
# "Your Request Originates from an Undeclared Automated Tool"). When we
# navigate to one of these hosts, we override the UA via extra HTTP headers.
_HOSTS_REQUIRING_CONTACT_UA = {
    "www.sec.gov", "data.sec.gov", "efts.sec.gov", "sec.gov",
}

if TYPE_CHECKING:
    from playwright.async_api import Page

log = logging.getLogger(__name__)


# ── PageSnapshot capture ────────────────────────────────────────────────────

async def snapshot(page: Page) -> PageSnapshot:
    """Capture a small fingerprint of the current page state.

    The four CDP round-trips (title, html, aria, text) are independent;
    running them sequentially adds ~80–200 ms to every step's pre+post.
    `gather` parallelises them to ~max(individual). Errors on aria / text
    are tolerated (best-effort) and surface as empty strings.
    """
    async def _safe_aria() -> str:
        try:
            return await page.locator("body").aria_snapshot()
        except Exception as exc:  # noqa: BLE001
            log.debug("aria_snapshot failed: %s", exc)
            return ""

    async def _safe_text() -> str:
        with contextlib.suppress(Exception):
            return await page.locator("body").inner_text(timeout=2000)
        return ""

    title, html, aria, text = await asyncio.gather(
        page.title(), page.content(), _safe_aria(), _safe_text(),
    )
    # text_excerpt cap intentionally generous — modern API JSON responses
    # (e.g. SEC EFTS search returns 315 hits) easily blow past 5K. The oracle
    # + extract step both consume this, so truncation hurts both.
    return PageSnapshot(
        url=page.url, title=title, dom_hash=dom_hash_string(html),
        aria_snapshot=aria[:8000],
        text_excerpt=text[:20000],
    )


# ── Step execution ──────────────────────────────────────────────────────────

class StepActor:
    """Executes one step. Holds the LocatorResolver + Playwright Page."""

    def __init__(self, page: Page, resolver: LocatorResolver | None = None):
        self.page = page
        self.resolver = resolver or LocatorResolver()
        self.cache_hits = 0
        self.cache_writes = 0
        # Heals = cache hits that took the fingerprint-match drift recovery
        # path (vs the cheap dom_hash-equal path). Counted separately so the
        # TaskResult can report "self-maintenance ran N times".
        self.cache_heals = 0

    async def execute(self, step: Step) -> StepResult:
        pre = await snapshot(self.page)

        # Action-type dispatch ----------------------------------------------
        if step.action_type == ActionType.NAVIGATE:
            return await self._navigate(step, pre)

        if step.action_type == ActionType.EXTRACT:
            return await self._extract(step, pre)

        if step.action_type == ActionType.SCREENSHOT:
            return await self._screenshot(step, pre)

        if step.action_type == ActionType.WAIT_FOR:
            # Treat wait_for like a "snapshot only" step; resolver still tries
            return await self._wait_for(step, pre)

        # Click / Type / Select -- need a locator.
        return await self._element_action(step, pre)

    # -- specific action handlers --

    async def _navigate(self, step: Step, pre: PageSnapshot) -> StepResult:
        if not step.url:
            return StepResult(step_index=step.step_index, success=False,
                              error="navigate step missing url", pre=pre, post=pre)

        host = (urlparse(step.url).hostname or "").lower()

        # SEC blocks Playwright via JA3/TLS fingerprinting (verified
        # 2026-05-01: Playwright + SEC_USER_AGENT still gets HTTP 403; same
        # URL via httpx + same UA returns 200). The architectural fix is to
        # route SEC URLs through httpx; the browser is the wrong tool for
        # an endpoint with an official REST contract.
        if host in _HOSTS_REQUIRING_CONTACT_UA:
            return await self._navigate_via_httpx(step, pre)

        try:
            await self.page.goto(step.url, wait_until="domcontentloaded", timeout=20000)
        except Exception as exc:  # noqa: BLE001
            post = await snapshot(self.page)
            return StepResult(step_index=step.step_index, success=False,
                              error=f"goto failed: {exc}", pre=pre, post=post)
        post = await snapshot(self.page)
        return StepResult(step_index=step.step_index, success=True, pre=pre, post=post)

    async def _navigate_via_httpx(self, step: Step, pre: PageSnapshot) -> StepResult:
        """SEC-style fetch: httpx with SEC compliance UA, no browser.

        Loads the response body into the page via `page.set_content` so
        downstream extract / locator steps see it as if it had been
        navigated to. The URL bar is faked via `page.goto('about:blank')`
        first so the post snapshot's URL matches the requested URL.
        """
        import httpx
        sec_ua = os.environ.get(
            "SEC_USER_AGENT",
            "interview-hw-2026 contact@example.com",
        )
        headers = {
            "User-Agent": sec_ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        }
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as c:
                r = await c.get(step.url, headers=headers)
            if r.status_code >= 400:
                return StepResult(step_index=step.step_index, success=False,
                                  error=f"httpx fetch HTTP {r.status_code} from {step.url}",
                                  pre=pre, post=pre)
            html = r.text
        except Exception as exc:  # noqa: BLE001
            return StepResult(step_index=step.step_index, success=False,
                              error=f"httpx fetch failed: {exc}", pre=pre, post=pre)

        # Push the fetched body into the Playwright page so subsequent
        # locator / extract steps work uniformly.
        try:
            await self.page.set_content(html, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            log.warning("set_content failed (showing first 500 chars in error): %s", exc)
            return StepResult(step_index=step.step_index, success=False,
                              error=f"set_content failed: {exc}", pre=pre, post=pre)

        # snapshot() reads page state. URL will be "about:blank" since
        # set_content doesn't change navigation; build a synthetic snapshot
        # to keep the trajectory honest.
        post_real = await snapshot(self.page)
        post = post_real.model_copy(update={"url": step.url})
        return StepResult(step_index=step.step_index, success=True, pre=pre, post=post)

    async def _extract(self, step: Step, pre: PageSnapshot) -> StepResult:
        # Naïve text extraction; the result is the visible text excerpt.
        # The Planner emits an extract_query; downstream the post-task can
        # apply an LLM digest over `pre.text_excerpt` if richer extraction
        # is needed.
        post = await snapshot(self.page)
        return StepResult(step_index=step.step_index, success=True, pre=pre, post=post,
                          extracted={"query": step.extract_query, "text": post.text_excerpt[:3000]})

    async def _screenshot(self, step: Step, pre: PageSnapshot) -> StepResult:
        try:
            await self.page.screenshot(full_page=False)
        except Exception as exc:  # noqa: BLE001
            return StepResult(step_index=step.step_index, success=False,
                              error=f"screenshot failed: {exc}", pre=pre, post=pre)
        post = await snapshot(self.page)
        return StepResult(step_index=step.step_index, success=True, pre=pre, post=post)

    async def _wait_for(self, step: Step, pre: PageSnapshot) -> StepResult:
        resolution = await self._resolve_with_cache(step)
        try:
            if resolution is not None:
                await resolution.locator.wait_for(state="visible", timeout=10000)
            else:
                await self.page.wait_for_timeout(1500)
        except Exception as exc:  # noqa: BLE001
            post = await snapshot(self.page)
            return StepResult(step_index=step.step_index, success=False,
                              error=f"wait_for failed: {exc}", pre=pre, post=post,
                              locator_tier=resolution.tier if resolution else None,
                              selector=resolution.selector if resolution else None)
        if resolution is not None and resolution.healed:
            await self._persist_heal(step, resolution, pre, label="wait_for")
        post = await snapshot(self.page)
        return StepResult(step_index=step.step_index, success=True, pre=pre, post=post,
                          locator_tier=resolution.tier if resolution else None,
                          selector=resolution.selector if resolution else None,
                          cache_hit=bool(resolution and resolution.cache_hit))

    async def _element_action(self, step: Step, pre: PageSnapshot) -> StepResult:
        resolution = await self._resolve_with_cache(step)
        if resolution is None:
            return StepResult(step_index=step.step_index, success=False,
                              error=f"no locator resolved for intent {step.target_intent!r}",
                              pre=pre, post=pre)
        try:
            if step.action_type == ActionType.CLICK:
                await resolution.locator.click(timeout=8000)
            elif step.action_type == ActionType.TYPE:
                await resolution.locator.fill(step.value or "", timeout=8000)
            elif step.action_type == ActionType.SELECT:
                await resolution.locator.select_option(step.value or "", timeout=8000)
            else:
                return StepResult(step_index=step.step_index, success=False,
                                  error=f"unsupported action_type {step.action_type!r}",
                                  pre=pre, post=pre)
        except Exception as exc:  # noqa: BLE001
            post = await snapshot(self.page)
            return StepResult(step_index=step.step_index, success=False,
                              error=f"action {step.action_type.value} failed: {exc}",
                              pre=pre, post=post,
                              locator_tier=resolution.tier, selector=resolution.selector,
                              cache_hit=resolution.cache_hit)

        post = await snapshot(self.page)

        # Persist to cache. Three cases:
        #   1. ladder resolution (cache_hit=False)        → upsert from scratch
        #   2. cheap cache hit (cache_hit, not healed)    → row already current, bump counter
        #   3. healed cache hit (cache_hit AND healed)    → record_heal + refresh dom_hash
        if not resolution.cache_hit:
            try:
                await self._update_cache(step, resolution, pre.dom_hash, pre.url)
                self.cache_writes += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("selector_cache write failed: %s", exc)
        elif resolution.healed:
            self.cache_hits += 1
            await self._persist_heal(step, resolution, pre, label="element_action")
        else:
            self.cache_hits += 1

        return StepResult(step_index=step.step_index, success=True, pre=pre, post=post,
                          locator_tier=resolution.tier, selector=resolution.selector,
                          cache_hit=resolution.cache_hit)

    # -- cache plumbing --

    async def _resolve_with_cache(self, step: Step):
        cached_selector = None
        cache_dom_match = False
        rec = await cache.lookup(self.page.url, step.target_intent)
        if rec is not None:
            # Selectors are stored already-prefixed (e.g. "role=button[name~='search']").
            cached_selector = rec.selector
            current_dom_hash = dom_hash_string(await self.page.content())
            cache_dom_match = rec.dom_hash == current_dom_hash
        return await self.resolver.resolve(
            self.page,
            step.target_intent,
            hints=step.selector_hints,
            cached_selector=cached_selector,
            cache_dom_match=cache_dom_match,
            cache_record=rec,
        )

    async def _persist_heal(self, step: Step, resolution,
                            pre: PageSnapshot, *, label: str) -> None:
        """Audit the heal and refresh dom_hash so the next lookup takes
        the cheap dom_hash-equal path instead of re-running the heal."""
        self.cache_heals += 1
        try:
            await cache.record_heal(
                url_to_template(pre.url),
                step.target_intent,
                resolution.healing_diff or "fingerprint-matched",
            )
            await self._update_cache(step, resolution, pre.dom_hash, pre.url)
        except Exception as exc:  # noqa: BLE001
            log.warning("selector_cache heal-record (%s) failed: %s", label, exc)

    async def _update_cache(self, step: Step, resolution, pre_dom_hash: str, page_url: str):
        # Extract the strategy from the serialized selector
        if "=" in resolution.selector:
            strategy, _ = resolution.selector.split("=", 1)
        else:
            strategy = resolution.tier.value
        aria_fp = await aria_fingerprint_of(resolution.locator)
        rec = CacheRecord(
            page_url_template=url_to_template(page_url),
            action_intent=step.target_intent,
            selector_strategy=strategy,
            selector=resolution.selector,
            dom_hash=pre_dom_hash,
            aria_fingerprint=aria_fp,
        )
        await cache.upsert(rec)


