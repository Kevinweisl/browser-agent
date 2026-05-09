"""Tests for the locator-ladder helper logic that doesn't require a real Page.

Live ladder behavior (resolving roles/text against a real DOM) is exercised
in the end-to-end smoke task — these unit tests only cover the heuristics,
the CSS-safety guard, and the cache/heal Tier-1 paths via Playwright fakes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from workers.browser.locator_ladder import (
    LocatorResolver,
    _is_safe_css,
    infer_name,
    infer_role,
)
from workers.browser.schema import LocatorTier, SelectorHints
from workers.browser.selector_cache import CacheRecord

# ── infer_role ───────────────────────────────────────────────────────────────

def test_infer_role_button():
    assert infer_role("the search button") == "button"


def test_infer_role_textbox_from_input():
    assert infer_role("the email input field") == "textbox"


def test_infer_role_searchbox():
    assert infer_role("the main search box") == "searchbox"


def test_infer_role_dropdown():
    assert infer_role("the country dropdown") == "combobox"


def test_infer_role_unknown():
    assert infer_role("a thing on the page") is None


# ── infer_name ───────────────────────────────────────────────────────────────

def test_infer_name_strips_click_verb():
    assert infer_name("click the submit button") == "submit"


def test_infer_name_strips_press_verb():
    assert infer_name("press search") == "search"


def test_infer_name_strips_role_tail():
    assert infer_name("login link") == "login"


def test_infer_name_handles_quoted():
    assert infer_name("click 'Sign In'") == "sign in"


# ── _is_safe_css ─────────────────────────────────────────────────────────────

def test_safe_css_allows_id():
    assert _is_safe_css("#submit") is True


def test_safe_css_allows_data_attr():
    assert _is_safe_css("[data-testid='go']") is True


def test_safe_css_rejects_class_chain():
    assert _is_safe_css(".btn-primary") is False
    assert _is_safe_css("div.btn") is False


def test_safe_css_allows_tag():
    assert _is_safe_css("button") is True


# ── Tier 1 cache / heal paths ────────────────────────────────────────────────
#
# We don't have a real Playwright Page in unit tests. The resolver only
# touches a small surface of Page (`get_by_role` + `locator(...)`) and
# Locator (`count`, `filter`, `get_attribute`, `evaluate`, `text_content`),
# all of which are async. AsyncMock + MagicMock cover this cleanly.

def _mk_locator(
    *,
    count: int = 1,
    role: str | None = None,
    aria_label: str | None = None,
    id_: str | None = None,
    data_testid: str | None = None,
    tag: str = "button",
    text: str = "",
):
    """Construct a fake Playwright Locator that reports given attributes.

    `.filter(...)` returns the same locator (we never test ambiguity here —
    the heal tests assume the cached selector resolves to exactly one
    element, which is what `count=1` gives us via `_narrow`'s fast path).
    """
    loc = MagicMock(name=f"Locator(role={role!r})")
    loc.count = AsyncMock(return_value=count)
    loc.filter = MagicMock(return_value=loc)
    loc.get_attribute = AsyncMock(side_effect=lambda attr: {
        "role": role,
        "aria-label": aria_label,
        "id": id_,
        "data-testid": data_testid,
    }.get(attr))
    loc.evaluate = AsyncMock(return_value=tag.upper())
    loc.text_content = AsyncMock(return_value=text)
    return loc


def _mk_page(role_locator):
    """Page where `get_by_role(...)` returns the given fake locator."""
    page = MagicMock(name="Page")
    page.get_by_role = MagicMock(return_value=role_locator)
    page.locator = MagicMock(return_value=role_locator)
    page.get_by_label = MagicMock(return_value=role_locator)
    page.get_by_test_id = MagicMock(return_value=role_locator)
    page.get_by_text = MagicMock(return_value=role_locator)
    return page


@pytest.mark.asyncio
async def test_resolver_cache_hit_dom_equal():
    """dom_hash matches → cheap CACHED hit, no fingerprint check needed."""
    loc = _mk_locator(role="button", id_="submit")
    page = _mk_page(loc)
    resolver = LocatorResolver()

    res = await resolver.resolve(
        page,
        intent="click submit",
        cached_selector="role=button[name~='submit']",
        cache_dom_match=True,
        cache_record=None,  # unused on the equal path
    )
    assert res is not None
    assert res.tier == LocatorTier.CACHED
    assert res.cache_hit is True
    assert res.healed is False
    assert res.healing_diff is None


@pytest.mark.asyncio
async def test_resolver_heal_path_dom_drift_fingerprint_match():
    """dom_hash differs but stored fingerprint still matches → healed=True."""
    # Stored fingerprint and current element agree on role+id (2/4 strong).
    loc = _mk_locator(
        role="button", aria_label="Sign In", id_="login-btn",
        data_testid=None, tag="button", text="Sign In",
    )
    page = _mk_page(loc)
    rec = CacheRecord(
        page_url_template="https://x.com/login",
        action_intent="click login",
        selector_strategy="role",
        selector="role=button[name~='login']",
        dom_hash="OLD_HASH",
        aria_fingerprint={
            "role": "button",
            "aria_label": "Login",  # drifted: Login → Sign In
            "id": "login-btn",
            "data_testid": None,
            "tag": "button",
            "text": "Login",
        },
    )
    resolver = LocatorResolver()
    res = await resolver.resolve(
        page,
        intent="click login",
        cached_selector=rec.selector,
        cache_dom_match=False,
        cache_record=rec,
    )
    assert res is not None
    assert res.tier == LocatorTier.CACHED
    assert res.cache_hit is True
    assert res.healed is True
    assert res.healing_diff is not None
    assert "aria_label" in res.healing_diff


@pytest.mark.asyncio
async def test_resolver_heal_fails_fingerprint_mismatch():
    """dom_hash drifts AND fingerprint mismatches → CACHED tier refuses.

    After Tier 1 refuses, the resolver continues down the ladder. We only
    assert the heal path itself didn't fire — the resolver must NOT return
    a `Resolution(tier=CACHED)` from the bogus cached selector. Whether a
    later tier (text/role) recovers is independent of the heal logic.
    """
    # role matches (Tier-1 hard role-check passes) but every other strong
    # attr drifted → only 1/4, below threshold → no heal.
    loc = _mk_locator(
        role="button", aria_label="Cancel", id_="cancel-btn",
        data_testid="cancel", tag="button", text="Cancel",
    )
    page = _mk_page(loc)
    rec = CacheRecord(
        page_url_template="https://x.com/login",
        action_intent="click submit",
        selector_strategy="role",
        selector="role=button[name~='submit']",
        dom_hash="OLD",
        aria_fingerprint={
            "role": "button",
            "aria_label": "Submit",
            "id": "submit-btn",
            "data_testid": "submit",
            "tag": "button",
            "text": "Submit",
        },
    )
    resolver = LocatorResolver()
    res = await resolver.resolve(
        page,
        intent="frobnicate the widget",
        hints=SelectorHints(),
        cached_selector=rec.selector,
        cache_dom_match=False,
        cache_record=rec,
    )
    # Heal must NOT fire — either we get None or a non-CACHED, non-healed
    # resolution from a later tier.
    if res is not None:
        assert res.tier != LocatorTier.CACHED
        assert res.healed is False


@pytest.mark.asyncio
async def test_resolver_heal_role_mismatch_hard_fails():
    """Stored role=button but current element role=link → heal must refuse."""
    loc = _mk_locator(
        role="link", aria_label="Submit", id_="submit-btn",
        data_testid="submit", tag="a", text="Submit",
    )
    page = _mk_page(loc)
    rec = CacheRecord(
        page_url_template="https://x.com/form",
        action_intent="click submit",
        selector_strategy="role",
        selector="role=button[name~='submit']",
        dom_hash="OLD",
        aria_fingerprint={
            "role": "button",
            "aria_label": "Submit",
            "id": "submit-btn",
            "data_testid": "submit",
            "tag": "button",
            "text": "Submit",
        },
    )
    resolver = LocatorResolver()
    res = await resolver.resolve(
        page,
        intent="frobnicate the widget",
        hints=SelectorHints(),
        cached_selector=rec.selector,
        cache_dom_match=False,
        cache_record=rec,
    )
    if res is not None:
        assert res.tier != LocatorTier.CACHED
        assert res.healed is False


@pytest.mark.asyncio
async def test_resolver_heal_skipped_when_no_stored_fingerprint():
    """Old cache rows pre-fingerprint shouldn't crash the heal path."""
    loc = _mk_locator(role="button", id_="x")
    page = _mk_page(loc)
    rec = CacheRecord(
        page_url_template="https://x.com/p",
        action_intent="click x",
        selector_strategy="role",
        selector="role=button[name~='x']",
        dom_hash="OLD",
        aria_fingerprint=None,  # legacy row, fingerprint never recorded
    )
    resolver = LocatorResolver()
    res = await resolver.resolve(
        page,
        intent="frobnicate widget",
        hints=SelectorHints(),
        cached_selector=rec.selector,
        cache_dom_match=False,
        cache_record=rec,
    )
    # Critical: did NOT crash, and did NOT report a CACHED hit (no
    # fingerprint to anchor on means heal is impossible).
    if res is not None:
        assert res.tier != LocatorTier.CACHED
        assert res.healed is False
