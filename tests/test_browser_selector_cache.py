"""Unit tests for the selector_cache helper functions (URL templating + hashing).

Postgres roundtrip is exercised in the live smoke test, not in unit tests
(would require a running DB and pollute the cache during eval runs).
"""

from __future__ import annotations

from workers.browser.selector_cache import (
    dom_hash_string,
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
