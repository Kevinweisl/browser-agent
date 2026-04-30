"""Status classifier tests — the core of Phase 1.

Test patterns are adapted from real Apple 2024 10-K fragments and known SEC boilerplate.
"""

from workers.extractor.classifier import classify_status


# --- reserved ---

def test_reserved_canonical():
    assert classify_status("Item 6. [Reserved]\n") == "reserved"


def test_reserved_uppercase_with_extra_space():
    assert classify_status("ITEM 6 [ RESERVED ]") == "reserved"


# --- not_applicable ---

def test_not_applicable_period():
    assert classify_status("Not applicable.\n") == "not_applicable"


def test_none_short():
    assert classify_status("None.") == "not_applicable"


def test_not_applicable_lowercase():
    assert classify_status("  not applicable  ") == "not_applicable"


def test_substantive_text_with_word_none_inside_is_NOT_not_applicable():
    long = "We operate in many segments. None of our subsidiaries " * 30
    assert classify_status(long) != "not_applicable"


# --- incorporated_by_reference ---

def test_incorporated_canonical_apple_item11():
    text = (
        "The information required by this Item regarding executive compensation "
        "will be included in the 2025 Proxy Statement, and is incorporated herein by reference."
    )
    assert classify_status(text) == "incorporated_by_reference"


def test_incorporated_called_for_variant():
    text = (
        "The information called for by this Item is set forth in the 2025 Proxy Statement "
        "and is incorporated herein by reference."
    )
    assert classify_status(text) == "incorporated_by_reference"


# --- negative control ---

def test_websites_referenced_are_NOT_incorporated():
    # Apple 2024 cover-page boilerplate (paraphrased to ensure no canonical inc match)
    text = (
        "The information contained on the websites referenced in this Form 10-K is "
        "not incorporated by reference into this filing."
    )
    assert classify_status(text) == "extracted"


# --- extracted ---

def test_substantive_business_description_is_extracted():
    text = "We are a technology company headquartered in Cupertino, California. " * 20
    assert classify_status(text) == "extracted"


def test_empty_text_is_extracted():
    # an empty body should not trigger any classifier rule
    assert classify_status("") == "extracted"


# --- partial (mixed inline + by-reference) ---

def test_partial_mixed_apple_item10_pattern():
    # Apple 2024 Item 10 has inline insider-trading-policy paragraph then by-ref clause.
    text = (
        "We have adopted an Insider Trading Policy that governs the purchase, sale, "
        "and other dispositions of the company's securities by directors, officers, "
        "and employees, and is reasonably designed to promote compliance with insider "
        "trading laws, rules, and regulations. " * 5
        + "\n\n"
        + "The information required by this Item regarding directors, executive officers "
        "and corporate governance will be included in the 2025 Proxy Statement and is "
        "incorporated herein by reference."
    )
    assert classify_status(text) == "partial"
