"""LLM client + ensemble voting wrapper.

Three model families, all OpenAI-compatible (NVIDIA NIM hosts all three):
  - DeepSeek V4 Pro
  - NVIDIA Nemotron 3 Super 120B
  - Mistral Medium 3.5 128B

These have meaningfully different training distributions, so K=3 ensemble
voting on discrete-classification roles produces uncorrelated errors —
which is what makes voting actually work, vs. running the same model 3x.

Per-role configuration via env vars (see .env.example):
  - VALIDATOR_MODELS=deepseek,nemotron,mistral  (comma-list → vote, single → K=1)
  - THINKING_VALIDATOR=on/off

Usage:
    from shared.llm_client import call_role, vote_role

    # K=1 single-call form (e.g. PLANNER which is free-form text)
    text = await call_role("planner", messages=[...])

    # K=N voting form, returns the majority pick + confidence + raw votes
    pick, confidence, votes = await vote_role("validator", messages=[...],
                                               parser=lambda t: parse_my_json(t))
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from shared.retry import is_transient_http_error, retry_async

log = logging.getLogger(__name__)


# ── Provider registry ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Provider:
    """One LLM endpoint. Multiple providers can share base_url + api_key
    (different models from the same NIM account)."""

    name: str          # "deepseek" / "nemotron" / "mistral"
    model: str         # e.g. "deepseek-ai/deepseek-v4-pro"
    base_url: str
    api_key: str
    # Provider-specific quirks: how does this provider toggle thinking mode?
    # We support 3 known forms (used as a hint when role thinking=on):
    #   - "deepseek_chat_template": extra_body={"chat_template_kwargs":{"thinking":<bool>}}
    #   - "nemotron_enable_thinking": extra_body={"chat_template_kwargs":{"enable_thinking":<bool>}, "reasoning_budget":16384}
    #   - "openai_reasoning_effort":  extra_body={"reasoning_effort": "high"|"low"}
    thinking_style: str = "none"


def _provider_from_env(name: str, thinking_style: str) -> Provider | None:
    """Build a Provider from NIM_{NAME}_* env vars. Returns None if any required
    var is missing (so the caller can skip silently in offline test mode)."""
    upper = name.upper()
    model = os.environ.get(f"NIM_{upper}_MODEL")
    api_key = os.environ.get(f"NIM_{upper}_API_KEY")
    base_url = os.environ.get(f"NIM_{upper}_BASE_URL", "https://integrate.api.nvidia.com/v1")
    if not model or not api_key:
        return None
    return Provider(name=name, model=model, base_url=base_url, api_key=api_key,
                    thinking_style=thinking_style)


def _registry() -> dict[str, Provider]:
    """Build the provider registry from environment. Cached per-process."""
    out: dict[str, Provider] = {}
    for name, style in [
        ("deepseek", "deepseek_chat_template"),
        ("nemotron", "nemotron_enable_thinking"),
        ("mistral",  "openai_reasoning_effort"),
        ("qwen",     "enable_thinking_simple"),
        ("gemma",    "enable_thinking_simple"),
    ]:
        p = _provider_from_env(name, style)
        if p:
            out[name] = p
    return out


# ── Single-call form ─────────────────────────────────────────────────────────

# Default reasoning budget for Nemotron when thinking=on. Status classification
# rarely needs more than 1-2K reasoning tokens; 16K was overkill and pushed
# latency past 3 minutes per call. Override via NEMOTRON_REASONING_BUDGET env.
_NEMOTRON_BUDGET_ON = int(os.environ.get("NEMOTRON_REASONING_BUDGET", "2048"))


def _build_thinking_extra_body(style: str, on: bool) -> dict | None:
    """Translate role-level thinking flag to per-provider extra_body kwargs."""
    if style == "deepseek_chat_template":
        return {"chat_template_kwargs": {"thinking": bool(on)}}
    if style == "nemotron_enable_thinking":
        return {
            "chat_template_kwargs": {"enable_thinking": bool(on)},
            "reasoning_budget": _NEMOTRON_BUDGET_ON if on else 0,
        }
    if style == "enable_thinking_simple":
        # Qwen / Gemma — chat_template_kwargs.enable_thinking only, no budget knob.
        return {"chat_template_kwargs": {"enable_thinking": bool(on)}}
    if style == "openai_reasoning_effort":
        # Mistral uses top-level reasoning_effort, not extra_body; handled by caller
        return None
    return None


# Per-provider concurrency cap — NVIDIA NIM trial accounts rate-limit aggressively.
# Each provider gets its own semaphore so a slow provider doesn't starve others.
# Override via NIM_MAX_CONCURRENT env var.
_MAX_CONCURRENT = int(os.environ.get("NIM_MAX_CONCURRENT", "3"))
# Per-event-loop semaphore registry. asyncio.Semaphore is bound to the loop
# that created it; a process that calls asyncio.run() multiple times (e.g.
# the SEC eval runner extracts each filing in its own asyncio.run) needs a
# fresh semaphore per loop or `acquire()` raises
# "Semaphore is bound to a different event loop".
#
# Using WeakKeyDictionary keyed on the loop object itself (not its id):
# when the loop is garbage-collected, the entry vanishes — no risk of an
# id() being recycled and the cache returning a semaphore bound to a dead
# loop. Recommended pattern in 2026-05-01-failure-mode-fixes.md §4.
import weakref  # noqa: E402  -- intentional placement: see comment block above

_provider_locks: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[str, asyncio.Semaphore]
] = weakref.WeakKeyDictionary()


def _provider_lock(name: str) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    per_loop = _provider_locks.get(loop)
    if per_loop is None:
        per_loop = {}
        _provider_locks[loop] = per_loop
    if name not in per_loop:
        per_loop[name] = asyncio.Semaphore(_MAX_CONCURRENT)
    return per_loop[name]


async def _call_one(
    provider: Provider,
    *,
    messages: list[dict],
    thinking: bool,
    max_tokens: int,
    temperature: float,
    timeout: float,
) -> str:
    """Make one chat-completion call to a provider. Returns assistant text content."""
    extra: dict[str, Any] = {}
    eb = _build_thinking_extra_body(provider.thinking_style, thinking)
    if eb is not None:
        extra["extra_body"] = eb
    if provider.thinking_style == "openai_reasoning_effort":
        # Mistral on NIM accepts only 'none' or 'high' for reasoning_effort
        # ('low' is rejected with HTTP 400). 'none' = no reasoning, fastest.
        extra["extra_body"] = {"reasoning_effort": "high" if thinking else "none"}

    async with _provider_lock(provider.name):
        client = AsyncOpenAI(
            base_url=provider.base_url,
            api_key=provider.api_key,
            timeout=timeout,
        )
        return await _create_with_retry(
            client, provider.model, messages, max_tokens, temperature, extra,
        )


# Wrapped chat-completion call with transient-HTTP retry. Lives outside _call_one
# so the decorator instance is created once at import time, not per-call. Retry
# attempts honor the semaphore from the caller — held throughout the backoff.
@retry_async(
    max_attempts=int(os.environ.get("NIM_MAX_RETRY_ATTEMPTS", "3")),
    base_delay=float(os.environ.get("NIM_RETRY_BASE_DELAY", "1.0")),
    max_delay=10.0,
    predicate=is_transient_http_error,
    logger=log,
)
async def _create_with_retry(
    client: AsyncOpenAI,
    model: str,
    messages: list[dict],
    max_tokens: int,
    temperature: float,
    extra: dict[str, Any],
) -> str:
    r = await client.chat.completions.create(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        max_tokens=max_tokens,
        temperature=temperature,
        **extra,
    )
    return (r.choices[0].message.content or "").strip()


def _role_providers(role: str) -> list[Provider]:
    """Resolve a role to its list of providers via {ROLE}_MODELS env var."""
    env_var = f"{role.upper()}_MODELS"
    spec = os.environ.get(env_var, "").strip()
    if not spec:
        # Fallback to deepseek as default if registry has it
        registry = _registry()
        return [registry["deepseek"]] if "deepseek" in registry else []
    names = [n.strip() for n in spec.split(",") if n.strip()]
    registry = _registry()
    return [registry[n] for n in names if n in registry]


def _role_thinking(role: str) -> bool:
    return os.environ.get(f"THINKING_{role.upper()}", "off").lower() == "on"


async def call_role(
    role: str,
    *,
    messages: list[dict],
    max_tokens: int = 4096,
    temperature: float = 0.3,
    timeout: float = 60.0,
) -> str:
    """Call the FIRST provider configured for `role`. Single-call form for
    roles where ensemble voting doesn't apply (Planner free-form text)."""
    providers = _role_providers(role)
    if not providers:
        raise RuntimeError(f"No providers configured for role={role!r}. "
                           f"Set {role.upper()}_MODELS in .env")
    return await _call_one(
        providers[0],
        messages=messages,
        thinking=_role_thinking(role),
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
    )


