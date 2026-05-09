"""Unit tests for the selector_cache helper functions (URL templating + hashing).

Postgres roundtrip is exercised in the live smoke test, not in unit tests
(would require a running DB and pollute the cache during eval runs).
"""

from __future__ import annotations

from workers.browser.selector_cache import (
    dom_hash_string,
    fingerprint_match,
    intent_hash,
    url_to_template,
)


def test_url_template_strips_query_and_fragment():
    assert url_to_template("https://x.com/a?b=1#c") == "https://x.com/a"


def test_url_template_replaces_numeric_path_segments():
    out = url_to_template("https://x.com/users/12345/orders/789")
    assert out == "https://x.com/users/{}/orders/{}"


def test_url_template_replaces_hex_segments():
    out = url_to_template("https://x.com/v1/abc12345/data")
    assert out == "https://x.com/v1/{}/data"


def test_url_template_keeps_non_numeric_segments():
    out = url_to_template("https://x.com/users/profile/edit")
    assert out == "https://x.com/users/profile/edit"


def test_intent_hash_stable_and_short():
    h1 = intent_hash("Search button")
    h2 = intent_hash("search button")
    h3 = intent_hash("  Search Button ")
    assert h1 == h2 == h3
    assert len(h1) == 16


def test_intent_hash_different_for_different_intents():
    assert intent_hash("a") != intent_hash("b")


def test_dom_hash_string_stable():
    h1 = dom_hash_string("<html>hi</html>")
    h2 = dom_hash_string("<html>hi</html>")
    assert h1 == h2
    assert len(h1) == 16


def test_dom_hash_string_diverges():
    h1 = dom_hash_string("<html>hi</html>")
    h2 = dom_hash_string("<html>HI</html>")
    assert h1 != h2


# ── fingerprint_match ────────────────────────────────────────────────────────
# Heal threshold: ≥ 2 of {role, aria_label, id, data_testid} must match,
# AND if stored.role is set it must equal current.role exactly.

def test_fingerprint_match_2_of_4_strong_attrs():
    """role + id same, aria_label drifted, data_testid both None → 2/4 match → heal."""
    stored = {
        "role": "button", "aria_label": "Submit form", "id": "submit",
        "data_testid": None, "tag": "button", "text": "Submit",
    }
    current = {
        "role": "button", "aria_label": "Send", "id": "submit",
        "data_testid": None, "tag": "button", "text": "Send",
    }
    matched, diff = fingerprint_match(stored, current)
    assert matched is True
    assert "aria_label" in diff  # the diff should mention the drifted attr


def test_fingerprint_match_role_mismatch_fails():
    """Role drift is a hard fail even if id+aria_label+data_testid all match."""
    stored = {
        "role": "button", "aria_label": "Go", "id": "go",
        "data_testid": "go-btn", "tag": "button", "text": "Go",
    }
    current = {
        "role": "link", "aria_label": "Go", "id": "go",
        "data_testid": "go-btn", "tag": "a", "text": "Go",
    }
    matched, diff = fingerprint_match(stored, current)
    assert matched is False
    assert "role drift" in diff


def test_fingerprint_match_only_1_of_4_fails():
    """Only id matches, everything else drifted → below threshold → no heal."""
    stored = {
        "role": "button", "aria_label": "Submit", "id": "go",
        "data_testid": "submit-btn", "tag": "button", "text": "Submit",
    }
    current = {
        "role": "button", "aria_label": "Different", "id": "different",
        "data_testid": "different-btn", "tag": "button", "text": "Different",
    }
    matched, diff = fingerprint_match(stored, current)
    assert matched is False
    # role matched (1) — only that one strong attr matched, below threshold
    assert "1/4" in diff or "below" in diff.lower() or "only" in diff.lower()


def test_fingerprint_match_none_on_both_sides_counts_as_match():
    """Element legitimately lacks data_testid + aria_label on both visits.

    role matches + id matches + (testid both None counts) + (aria_label both
    None counts) = 4/4. Should heal.
    """
    stored = {
        "role": "button", "aria_label": None, "id": "go",
        "data_testid": None, "tag": "button", "text": "Go",
    }
    current = {
        "role": "button", "aria_label": None, "id": "go",
        "data_testid": None, "tag": "button", "text": "Go!",
    }
    matched, _ = fingerprint_match(stored, current)
    assert matched is True


def test_fingerprint_match_missing_fingerprint_fails():
    matched, diff = fingerprint_match(None, {"role": "button"})
    assert matched is False
    assert "missing" in diff
    matched, diff = fingerprint_match({"role": "button"}, None)
    assert matched is False


def test_fingerprint_match_text_drift_does_not_block():
    """text + tag are deliberately excluded from strong attrs.

    role + id + data_testid match (3/4) → heal; text changed completely
    is irrelevant.
    """
    stored = {
        "role": "button", "aria_label": None, "id": "x",
        "data_testid": "x", "tag": "button", "text": "Login",
    }
    current = {
        "role": "button", "aria_label": None, "id": "x",
        "data_testid": "x", "tag": "button", "text": "登入",
    }
    matched, _ = fingerprint_match(stored, current)
    assert matched is True
