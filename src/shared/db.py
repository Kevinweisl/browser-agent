"""Postgres connection pool, shared by gateway and workers."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import asyncpg


_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        url = os.environ["DATABASE_URL"]
        _pool = await asyncpg.create_pool(url, min_size=2, max_size=10)
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
    async with pool.acquire() as conn:
        async with conn.transaction():
            yield conn
