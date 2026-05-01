"""7-tier locator ladder.

Order from cheapest/most-stable to most-expensive/least-stable, per the
research delta (browser-agent-update.md §1):

    1. CACHED          — selector_cache lookup, dom_hash match
    2. GET_BY_ROLE     — Playwright official first choice
    3. GET_BY_LABEL    — form-element preferred
    4. GET_BY_TEST_ID  — automation-only, brittle to dev practice
    5. GET_BY_TEXT     — visible text anchor
    6. CSS_NO_CLASS    — id / data-* only (class chains are anti-pattern)
    7. VISION_FALLBACK — Computer Use zoom+click (stub for Day 6 — needs
                         Anthropic CU beta; real impl in a future task)

We deliberately drop the original design's standalone "ARIA" tier (subsumed
by `getByRole`) and "XPath" tier (Playwright officially anti-pattern).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .schema import LocatorTier, SelectorHints

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page

log = logging.getLogger(__name__)


@dataclass
class Resolution:
    """Result of a locator ladder resolve attempt."""
    tier: LocatorTier
    selector: str            # serialized form for cache + audit
    locator: Locator
    cache_hit: bool = False


# ── Heuristics for inferring tier-2-6 hints from a free-form intent ─────────

_ROLE_KEYWORDS = {
    "button": "button",
    "link": "link",
    "textbox": "textbox",
    "input": "textbox",
    "search box": "searchbox",
    "search field": "searchbox",
    "checkbox": "checkbox",
    "radio": "radio",
    "dropdown": "combobox",
    "select": "combobox",
    "menu": "menu",
    "menuitem": "menuitem",
    "tab": "tab",
    "list item": "listitem",
}


def infer_role(intent: str) -> str | None:
    lo = intent.lower()
    for needle, role in _ROLE_KEYWORDS.items():
        if needle in lo:
            return role
    return None


def infer_name(intent: str) -> str | None:
    """Strip role keywords + connectives to leave the likely accessible name.

    'click the search button' → 'search'
    'submit form'            → 'submit'
    """
    lo = intent.lower().strip()
    # Order matters — strip the longer prefix first so 'click the X' doesn't
    # leave 'the X' after the shorter 'click ' matches.
    for verb in ("click the ", "press the ", "tap the ", "select the ",
                 "choose the ", "click ", "press ", "tap ", "select ",
                 "choose "):
        if lo.startswith(verb):
            lo = lo[len(verb):]
            break
    for tail in (" button", " link", " input", " field", " textbox",
                 " checkbox", " menu", " tab"):
        if lo.endswith(tail):
            lo = lo[:-len(tail)]
            break
    lo = lo.strip().strip("'\"")
    return lo or None


# ── Resolver ─────────────────────────────────────────────────────────────────

class LocatorResolver:
    """Walks the 7-tier ladder for a given intent + optional hints.

    `cached_selector` is the serialized form from selector_cache (e.g.
    `'role=button[name="Search"]'` or `'css=#submit'`); if provided AND
    `cache_dom_match=True`, the resolver tries it first as tier CACHED.

    `vision_fallback` is an optional callable for tier 7. If absent, tier 7 is
    skipped (most demos won't need it).
    """

    def __init__(self, *, vision_fallback=None):
        self.vision_fallback = vision_fallback

    async def resolve(
        self,
        page: Page,
        intent: str,
        *,
        hints: SelectorHints | None = None,
        cached_selector: str | None = None,
        cache_dom_match: bool = False,
    ) -> Resolution | None:
        hints = hints or SelectorHints()

        # Tier 1: cached selector (only if dom_hash matched)
        if cached_selector and cache_dom_match:
            loc = self._materialize_serialized(page, cached_selector)
            narrowed = await _narrow(loc, hints) if loc is not None else None
            if narrowed is not None:
                return Resolution(LocatorTier.CACHED, cached_selector, narrowed, True)

        # Tier 2: getByRole
        role = hints.role or infer_role(intent)
        name = hints.name or infer_name(intent)
        if role:
            kwargs = {}
            if name:
                kwargs["name"] = re.compile(re.escape(name), re.IGNORECASE)
            loc = page.get_by_role(role, **kwargs)
            narrowed = await _narrow(loc, hints, free_text=name)
            if narrowed is not None:
                serialized = f"role={role}" + (f"[name~={name!r}]" if name else "")
                return Resolution(LocatorTier.GET_BY_ROLE, serialized, narrowed)

        # Tier 3: getByLabel
        label = hints.label
        if label:
            loc = page.get_by_label(label)
            narrowed = await _narrow(loc, hints, free_text=label)
            if narrowed is not None:
                return Resolution(LocatorTier.GET_BY_LABEL, f"label={label!r}", narrowed)

        # Tier 4: getByTestId
        test_id = hints.test_id
        if test_id:
            loc = page.get_by_test_id(test_id)
            narrowed = await _narrow(loc, hints)
            if narrowed is not None:
                return Resolution(LocatorTier.GET_BY_TEST_ID, f"testid={test_id}", narrowed)

        # Tier 5: getByText
        text = hints.text or name
        if text:
            loc = page.get_by_text(text, exact=False)
            narrowed = await _narrow(loc, hints, free_text=text)
            if narrowed is not None:
                return Resolution(LocatorTier.GET_BY_TEXT, f"text={text!r}", narrowed)

        # Tier 6: CSS without class (id/data-* only)
        css = hints.css
        if css and _is_safe_css(css):
            loc = page.locator(css)
            narrowed = await _narrow(loc, hints)
            if narrowed is not None:
                return Resolution(LocatorTier.CSS_NO_CLASS, f"css={css}", narrowed)

        # Tier 7: vision fallback (stubbed)
        if self.vision_fallback is not None:
            res = await self.vision_fallback(page, intent, hints)
            if res is not None:
                return res

        return None

    def _materialize_serialized(self, page: Page, serialized: str) -> Locator | None:
        """Reverse of the serializer: turn a stored selector string back into a Locator."""
        if serialized.startswith("role="):
            # role=button[name~='search'] → role=button + name=search
            m = re.match(r"role=([\w-]+)(?:\[name~=(.+)\])?$", serialized)
            if not m:
                return None
            role = m.group(1)
            name_repr = m.group(2)
            kwargs = {}
            if name_repr:
                try:
                    name = name_repr.strip("'\"")
                    kwargs["name"] = re.compile(re.escape(name), re.IGNORECASE)
                except Exception:  # noqa: BLE001
                    return None
            return page.get_by_role(role, **kwargs)
        if serialized.startswith("label="):
            return page.get_by_label(serialized[len("label="):].strip("'\""))
        if serialized.startswith("testid="):
            return page.get_by_test_id(serialized[len("testid="):])
        if serialized.startswith("text="):
            return page.get_by_text(serialized[len("text="):].strip("'\""), exact=False)
        if serialized.startswith("css="):
            return page.locator(serialized[len("css="):])
        return None


_RE_CSS_CLASS = re.compile(r"\.[a-zA-Z_-]")


def _is_safe_css(css: str) -> bool:
    """Disallow .class chains (research § anti-patterns)."""
    return not _RE_CSS_CLASS.search(css)


async def _has_one(locator: Locator) -> bool:
    """A locator is usable if it resolves to exactly one element.

    `count() > 0` would let `nth(0)`-style fragility through. We require
    exactly one match so the resolver is unambiguous.
    """
    try:
        n = await locator.count()
    except Exception:  # noqa: BLE001
        return False
    return n == 1


async def _narrow(
    locator: Locator,
    hints: SelectorHints,
    *,
    free_text: str | None = None,
) -> Locator | None:
    """Try to narrow a locator down to exactly one element via a chain.

    Order (per research delta §1, Stagehand's observe-style ranking, but
    WITHOUT the `.first()` fallback after we measured it caused regressions):
      1. raw locator             — already unambiguous, done
      2. .filter(visible=True)   — kills off-screen / dialog twins
      3. .filter(has_text=hints.text)
      4. .filter(has_text=free_text)
      Returns None when still ambiguous; ladder tries the next tier.

    Why no `.first()`: empirically `.first()` clicked the wrong element
    (e.g. Wikipedia TOC entry vs body header) often enough that the
    validator REPLAN'd anyway, paying for both a bad action and a bad
    validate. Better to fail this tier and let the planner replan.
    """
    try:
        n = await locator.count()
    except Exception:  # noqa: BLE001
        return None
    if n == 0:
        return None
    if n == 1:
        return locator

    # Step 1: visible only
    try:
        visible = locator.filter(visible=True)
        n_v = await visible.count()
    except Exception:  # noqa: BLE001
        n_v = 0
    if n_v == 1:
        return visible
    if n_v >= 1:
        locator = visible  # keep narrowing from here

    # Step 2: filter by hint text
    if hints.text:
        try:
            with_text = locator.filter(has_text=hints.text)
            n_t = await with_text.count()
        except Exception:  # noqa: BLE001
            n_t = 0
        if n_t == 1:
            return with_text
        if n_t >= 1:
            locator = with_text

    # Step 3: filter by free_text (the inferred name from intent)
    if free_text and (not hints.text or free_text != hints.text):
        try:
            with_free = locator.filter(has_text=free_text)
            n_f = await with_free.count()
        except Exception:  # noqa: BLE001
            n_f = 0
        if n_f == 1:
            return with_free
        # don't narrow further if still ambiguous — fall through to None

    log.info("locator narrowed but still ambiguous "
             "(hints=%s, free_text=%r) — skipping tier", hints.model_dump(), free_text)
    return None
