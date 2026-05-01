"""Postgres-backed selector cache.

Cross-session self-maintenance: the same `(page_url_template, intent)` pair
re-uses a previously-resolved selector when its DOM fingerprint still
matches. On mismatch, the entry is healed (Healenium-style) using the stored
`aria_fingerprint` rather than thrown away wholesale.

Key insight from the browser-agent research delta (§4): caching a *bare*
selector is brittle. Caching `(selector, aria_fingerprint, dom_hash)` together
gives a healer something to anchor to when the DOM drifts.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from shared.db import get_pool

# ── Key normalization ────────────────────────────────────────────────────────

_RE_TRAILING_NUMERIC = re.compile(r"/\d+(?=/|$)")
_RE_TRAILING_HEX = re.compile(r"/[0-9a-f]{8,}(?=/|$)", re.IGNORECASE)


def url_to_template(url: str) -> str:
    """Reduce a concrete URL to a template that varies less than the URL.

    `https://example.com/users/12345/orders/abc-789` →
    `https://example.com/users/{}/orders/{}`.

    The point is to share cache entries across instance ids in URL paths.
    Query strings and fragments are dropped.
    """
    base = url.split("?", 1)[0].split("#", 1)[0]
    out = _RE_TRAILING_NUMERIC.sub("/{}", base)
    out = _RE_TRAILING_HEX.sub("/{}", out)
    return out


def intent_hash(intent: str) -> str:
    """Stable short hash of intent text for cache lookup."""
    return hashlib.sha256(intent.strip().lower().encode()).hexdigest()[:16]


# ── Cache record ─────────────────────────────────────────────────────────────

@dataclass
class CacheRecord:
    page_url_template: str
    action_intent: str
    selector_strategy: str
    selector: str
    dom_hash: str
    aria_fingerprint: dict[str, Any] | None = None
    last_healed_at: Any = None
    healing_diff: str | None = None
    hit_count: int = 0


async def lookup(page_url: str, intent: str) -> CacheRecord | None:
    """Return the cached entry for (template, intent) if any."""
    template = url_to_template(page_url)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT page_url_template, action_intent, selector_strategy, selector,
                   dom_hash, aria_fingerprint, last_healed_at, healing_diff, hit_count
            FROM selector_cache
            WHERE page_url_template = $1 AND action_intent = $2
            """,
            template, intent,
        )
    if row is None:
        return None
    raw_aria = row["aria_fingerprint"]
    aria_dict: dict[str, Any] | None
    if raw_aria is None:
        aria_dict = None
    elif isinstance(raw_aria, str):
        try:
            aria_dict = json.loads(raw_aria)
        except json.JSONDecodeError:
            aria_dict = None
    else:
        aria_dict = raw_aria
    return CacheRecord(
        page_url_template=row["page_url_template"],
        action_intent=row["action_intent"],
        selector_strategy=row["selector_strategy"],
        selector=row["selector"],
        dom_hash=row["dom_hash"],
        aria_fingerprint=aria_dict,
        last_healed_at=row["last_healed_at"],
        healing_diff=row["healing_diff"],
        hit_count=row["hit_count"],
    )


async def upsert(rec: CacheRecord) -> None:
    """Insert or update a cache entry; bump hit_count on existing rows."""
    pool = await get_pool()
    aria_json = json.dumps(rec.aria_fingerprint) if rec.aria_fingerprint else None
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO selector_cache
              (page_url_template, action_intent, selector_strategy, selector,
               dom_hash, aria_fingerprint, last_success_at, hit_count,
               last_healed_at, healing_diff)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, now(), 1, $7, $8)
            ON CONFLICT (page_url_template, action_intent) DO UPDATE SET
              selector_strategy = EXCLUDED.selector_strategy,
              selector          = EXCLUDED.selector,
              dom_hash          = EXCLUDED.dom_hash,
              aria_fingerprint  = COALESCE(EXCLUDED.aria_fingerprint, selector_cache.aria_fingerprint),
              last_success_at   = now(),
              hit_count         = selector_cache.hit_count + 1,
              last_healed_at    = COALESCE(EXCLUDED.last_healed_at, selector_cache.last_healed_at),
              healing_diff      = COALESCE(EXCLUDED.healing_diff, selector_cache.healing_diff)
            """,
            rec.page_url_template, rec.action_intent, rec.selector_strategy,
            rec.selector, rec.dom_hash, aria_json,
            rec.last_healed_at, rec.healing_diff,
        )


async def record_heal(template: str, intent: str, diff: str) -> None:
    """Stamp last_healed_at + healing_diff for a drift audit log."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE selector_cache
            SET last_healed_at = now(),
                healing_diff = $3
            WHERE page_url_template = $1 AND action_intent = $2
            """,
            template, intent, diff,
        )


def dom_hash_string(html: str) -> str:
    """Stable short fingerprint over the rendered DOM."""
    return hashlib.sha256(html.encode("utf-8", errors="ignore")).hexdigest()[:16]