# ── Voting form ──────────────────────────────────────────────────────────────

@dataclass
class VoteResult:
    pick: Any                              # majority answer (post-parser)
    confidence: float                      # fraction of voters who picked `pick`
    votes: list[tuple[str, Any, str]]      # (provider_name, parsed_value, raw_text)
    fallback_used: bool = False            # True if all parsers failed and we fell back


async def vote_role(
    role: str,
    *,
    messages: list[dict],
    parser: Callable[[str], Any],
    fallback: Any = None,
    max_tokens: int = 4096,
    temperature: float = 0.3,
    timeout: float = 60.0,
) -> VoteResult:
    """K-model parallel vote for `role`. Returns the majority parsed value.

    `parser` extracts the discrete decision from each model's raw text (e.g.
    parse_status_json -> Status). Parser MUST be tolerant of malformed output
    and may raise — those votes are discarded.

    Tie-breaks (1-1-1 with 3 models, etc.) defer to `fallback` if provided.
    If no parser succeeds for ANY voter, returns the fallback with
    `fallback_used=True` and confidence=0.
    """
    providers = _role_providers(role)
    if not providers:
        raise RuntimeError(f"No providers configured for role={role!r}")

    thinking = _role_thinking(role)
    coros: list[Awaitable[str]] = [
        _call_one(p, messages=messages, thinking=thinking,
                  max_tokens=max_tokens, temperature=temperature, timeout=timeout)
        for p in providers
    ]
    raw_results = await asyncio.gather(*coros, return_exceptions=True)

    parsed_votes: list[tuple[str, Any, str]] = []
    for provider, raw in zip(providers, raw_results, strict=True):
        if isinstance(raw, BaseException):
            log.warning("vote_role(%s) provider %s call failed: %s",
                        role, provider.name, raw)
            continue
        try:
            value = parser(raw)
        except Exception as exc:  # noqa: BLE001
            log.warning("vote_role(%s) provider %s parse failed: %s. raw=%r",
                        role, provider.name, exc, raw[:200])
            continue
        parsed_votes.append((provider.name, value, raw))

    if not parsed_votes:
        return VoteResult(
            pick=fallback, confidence=0.0, votes=[], fallback_used=True,
        )

    # Tally — Counter on the parsed values. Values must be hashable; if a
    # caller's parser produces dicts, they should make-key in the parser.
    tally = Counter(v for _, v, _ in parsed_votes)
    most_common = tally.most_common()
    top_value, top_count = most_common[0]
    n = len(parsed_votes)

    # Tie detection: top-count tied with second-most? Use fallback.
    if len(most_common) > 1 and most_common[1][1] == top_count:
        if fallback is not None:
            return VoteResult(
                pick=fallback,
                confidence=0.0,
                votes=parsed_votes,
                fallback_used=True,
            )
        # No fallback — first parsed vote wins as a deterministic tiebreak
        top_value = parsed_votes[0][1]
        top_count = sum(1 for _, v, _ in parsed_votes if v == top_value)

    return VoteResult(
        pick=top_value,
        confidence=top_count / n,
        votes=parsed_votes,
        fallback_used=False,
    )
