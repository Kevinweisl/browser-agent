"""Tests for the locator-ladder helper logic that doesn't require a real Page.

Live ladder behavior (resolving roles/text against a real DOM) is exercised
in the end-to-end smoke task — these unit tests only cover the heuristics
and CSS-safety guard.
"""

from __future__ import annotations

from workers.browser.locator_ladder import (
    _is_safe_css,
    infer_name,
    infer_role,
)

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
