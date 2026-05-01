"""Tests for the LLM client and voting wrapper.

Mocks the OpenAI client so we don't hit live endpoints in unit tests.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import patch

import pytest

from shared.llm_client import (
    Provider,
    VoteResult,
    _build_thinking_extra_body,
    _role_providers,
    _role_thinking,
    call_role,
    vote_role,
)


@pytest.fixture
def env_three_providers(monkeypatch):
    """Configure all three NIM providers."""
    monkeypatch.setenv("NIM_DEEPSEEK_BASE_URL", "https://nim.example/v1")
    monkeypatch.setenv("NIM_DEEPSEEK_MODEL", "deepseek-ai/test")
    monkeypatch.setenv("NIM_DEEPSEEK_API_KEY", "key-d")
    monkeypatch.setenv("NIM_NEMOTRON_BASE_URL", "https://nim.example/v1")
    monkeypatch.setenv("NIM_NEMOTRON_MODEL", "nvidia/test")
    monkeypatch.setenv("NIM_NEMOTRON_API_KEY", "key-n")
    monkeypatch.setenv("NIM_MISTRAL_BASE_URL", "https://nim.example/v1")
    monkeypatch.setenv("NIM_MISTRAL_MODEL", "mistralai/test")
    monkeypatch.setenv("NIM_MISTRAL_API_KEY", "key-m")


def test_thinking_extra_body_deepseek():
    eb = _build_thinking_extra_body("deepseek_chat_template", on=True)
    assert eb == {"chat_template_kwargs": {"thinking": True}}


def test_thinking_extra_body_nemotron():
    eb = _build_thinking_extra_body("nemotron_enable_thinking", on=True)
    assert eb["chat_template_kwargs"]["enable_thinking"] is True
    # Default is 2048; configurable via NEMOTRON_REASONING_BUDGET
    assert eb["reasoning_budget"] >= 1024


def test_thinking_extra_body_nemotron_off_zero_budget():
    eb = _build_thinking_extra_body("nemotron_enable_thinking", on=False)
    assert eb["reasoning_budget"] == 0


def test_role_providers_three_voters(env_three_providers, monkeypatch):
    monkeypatch.setenv("VALIDATOR_MODELS", "deepseek,nemotron,mistral")
    providers = _role_providers("validator")
    assert [p.name for p in providers] == ["deepseek", "nemotron", "mistral"]


def test_role_providers_single(env_three_providers, monkeypatch):
    monkeypatch.setenv("PLANNER_MODELS", "deepseek")
    providers = _role_providers("planner")
    assert [p.name for p in providers] == ["deepseek"]


def test_role_providers_skips_unconfigured(env_three_providers, monkeypatch):
    """Models listed but not in env shouldn't crash — just skipped."""
    monkeypatch.setenv("VALIDATOR_MODELS", "deepseek,does-not-exist,mistral")
    providers = _role_providers("validator")
    assert [p.name for p in providers] == ["deepseek", "mistral"]


def test_role_thinking(monkeypatch):
    monkeypatch.setenv("THINKING_VALIDATOR", "on")
    monkeypatch.setenv("THINKING_TRIGGER_EVAL", "off")
    assert _role_thinking("validator") is True
    assert _role_thinking("trigger_eval") is False
    assert _role_thinking("not_set") is False


# ── Voting tests with mocked _call_one ────────────────────────────────────────

class _FakeCallOne:
    """Returns canned responses keyed by provider name. Use to mock _call_one."""

    def __init__(self, responses: dict[str, str | BaseException]):
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, provider, *, messages, **kwargs):
        self.calls.append((provider.name, kwargs))
        r = self.responses.get(provider.name)
        if isinstance(r, BaseException):
            raise r
        if r is None:
            raise RuntimeError(f"No mock response for {provider.name}")
        return r


def _parse_status(text: str) -> str:
    """Parse a one-word status from text. Raises on malformed input."""
    cleaned = text.strip().lower()
    if cleaned in {"extracted", "incorporated_by_reference", "not_applicable", "reserved", "partial"}:
        return cleaned
    raise ValueError(f"Unrecognized: {cleaned!r}")


@pytest.mark.asyncio
async def test_vote_majority_wins(env_three_providers, monkeypatch):
    monkeypatch.setenv("VALIDATOR_MODELS", "deepseek,nemotron,mistral")
    fake = _FakeCallOne({
        "deepseek": "extracted",
        "nemotron": "extracted",
        "mistral": "incorporated_by_reference",
    })
    with patch("shared.llm_client._call_one", fake):
        r = await vote_role("validator", messages=[{"role": "user", "content": "x"}],
                            parser=_parse_status)
    assert r.pick == "extracted"
    assert r.confidence == pytest.approx(2 / 3)
    assert len(r.votes) == 3
    assert not r.fallback_used


