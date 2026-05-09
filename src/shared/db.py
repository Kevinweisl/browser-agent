"""Postgres connection pool, shared by gateway and workers.

Pool creation also runs an idempotent schema bootstrap so a fresh database
(e.g., a brand-new Zeabur Postgres service) doesn't crash on the first
selector_cache lookup. The DDL matches what selector_cache.py reads/writes.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import asyncpg

_pool: asyncpg.Pool | None = None
_schema_ready = False


_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS selector_cache (
    page_url_template TEXT NOT NULL,
    action_intent     TEXT NOT NULL,
    selector_strategy TEXT NOT NULL,
    selector          TEXT NOT NULL,
    dom_hash          TEXT NOT NULL,
    aria_fingerprint  JSONB,
    last_success_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_healed_at    TIMESTAMPTZ,
    healing_diff      TEXT,
    hit_count         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (page_url_template, action_intent)
);
"""


async def _ensure_schema(pool: asyncpg.Pool) -> None:
    """Run the idempotent DDL once per process. CREATE TABLE IF NOT EXISTS
    is safe to call repeatedly; the _schema_ready flag just avoids the
    network round-trip after the first successful pool acquire."""
    global _schema_ready
    if _schema_ready:
        return
    async with pool.acquire() as conn:
        await conn.execute(_SCHEMA_DDL)
    _schema_ready = True


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        url = os.environ["DATABASE_URL"]
        _pool = await asyncpg.create_pool(url, min_size=2, max_size=10)
        await _ensure_schema(_pool)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def transaction():
    """Context manager yielding (conn) inside a transaction."""
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        yield conn
