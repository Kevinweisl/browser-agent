"""Top-level browser-task handler: Planner → Actor → Validator → replan loop.

Wires the four subsystems together for a single NL task and returns a
TaskResult with full trajectory for audit.

Entry point shape matches the worker dispatch contract: `async (input_dict) → result_dict`.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from .actor import StepActor
from .locator_ladder import LocatorResolver
from .planner import plan_task, replan
from .schema import (
    ActionType,
    PageSnapshot,
    StepResult,
    StepValidation,
    TaskInput,
    TaskResult,
    TrajectoryEvent,
    ValidatorDecision,
)
from .silent_failure import collect_signals, is_content_failed, negative_oracle_violations
from .validator import validate_step

# Action types where the cheap signals + step.success alone are sufficient —
# no LLM validator call needed. Saves ~5s × K voters per step on tasks with
# many extracts/screenshots.
_PASSIVE_ACTIONS = {ActionType.EXTRACT, ActionType.SCREENSHOT, ActionType.WAIT_FOR}

log = logging.getLogger(__name__)


# Replan limits — keep tight to prevent runaway loops on hard tasks.
_MAX_REPLANS_DEFAULT = 3


@asynccontextmanager
async def _browser_context(*, headless: bool = True):
    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.launch(headless=headless)
        ctx = await browser.new_context(
            viewport={"width": 1288, "height": 711},  # Stagehand default
            user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"),
        )
        page = await ctx.new_page()
        try:
            yield page
        finally:
            await ctx.close()
            await browser.close()
    finally:
        await pw.stop()


async def _validate_negative_oracle(post: PageSnapshot, oracle: dict) -> list[str]:
    """Apply the global per-task oracle (separate from per-step success_criteria)."""
    violations: list[str] = []
    text_lower = post.text_excerpt.lower()
    for must in oracle.get("must_appear_on_final_page", []):
        if must.lower() not in text_lower:
            violations.append(f"final-page missing must_appear: {must!r}")
    for must_not in oracle.get("must_not_appear", []):
        if must_not.lower() in text_lower:
            violations.append(f"final-page present must_not_appear: {must_not!r}")
    return violations


async def run_task(task_input: TaskInput) -> TaskResult:
    t0 = time.perf_counter()
    deadline_s = task_input.max_seconds
    trajectory: list[TrajectoryEvent] = []
    fail_reason = "completed"

    async with _browser_context(headless=True) as page:
        actor = StepActor(page, LocatorResolver())

        # ─── Plan ────────────────────────────────────────────────────────────
        try:
            steps, oracle_dict = await plan_task(
                task_input.task,
                max_steps=task_input.max_steps,
                starting_url=task_input.starting_url,
            )
        except Exception:
            log.exception("planner failed")
            return TaskResult(
                ok=False, trajectory=[], extracted_content=None,
                duration_ms=int((time.perf_counter() - t0) * 1000),
                fail_reason="planner_failure",
            )

        replans = 0
        replan_history: list[dict] = []
        steps_executed = 0
        extracted_content: Any = None
        ok = True

        # ─── Step loop with replan ──────────────────────────────────────────
        idx = 0
        while idx < len(steps):
            if (time.perf_counter() - t0) > deadline_s:
                ok = False
                fail_reason = "wall_clock_exceeded"
                break
            if steps_executed >= task_input.max_steps:
                ok = False
                fail_reason = "step_cap_exceeded"
                break

            step = steps[idx]

            # Execute
            try:
                result = await actor.execute(step)
            except Exception as exc:
                log.exception("actor execute crashed step=%s", step.step_index)
                result = StepResult(step_index=step.step_index, success=False,
                                    error=f"actor crash: {exc}")

            # Cheap signals + per-step oracle
            signals: list[str] = []
            oracle_violations: list[str] = []
            if result.pre and result.post:
                signals = collect_signals(step, result.pre, result.post)
                oracle_violations = negative_oracle_violations(step, result.post)

            # Validator dispatch:
            #  - Layer 0 page-content failure (Wikipedia 404 / "page not found"
            #    / soft-block markers): short-circuit to REPLAN regardless of
            #    action type. The page itself declares broken — no value in
            #    asking an LLM, the answer is fixed.
            #  - Passive actions (extract / screenshot / wait_for): cheap-signal
            #    only; no LLM call. Pass on result.success; per-step
            #    success_criteria is too strict for passive ops, the global
            #    negative_oracle on the final page is the right check.
            #  - Mutating actions: LLM K=N vote on top of cheap signals.
            if is_content_failed(signals):
                content_markers = [s.split(":", 1)[1] for s in signals
                                   if s.startswith("page_content_failed:")]
                validation = StepValidation(
                    decision=ValidatorDecision.REPLAN,
                    reason=("page-content failure detected; short-circuit REPLAN: "
                            + ", ".join(content_markers)),
                    silent_failure_signals=signals, confidence=1.0,
                )
            elif step.action_type in _PASSIVE_ACTIONS:
                validation = StepValidation(
                    decision=ValidatorDecision.PASS if result.success else ValidatorDecision.REPLAN,
                    reason=("passive action succeeded; validator short-circuit"
                            if result.success
                            else f"passive action error: {result.error}"),
                    silent_failure_signals=signals, confidence=1.0,
                )
            else:
                try:
                    validation = await validate_step(step, result, signals, oracle_violations)
                except Exception as exc:
                    log.exception("validator crashed step=%s", step.step_index)
                    validation = StepValidation(
                        decision=ValidatorDecision.PASS if result.success else ValidatorDecision.REPLAN,
                        reason=f"validator-crash; defaulted on result.success={result.success}: {exc}",
                        silent_failure_signals=signals, confidence=0.0,
                    )

            trajectory.append(TrajectoryEvent(step=step, result=result, validation=validation))
            steps_executed += 1

            # Capture last extract content
            if result.extracted is not None:
                extracted_content = result.extracted

            if validation.decision == ValidatorDecision.ABORT:
                ok = False
                fail_reason = "validator_aborted"
                break

            if validation.decision == ValidatorDecision.REPLAN:
                replans += 1
                if replans > _MAX_REPLANS_DEFAULT:
                    ok = False
                    fail_reason = "validator_aborted"  # treat exhausted replan budget as abort
                    break
                replan_history.append({
                    "step_index": step.step_index,
                    "intent": step.target_intent,
                    "result_error": result.error,
                    "validation_reason": validation.reason,
                    "signals": signals,
                })
                try:
                    new_steps = await replan(task_input.task, replan_history,
                                             validation.reason,
                                             max_steps=task_input.max_steps)
                except Exception:
                    log.exception("replanner crashed")
                    ok = False
                    fail_reason = "planner_failure"
                    break
                # Restart with the new plan; renumber indexes
                steps = [s.model_copy(update={"step_index": i + 1})
                         for i, s in enumerate(new_steps)]
                idx = 0
                continue

            # PASS → next step
            idx += 1

    duration_ms = int((time.perf_counter() - t0) * 1000)
    return TaskResult(
        ok=ok,
        trajectory=trajectory,
        extracted_content=extracted_content,
        selector_cache_hits=actor.cache_hits if 'actor' in locals() else 0,
        selector_cache_writes=actor.cache_writes if 'actor' in locals() else 0,
        healed_selector_count=actor.cache_heals if 'actor' in locals() else 0,
        duration_ms=duration_ms,
        fail_reason=fail_reason,
    )


# ── Worker handler signature: (input_dict) -> result_dict ────────────────────

async def browser_task_handler(payload: dict) -> dict:
    task_input = TaskInput(**payload)
    result = await run_task(task_input)
    return result.model_dump(mode="json")
