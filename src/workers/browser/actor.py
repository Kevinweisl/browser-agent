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

import logging
from typing import TYPE_CHECKING

from . import selector_cache as cache
from .locator_ladder import LocatorResolver
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

if TYPE_CHECKING:
    from playwright.async_api import Page

log = logging.getLogger(__name__)


# ── PageSnapshot capture ────────────────────────────────────────────────────

async def snapshot(page: Page) -> PageSnapshot:
    """Capture a small fingerprint of the current page state."""
    url = page.url
    title = await page.title()
    html = await page.content()
    dom_hash = dom_hash_string(html)
    aria = ""
    try:
        aria = await page.locator("body").aria_snapshot()
    except Exception as exc:  # noqa: BLE001
        log.debug("aria_snapshot failed: %s", exc)
    text = ""
    import contextlib
    with contextlib.suppress(Exception):
        text = await page.locator("body").inner_text(timeout=2000)
    return PageSnapshot(
        url=url, title=title, dom_hash=dom_hash,
        aria_snapshot=aria[:8000],
        text_excerpt=text[:5000],
    )


# ── Aria-fingerprint extraction (cache value extra) ─────────────────────────

async def _aria_fingerprint(locator) -> dict | None:
    """Extract a small bag of attributes used by Healenium-style heal logic.

    On any failure, return None — fingerprint is best-effort.
    """
    try:
        return {
            "role": await locator.get_attribute("role"),
            "aria_label": await locator.get_attribute("aria-label"),
            "id": await locator.get_attribute("id"),
            "data_testid": await locator.get_attribute("data-testid"),
            "tag": (await locator.evaluate("el => el.tagName")).lower() if locator else None,
            "text": (await locator.text_content() or "").strip()[:120],
        }
    except Exception:  # noqa: BLE001
        return None


# ── Step execution ──────────────────────────────────────────────────────────

class StepActor:
    """Executes one step. Holds the LocatorResolver + Playwright Page."""

    def __init__(self, page: Page, resolver: LocatorResolver | None = None):
        self.page = page
        self.resolver = resolver or LocatorResolver()
        self.cache_hits = 0
        self.cache_writes = 0

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
        try:
            await self.page.goto(step.url, wait_until="domcontentloaded", timeout=20000)
        except Exception as exc:  # noqa: BLE001
            post = await snapshot(self.page)
            return StepResult(step_index=step.step_index, success=False,
                              error=f"goto failed: {exc}", pre=pre, post=post)
        post = await snapshot(self.page)
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

        # On success, persist to cache (but ONLY if we resolved via the ladder
        # — pure CACHED hits already exist in the table)
        if not resolution.cache_hit:
            try:
                await self._update_cache(step, resolution, pre.dom_hash, pre.url)
                self.cache_writes += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("selector_cache write failed: %s", exc)
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
        )

    async def _update_cache(self, step: Step, resolution, pre_dom_hash: str, page_url: str):
        # Extract the strategy from the serialized selector
        if "=" in resolution.selector:
            strategy, _ = resolution.selector.split("=", 1)
        else:
            strategy = resolution.tier.value
        aria_fp = await _aria_fingerprint(resolution.locator)
        rec = CacheRecord(
            page_url_template=url_to_template(page_url),
            action_intent=step.target_intent,
            selector_strategy=strategy,
            selector=resolution.selector,
            dom_hash=pre_dom_hash,
            aria_fingerprint=aria_fp,
        )
        await cache.upsert(rec)


