"""LLM Validator: K=2 (or K=3 when DeepSeek returns) ensemble vote per step.

Discrete output (PASS / REPLAN / ABORT) makes voting easy: tally exact matches.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from shared.llm_client import vote_role

from .schema import (
    PageSnapshot,
    Step,
    StepResult,
    StepValidation,
    ValidatorDecision,
)

log = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "validator.txt"
_SYSTEM_PROMPT = _PROMPT_PATH.read_text()


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    return text


def _parse_validator(raw: str) -> ValidatorDecision:
    text = _strip_code_fence(raw)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise ValueError(f"no JSON in validator response: {raw[:200]!r}") from None
        obj = json.loads(m.group(0))
    pick = str(obj.get("decision", "")).strip().lower()
    if pick not in {"pass", "replan", "abort"}:
        raise ValueError(f"invalid decision: {pick!r}")
    return ValidatorDecision(pick)


def _build_messages(step: Step, result: StepResult, signals: list[str],
                    oracle_violations: list[str]) -> list[dict]:
    pre_summary = _snapshot_summary(result.pre)
    post_summary = _snapshot_summary(result.post)
    user_blob = json.dumps({
        "step": {
            "step_index": step.step_index,
            "action_type": step.action_type.value,
            "target_intent": step.target_intent,
            "value": step.value,
            "url": step.url,
            "extract_query": step.extract_query,
        },
        "pre": pre_summary,
        "post": post_summary,
        "selectors": {
            "tier": result.locator_tier.value if result.locator_tier else None,
            "selector": result.selector,
            "cache_hit": result.cache_hit,
        },
        "signals": signals,
        "oracle_violations": oracle_violations,
        "step_error": result.error,
    }, default=str, indent=2)
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_blob},
    ]


def _snapshot_summary(snap: PageSnapshot | None) -> dict | None:
    if snap is None:
        return None
    return {
        "url": snap.url,
        "title": snap.title,
        "dom_hash": snap.dom_hash,
        "text_excerpt": snap.text_excerpt[:1500],
        # We deliberately drop ariaSnapshot here — it's huge and the diff
        # has already been collected into `signals`.
    }


async def validate_step(step: Step, result: StepResult, signals: list[str],
                        oracle_violations: list[str]) -> StepValidation:
    """K=N vote on the step outcome. Falls back to PASS on parse failure to
    avoid blocking forward progress; the trajectory log captures the raw vote
    record for audit.
    """
    messages = _build_messages(step, result, signals, oracle_violations)
    vote = await vote_role(
        "validator",
        messages=messages,
        parser=_parse_validator,
        fallback=ValidatorDecision.PASS,
        max_tokens=512,
        temperature=0.1,
        timeout=60.0,
    )
    reason = f"vote pick={vote.pick.value} confidence={vote.confidence:.2f}"
    if vote.fallback_used:
        reason += " (fallback used — parsers failed or tied)"
    return StepValidation(
        decision=vote.pick,
        reason=reason,
        silent_failure_signals=signals,
        confidence=vote.confidence,
    )
