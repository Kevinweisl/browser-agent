"""LLM Planner: decompose NL task into a list of `Step`s.

Single-call (K=1) since the output is free-form structured text where voting
doesn't apply (would require structural alignment across votes — not worth
the cost). Reasoning models (`THINKING_PLANNER=on`) help with long tasks.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from shared.llm_client import call_role

from .schema import Step

log = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "planner.txt"


def _system_prompt(max_steps: int) -> str:
    return _PROMPT_PATH.read_text().replace("{max_steps}", str(max_steps))


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    return text


def _extract_json_object(text: str) -> dict:
    text = _strip_code_fence(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise ValueError(f"no JSON object in planner output: {text[:200]!r}") from None
        return json.loads(m.group(0))


async def plan_task(nl_task: str, *, max_steps: int = 25,
                    starting_url: str | None = None) -> tuple[list[Step], dict]:
    """Returns (steps, negative_oracle_dict). `negative_oracle_dict` may be empty."""
    user_msg = nl_task
    if starting_url:
        user_msg = f"Starting URL: {starting_url}\n\nTask: {nl_task}"
    messages = [
        {"role": "system", "content": _system_prompt(max_steps)},
        {"role": "user", "content": user_msg},
    ]
    raw = await call_role("planner", messages=messages,
                          max_tokens=3000, temperature=0.2, timeout=120.0)
    obj = _extract_json_object(raw)
    raw_steps = obj.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError(f"planner returned no steps: {obj!r}")
    steps = [Step(**s) for s in raw_steps]
    oracle = obj.get("negative_oracle", {}) or {}
    return steps, oracle


async def replan(nl_task: str, history: list[dict], reason: str, *,
                 max_steps: int = 25) -> list[Step]:
    """Re-plan from scratch given the failure context.

    `history` is a JSON-serializable summary of what was tried + why it
    failed. The Planner sees only the structured summary, not full DOMs, to
    keep the context tight.
    """
    history_blob = json.dumps(history[-10:], default=str, indent=2)  # cap tail
    messages = [
        {"role": "system", "content": _system_prompt(max_steps)},
        {"role": "user", "content": (
            f"Original task: {nl_task}\n\n"
            f"Replan reason: {reason}\n\n"
            f"Last steps tried (most-recent last):\n{history_blob}\n\n"
            "Output a NEW plan starting from the current page state. "
            "Avoid the failed approaches. Same JSON schema as before."
        )},
    ]
    raw = await call_role("planner", messages=messages,
                          max_tokens=3000, temperature=0.4, timeout=120.0)
    obj = _extract_json_object(raw)
    raw_steps = obj.get("steps", [])
    if not raw_steps:
        raise ValueError(f"replanner returned no steps: {obj!r}")
    return [Step(**s) for s in raw_steps]