@pytest.mark.asyncio
async def test_vote_unanimous(env_three_providers, monkeypatch):
    monkeypatch.setenv("VALIDATOR_MODELS", "deepseek,nemotron,mistral")
    fake = _FakeCallOne({n: "not_applicable" for n in ["deepseek", "nemotron", "mistral"]})
    with patch("shared.llm_client._call_one", fake):
        r = await vote_role("validator", messages=[{"role": "user", "content": "x"}],
                            parser=_parse_status)
    assert r.pick == "not_applicable"
    assert r.confidence == 1.0


@pytest.mark.asyncio
async def test_vote_three_way_tie_uses_fallback(env_three_providers, monkeypatch):
    monkeypatch.setenv("VALIDATOR_MODELS", "deepseek,nemotron,mistral")
    fake = _FakeCallOne({
        "deepseek": "extracted",
        "nemotron": "incorporated_by_reference",
        "mistral": "not_applicable",
    })
    with patch("shared.llm_client._call_one", fake):
        r = await vote_role("validator", messages=[{"role": "user", "content": "x"}],
                            parser=_parse_status, fallback="extracted")
    assert r.pick == "extracted"
    assert r.fallback_used is True
    assert r.confidence == 0.0


@pytest.mark.asyncio
async def test_vote_one_voter_fails_others_succeed(env_three_providers, monkeypatch):
    """If one provider crashes, voting proceeds with the survivors."""
    monkeypatch.setenv("VALIDATOR_MODELS", "deepseek,nemotron,mistral")
    fake = _FakeCallOne({
        "deepseek": "extracted",
        "nemotron": RuntimeError("simulated 503"),
        "mistral": "extracted",
    })
    with patch("shared.llm_client._call_one", fake):
        r = await vote_role("validator", messages=[{"role": "user", "content": "x"}],
                            parser=_parse_status)
    assert r.pick == "extracted"
    assert r.confidence == 1.0  # 2 of 2 survivors agree
    assert len(r.votes) == 2


@pytest.mark.asyncio
async def test_vote_all_voters_fail_returns_fallback(env_three_providers, monkeypatch):
    monkeypatch.setenv("VALIDATOR_MODELS", "deepseek,nemotron,mistral")
    fake = _FakeCallOne({
        n: RuntimeError("simulated outage") for n in ["deepseek", "nemotron", "mistral"]
    })
    with patch("shared.llm_client._call_one", fake):
        r = await vote_role("validator", messages=[{"role": "user", "content": "x"}],
                            parser=_parse_status, fallback="extracted")
    assert r.pick == "extracted"
    assert r.confidence == 0.0
    assert r.fallback_used is True
    assert r.votes == []


@pytest.mark.asyncio
async def test_vote_parser_failures_count_as_no_vote(env_three_providers, monkeypatch):
    """Models that produce malformed output don't get to vote, but the
    survivors still drive the decision."""
    monkeypatch.setenv("VALIDATOR_MODELS", "deepseek,nemotron,mistral")
    fake = _FakeCallOne({
        "deepseek": "extracted",
        "nemotron": "this is some garbage text",  # parser raises
        "mistral": "extracted",
    })
    with patch("shared.llm_client._call_one", fake):
        r = await vote_role("validator", messages=[{"role": "user", "content": "x"}],
                            parser=_parse_status, fallback="not_applicable")
    assert r.pick == "extracted"
    assert r.confidence == 1.0
    assert len(r.votes) == 2


@pytest.mark.asyncio
async def test_call_role_uses_first_provider(env_three_providers, monkeypatch):
    monkeypatch.setenv("PLANNER_MODELS", "nemotron,mistral")
    fake = _FakeCallOne({"nemotron": "step 1: do X", "mistral": "should not be called"})
    with patch("shared.llm_client._call_one", fake):
        text = await call_role("planner", messages=[{"role": "user", "content": "task"}])
    assert text == "step 1: do X"
    assert len(fake.calls) == 1
    assert fake.calls[0][0] == "nemotron"


@pytest.mark.asyncio
async def test_vote_runs_in_parallel(env_three_providers, monkeypatch):
    """All three calls dispatched together — total time ≈ slowest call,
    not sum of all three."""
    monkeypatch.setenv("VALIDATOR_MODELS", "deepseek,nemotron,mistral")

    async def slow_call(provider, **kwargs):
        await asyncio.sleep(0.1)
        return "extracted"

    with patch("shared.llm_client._call_one", slow_call):
        import time
        t0 = time.perf_counter()
        await vote_role("validator", messages=[{"role": "user", "content": "x"}],
                        parser=_parse_status)
        elapsed = time.perf_counter() - t0
    # Three sequential 0.1s calls would take ~0.3s; parallel should be ~0.1s
    # Allow generous slack on a busy machine.
    assert elapsed < 0.25, f"voting wasn't parallel: took {elapsed:.3f}s"
