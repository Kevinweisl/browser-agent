"""Tests for the silent-failure detection cascade (browser worker)."""

from __future__ import annotations

from workers.browser.schema import ActionType, NegativeOracle, PageSnapshot, Step
from workers.browser.silent_failure import (
    aria_snapshot_diff,
    collect_signals,
    is_suspicious,
    negative_oracle_violations,
)


def _snap(*, url="https://x", title="X", dom_hash="abc",
          aria="", text="") -> PageSnapshot:
    return PageSnapshot(url=url, title=title, dom_hash=dom_hash,
                        aria_snapshot=aria, text_excerpt=text)


def _step(action_type: ActionType, *, must_appear=None, must_not_appear=None) -> Step:
    return Step(
        step_index=1,
        action_type=action_type,
        target_intent="test",
        success_criteria=NegativeOracle(
            must_appear=must_appear or [],
            must_not_appear=must_not_appear or [],
        ),
    )


# ── aria_snapshot_diff ────────────────────────────────────────────────────────

def test_aria_diff_returns_none_when_disabled():
    pre = _snap(aria="")
    post = _snap(aria="")
    assert aria_snapshot_diff(pre, post) is None


def test_aria_diff_returns_empty_when_unchanged():
    pre = _snap(aria="- button: 'Submit'")
    post = _snap(aria="- button: 'Submit'")
    assert aria_snapshot_diff(pre, post) == ""


def test_aria_diff_emits_unified_diff_when_changed():
    pre = _snap(aria="- button: 'Submit'")
    post = _snap(aria="- button: 'Search'")
    diff = aria_snapshot_diff(pre, post)
    assert diff
    assert "Submit" in diff and "Search" in diff


# ── collect_signals ──────────────────────────────────────────────────────────

def test_collect_signals_passive_no_change():
    pre = _snap(url="u", title="T", dom_hash="h", aria="A")
    post = _snap(url="u", title="T", dom_hash="h", aria="A")
    step = _step(ActionType.EXTRACT)
    sigs = collect_signals(step, pre, post)
    assert "no_visible_state_change_after_passive_action" in sigs
    assert "no_visible_state_change_after_mutating_action" not in sigs


def test_collect_signals_mutating_no_change_is_suspicious():
    pre = _snap(url="u", title="T", dom_hash="h", aria="A")
    post = _snap(url="u", title="T", dom_hash="h", aria="A")
    step = _step(ActionType.CLICK)
    sigs = collect_signals(step, pre, post)
    assert "no_visible_state_change_after_mutating_action" in sigs
    assert is_suspicious(sigs)


def test_collect_signals_url_change():
    pre = _snap(url="https://a", dom_hash="h")
    post = _snap(url="https://b", dom_hash="h2")
    step = _step(ActionType.NAVIGATE)
    sigs = collect_signals(step, pre, post)
    assert "url_changed" in sigs
    assert "dom_changed" in sigs
    assert not is_suspicious(sigs)


def test_collect_signals_aria_change_only():
    pre = _snap(aria="- listbox: A", dom_hash="h")
    post = _snap(aria="- listbox: B", dom_hash="h")
    step = _step(ActionType.CLICK)
    sigs = collect_signals(step, pre, post)
    assert "aria_snapshot_changed" in sigs
    assert "dom_changed" not in sigs


# ── negative_oracle_violations ───────────────────────────────────────────────

def test_oracle_must_appear_present():
    post = _snap(text="Welcome back, Kevin!")
    step = _step(ActionType.NAVIGATE, must_appear=["Welcome back"])
    assert negative_oracle_violations(step, post) == []


def test_oracle_must_appear_missing():
    post = _snap(text="Welcome back, Kevin!")
    step = _step(ActionType.NAVIGATE, must_appear=["Welcome back", "Log out"])
    violations = negative_oracle_violations(step, post)
    assert len(violations) == 1
    assert "Log out" in violations[0]


def test_oracle_must_not_appear_violated():
    post = _snap(text="404 page not found")
    step = _step(ActionType.NAVIGATE, must_not_appear=["page not found"])
    violations = negative_oracle_violations(step, post)
    assert len(violations) == 1
    assert "page not found" in violations[0]


def test_oracle_case_insensitive():
    post = _snap(text="WELCOME")
    step = _step(ActionType.NAVIGATE, must_appear=["welcome"])
    assert negative_oracle_violations(step, post) == []
