"""Tests for the retry_async decorator."""

from __future__ import annotations

import asyncio
import time

import pytest

from shared.retry import (
    is_rate_limit_error,
    is_transient_http_error,
    retry_async,
)

# ── Predicates ───────────────────────────────────────────────────────────────

def test_rate_limit_predicate_string_match():
    assert is_rate_limit_error(RuntimeError("Error code: 429 - Too Many Requests"))
    assert is_rate_limit_error(Exception("HTTP 429"))


def test_rate_limit_predicate_status_attr():
    class FakeApiError(Exception):
        def __init__(self):
            super().__init__("rate limited")
            self.status_code = 429
    assert is_rate_limit_error(FakeApiError())


def test_rate_limit_predicate_no_match():
    assert not is_rate_limit_error(RuntimeError("Error 400 bad request"))
    assert not is_rate_limit_error(ValueError("nothing to do with HTTP"))


def test_transient_http_includes_429():
    assert is_transient_http_error(RuntimeError("HTTP 429"))


def test_transient_http_includes_5xx():
    for code in ("502", "503", "504"):
        assert is_transient_http_error(RuntimeError(f"Error code: {code} bad gateway"))


def test_transient_http_includes_timeout():
    assert is_transient_http_error(RuntimeError("connection timeout"))
    assert is_transient_http_error(TimeoutError("operation timed out"))


def test_transient_http_excludes_4xx_other():
    """HTTP 400/401/403 are caller errors, not transient — don't retry."""
    assert not is_transient_http_error(RuntimeError("Error code: 400 bad request"))
    assert not is_transient_http_error(RuntimeError("Error code: 401 unauthorized"))


def test_transient_http_excludes_substring_match():
    """A message containing '5028' is NOT a 502 — predicate must be specific."""
    assert not is_transient_http_error(RuntimeError("Error 5028 not a real code"))


# ── Decorator behavior ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_succeeds_first_call_no_retry():
    calls = []

    @retry_async(max_attempts=3, base_delay=0.01, jitter=0)
    async def succeed():
        calls.append(1)
        return "ok"

    assert await succeed() == "ok"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_retries_on_transient_error_then_succeeds():
    calls = []

    @retry_async(max_attempts=3, base_delay=0.01, jitter=0)
    async def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("Error code: 429 transient")
        return "ok"

    assert await flaky() == "ok"
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_gives_up_after_max_attempts():
    calls = []

    @retry_async(max_attempts=3, base_delay=0.01, jitter=0)
    async def always_fail():
        calls.append(1)
        raise RuntimeError("HTTP 429 rate limit")

    with pytest.raises(RuntimeError, match="429"):
        await always_fail()
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_does_not_retry_non_transient():
    calls = []

    @retry_async(max_attempts=5, base_delay=0.01, jitter=0)
    async def auth_fail():
        calls.append(1)
        raise RuntimeError("Error code: 401 unauthorized")

    with pytest.raises(RuntimeError, match="401"):
        await auth_fail()
    assert len(calls) == 1  # no retry on 401


@pytest.mark.asyncio
async def test_custom_predicate():
    """Caller can opt to retry on ValueError (e.g. parser surfaced as exception)."""
    calls = []

    @retry_async(max_attempts=3, base_delay=0.01, jitter=0,
                 predicate=lambda e: isinstance(e, ValueError))
    async def returns_value_error():
        calls.append(1)
        if len(calls) < 2:
            raise ValueError("bad shape")
        return 42

    assert await returns_value_error() == 42
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_max_attempts_one_disables_retry():
    calls = []

    @retry_async(max_attempts=1, base_delay=0.01, jitter=0)
    async def fail_once():
        calls.append(1)
        raise RuntimeError("HTTP 429")

    with pytest.raises(RuntimeError):
        await fail_once()
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_exponential_backoff_doubles():
    """Successive retries should sleep base, 2*base, 4*base. Hard to time precisely
    but we can assert total elapsed exceeds the geometric sum."""
    calls = []

    @retry_async(max_attempts=4, base_delay=0.05, jitter=0)
    async def always_429():
        calls.append(time.perf_counter())
        raise RuntimeError("HTTP 429")

    t0 = time.perf_counter()
    with pytest.raises(RuntimeError):
        await always_429()
    elapsed = time.perf_counter() - t0
    # 3 sleeps: 0.05, 0.10, 0.20 → total ≈ 0.35s. Allow generous slack.
    assert elapsed > 0.30, f"expected ≥ 0.30s with exponential backoff, got {elapsed:.3f}s"
    assert elapsed < 1.0  # but not absurdly long


@pytest.mark.asyncio
async def test_max_delay_caps_backoff():
    """With base=0.5 and max_attempts=4, computed delays would be 0.5, 1, 2.
    With max_delay=0.6, all delays are capped to 0.6."""
    calls = []

    @retry_async(max_attempts=4, base_delay=0.5, max_delay=0.05, jitter=0)
    async def always_429():
        calls.append(time.perf_counter())
        raise RuntimeError("HTTP 429")

    t0 = time.perf_counter()
    with pytest.raises(RuntimeError):
        await always_429()
    elapsed = time.perf_counter() - t0
    # Three sleeps each capped at 0.05s = ~0.15s
    assert elapsed < 0.5, f"max_delay should have capped backoff, got {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_preserves_function_signature():
    @retry_async(max_attempts=2, base_delay=0.01)
    async def takes_args(a: int, b: int = 5) -> int:
        return a + b

    assert await takes_args(1, b=2) == 3
    assert takes_args.__name__ == "takes_args"


@pytest.mark.asyncio
async def test_invalid_max_attempts_raises():
    with pytest.raises(ValueError):
        retry_async(max_attempts=0)


@pytest.mark.asyncio
async def test_jitter_within_bounds():
    """With jitter=0.5, the actual sleep should be in [0.5*delay, 1.5*delay]."""
    sleeps = []

    real_sleep = asyncio.sleep

    async def fake_sleep(t):
        sleeps.append(t)
        await real_sleep(0)  # don't actually sleep

    # Patch asyncio.sleep that retry_async uses
    import shared.retry as retry_mod
    orig = retry_mod.asyncio.sleep
    retry_mod.asyncio.sleep = fake_sleep
    try:
        @retry_async(max_attempts=3, base_delay=1.0, jitter=0.5)
        async def always_429():
            raise RuntimeError("HTTP 429")

        with pytest.raises(RuntimeError):
            await always_429()
    finally:
        retry_mod.asyncio.sleep = orig

    # Two retries → two sleeps. base * 2^0 = 1.0, base * 2^1 = 2.0
    assert len(sleeps) == 2
    assert 0.5 <= sleeps[0] <= 1.5, f"first sleep out of jitter bounds: {sleeps[0]}"
    assert 1.0 <= sleeps[1] <= 3.0, f"second sleep out of jitter bounds: {sleeps[1]}"
