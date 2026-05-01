"""Tests for Phase 2 LLM status augmentation.

We mock vote_role so we can verify the trigger logic, prompt building,
response parsing, and override-policy without hitting live endpoints.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from shared.llm_client import VoteResult
from workers.extractor.llm_assist import (
    _parse_status_response,
    augment_status,
    should_augment_status,
    should_override_phase1,
)


# ── should_augment_status — when does Phase 2 fire? ──────────────────────────

def test_should_augment_skips_non_extracted_phase1():
    """Phase 1 verdicts other than 'extracted' are high-confidence; don't escalate."""
    long_content = "x" * 5000
    assert should_augment_status("not_applicable", long_content) is None
    assert should_augment_status("reserved", long_content) is None
    assert should_augment_status("incorporated_by_reference", long_content) is None
    assert should_augment_status("partial", long_content) is None


def test_should_augment_short_extracted():
    """Short 'extracted' content is suspicious — escalate."""
    reason = should_augment_status("extracted", "Short content here.")
    assert reason and "short content" in reason.lower()


def test_should_augment_contains_incorporated_phrase():
    text = (
        "The information called for by this Item is incorporated herein "
        "by reference to the Company's 2025 Proxy Statement, which is filed."
        + " Padding " * 80
    )
    reason = should_augment_status("extracted", text)
    assert reason and "incorporated by reference" in reason.lower()


def test_should_augment_within_doc_see_item():
    """The 'See Item N below' pattern (Chemical Banking 1995 etc.)"""
    text = (
        "Information regarding directors and executive officers. "
        "See Item 13 below for further detail." + " Padding " * 80
    )
    reason = should_augment_status("extracted", text)
    assert reason and "see item" in reason.lower()


def test_should_augment_remaining_information():
    """Apple 2024 Item 10 'remaining information' partial-disclosure phrasing."""
    text = (
        "We adopted an insider trading policy on October 1, 2024. " * 5
        + "Remaining information required by this Item is incorporated by reference "
        "to the Proxy Statement."
    )
    reason = should_augment_status("extracted", text)
    assert reason  # one of the three triggers should fire


def test_should_augment_long_clean_extracted_skipped():
    text = "We make iPhones, iPads, Macs and accessories. " * 100
    assert should_augment_status("extracted", text) is None


def test_should_augment_skips_cross_ref_toc_items():
    """GE 2021's cross-ref TOC items have a known marker in content_text;
    the TOC's status_hint is more authoritative than any LLM re-judgment."""
    toc_content = "[Cross-reference TOC] Business (4, 10-18, 103-104)"
    assert should_augment_status("extracted", toc_content) is None


# ── _parse_status_response — robust JSON parsing ─────────────────────────────

def test_parse_clean_json():
    raw = '{"status": "incorporated_by_reference", "confidence": 0.85, "rationale": "..."}'
    assert _parse_status_response(raw) == "incorporated_by_reference"


def test_parse_with_code_fences():
    raw = '```json\n{"status": "partial", "confidence": 0.7, "rationale": "mixed"}\n```'
    assert _parse_status_response(raw) == "partial"


def test_parse_with_surrounding_prose():
    raw = (
        'Here is my analysis:\n'
        '{"status": "not_applicable", "confidence": 0.99, "rationale": "explicit"}\n'
        'Hope that helps!'
    )
    assert _parse_status_response(raw) == "not_applicable"


def test_parse_status_value_normalized_to_lower():
    raw = '{"status": "EXTRACTED", "confidence": 0.5, "rationale": "x"}'
    assert _parse_status_response(raw) == "extracted"


def test_parse_invalid_status_raises():
    raw = '{"status": "NOT_A_REAL_CATEGORY", "confidence": 0.9, "rationale": "x"}'
    with pytest.raises(ValueError):
        _parse_status_response(raw)


def test_parse_no_json_raises():
    raw = "I don't know what to say."
    with pytest.raises(ValueError):
        _parse_status_response(raw)


def test_parse_missing_status_field_raises():
    raw = '{"confidence": 0.9, "rationale": "I forgot the status field"}'
    with pytest.raises(ValueError):
        _parse_status_response(raw)


# ── should_override_phase1 — override threshold policy ───────────────────────

def test_override_when_majority_disagrees():
    vote = VoteResult(pick="incorporated_by_reference", confidence=2 / 3, votes=[])
    assert should_override_phase1(vote, phase1_status="extracted") is True


def test_no_override_when_majority_agrees_with_phase1():
    """If the LLM vote also says 'extracted', no override needed."""
    vote = VoteResult(pick="extracted", confidence=1.0, votes=[])
    assert should_override_phase1(vote, phase1_status="extracted") is False


def test_no_override_on_tie_fallback():
    vote = VoteResult(pick="extracted", confidence=0.0, votes=[], fallback_used=True)
    assert should_override_phase1(vote, phase1_status="extracted") is False


def test_no_override_below_confidence_threshold():
    """Edge case: 1 of 3 voters disagreed (confidence=1/3) — not enough."""
    vote = VoteResult(pick="incorporated_by_reference", confidence=1 / 3, votes=[])
    assert should_override_phase1(vote, phase1_status="extracted") is False


def test_override_at_threshold_with_two_thirds():
    vote = VoteResult(pick="not_applicable", confidence=0.67, votes=[])
    assert should_override_phase1(vote, phase1_status="extracted") is True


# ── augment_status — integration with vote_role ──────────────────────────────

@pytest.mark.asyncio
async def test_augment_status_returns_vote_result():
    fake_vote = VoteResult(
        pick="incorporated_by_reference",
        confidence=2 / 3,
        votes=[("deepseek", "incorporated_by_reference", "..."),
               ("nemotron", "incorporated_by_reference", "..."),
               ("mistral", "extracted", "...")],
    )

    async def fake_vote_role(role, **kwargs):
        # Verify the parser is wired and the prompt has the expected shape
        assert role == "extractor_aug"
        assert callable(kwargs["parser"])
        assert kwargs["fallback"] == "extracted"
        return fake_vote

    with patch("workers.extractor.llm_assist.vote_role", fake_vote_role):
        result = await augment_status(
            item_number="11", item_title="Executive Compensation",
            content_text="The information called for is incorporated by reference",
            phase1_status="extracted",
            trigger_reason="contains 'incorporated by reference' phrase",
        )
    assert result.pick == "incorporated_by_reference"
    assert result.confidence == pytest.approx(2 / 3)
