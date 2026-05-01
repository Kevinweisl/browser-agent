"""Reusable retry decorator for async functions.

Used to wrap remote-API calls with exponential backoff on transient failures
(rate limits, gateway errors, timeouts). Decorator form so individual call
sites only declare WHAT they want retried — not the bookkeeping.

    from shared.retry import retry_async, is_transient_http_error

    @retry_async(max_attempts=3, base_delay=1.0)
    async def fetch_json(url: str) -> dict:
        async with httpx.AsyncClient() as c:
            r = await c.get(url)
            r.raise_for_status()
            return r.json()

    # Custom predicate — only retry on the rate-limit case
    @retry_async(max_attempts=5, predicate=is_rate_limit_error)
    async def call_llm(...):
        ...

The decorator is async-only by design — synchronous retries would block the
event loop. If you need a sync version, write one alongside; don't make this
do both via runtime checks.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


# ── Common predicates ─────────────────────────────────────────────────────────

def is_rate_limit_error(exc: BaseException) -> bool:
    """Match HTTP 429 / "Too Many Requests" errors.

    Works across OpenAI client, httpx, and bare requests-style exceptions
    by string-matching the message — these all surface 429s differently
    (status_code attr, body text, exception class) but consistently include
    one of these markers.
    """
    msg = str(exc)
    if "429" in msg or "Too Many Requests" in msg:
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    return status == 429


def is_transient_http_error(exc: BaseException) -> bool:
    """Match common transient HTTP failures: 429, 502, 503, 504, plus timeouts.

    Use as the default for remote-API retry wrappers.
    """
    if is_rate_limit_error(exc):
        return True
    msg = str(exc)
    for code in ("502", "503", "504"):
        # be specific — don't match "5028" or random other numerics
        if (f"{code} " in msg) or (f"code: {code}" in msg) or (f"status_code={code}" in msg):
            return True
    if "timeout" in msg.lower() or "timed out" in msg.lower():
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    return status in (429, 502, 503, 504)


# ── The decorator ─────────────────────────────────────────────────────────────

def retry_async(
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: float = 0.25,
    predicate: Callable[[BaseException], bool] = is_transient_http_error,
    logger: logging.Logger | None = None,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorator: retry an async function with exponential backoff + jitter.

    Args:
      max_attempts:  total attempts including the first call. max_attempts=1
                     disables retry. Must be ≥ 1.
      base_delay:    seconds before the first retry. Doubled on each subsequent
                     retry: base, 2*base, 4*base, ...
      max_delay:     ceiling on a single retry's delay (after exponential growth).
      jitter:        fraction of the computed delay to randomize ± by. 0.25 means
                     "actual delay between 75% and 125% of the computed delay".
                     Set to 0.0 to disable.
      predicate:     function(exc) → bool. Return True to mean "this exception
                     is retriable". Default matches common transient HTTP
                     failures (429 + 5xx + timeouts).
      logger:        logger to write retry-progress lines to. Defaults to this
                     module's logger.

    Behavior:
      - On a non-retriable exception → re-raised immediately, no backoff.
      - On a retriable exception with attempts remaining → log and sleep, retry.
      - On a retriable exception with no attempts remaining → re-raised.
      - On success → return value, no further retries.

    The decorated function's signature and return type are preserved.
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be ≥ 1, got {max_attempts}")
    if base_delay < 0:
        raise ValueError(f"base_delay must be ≥ 0, got {base_delay}")

    log_ = logger or log

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            last_exc: BaseException | None = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except BaseException as exc:
                    last_exc = exc
                    if not predicate(exc):
                        raise
                    if attempt == max_attempts - 1:
                        # Out of attempts — propagate the last error
                        raise
                    raw_delay = min(base_delay * (2 ** attempt), max_delay)
                    if jitter > 0:
                        # uniform jitter ± jitter * raw_delay
                        delta = raw_delay * jitter * (2 * random.random() - 1)
                        delay = max(0.0, raw_delay + delta)
                    else:
                        delay = raw_delay
                    log_.info(
                        "retry_async(%s) attempt %d/%d failed (%s); sleeping %.2fs",
                        func.__name__, attempt + 1, max_attempts,
                        type(exc).__name__, delay,
                    )
                    await asyncio.sleep(delay)
            # Unreachable in practice — the loop always either returns or raises
            raise last_exc if last_exc else RuntimeError("retry_async exhausted")

        return wrapper

    return decorator
