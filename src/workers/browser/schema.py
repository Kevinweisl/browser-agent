"""Pydantic models + enums shared across the browser worker."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ActionType(StrEnum):
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    SELECT = "select"
    EXTRACT = "extract"
    WAIT_FOR = "wait_for"
    SCREENSHOT = "screenshot"


class LocatorTier(StrEnum):
    """Names match the 7-tier ladder in docs/research/2026-05-01-browser-agent-update.md."""
    CACHED = "cached"
    GET_BY_ROLE = "get_by_role"
    GET_BY_LABEL = "get_by_label"
    GET_BY_TEST_ID = "get_by_test_id"
    GET_BY_TEXT = "get_by_text"
    CSS_NO_CLASS = "css_no_class"
    VISION_FALLBACK = "vision_fallback"


class NegativeOracle(BaseModel):
    """Cheap success/failure assertions evaluated by `silent_failure.py`."""
    must_appear: list[str] = Field(default_factory=list)
    must_not_appear: list[str] = Field(default_factory=list)
    url_must_change: bool = False
    url_must_match: str | None = None  # regex


class SelectorHints(BaseModel):
    """Optional structured hints from the Planner. The LocatorResolver tries
    these in tier order before falling back to inference from `target_intent`.

    All fields optional. The Planner provides whichever it knows; the
    resolver fills in via heuristics if absent.
    """
    role: str | None = None        # 'button', 'link', 'textbox', 'combobox', ...
    name: str | None = None        # accessible name for getByRole
    label: str | None = None       # for getByLabel
    test_id: str | None = None     # for getByTestId
    text: str | None = None        # for getByText
    css: str | None = None         # CSS without class — id or data-* only
    iframe_url_substring: str | None = None  # if the element is inside an iframe


class Step(BaseModel):
    """One unit of intent produced by the Planner."""
    step_index: int
    action_type: ActionType
    target_intent: str = Field(
        description="Semantic intent like 'search submit button' — used for cache key + resolution.",
    )
    value: str | None = None  # for type/select
    url: str | None = None    # for navigate
    extract_query: str | None = None  # for extract
    selector_hints: SelectorHints = Field(default_factory=SelectorHints)
    success_criteria: NegativeOracle = Field(default_factory=NegativeOracle)


class PageSnapshot(BaseModel):
    url: str
    title: str
    dom_hash: str
    aria_snapshot: str = ""  # Playwright YAML form
    text_excerpt: str = ""   # first ~2k chars of visible text


class StepResult(BaseModel):
    step_index: int
    locator_tier: LocatorTier | None = None
    selector: str | None = None
    cache_hit: bool = False
    success: bool
    error: str | None = None
    pre: PageSnapshot | None = None
    post: PageSnapshot | None = None
    extracted: Any = None


class ValidatorDecision(StrEnum):
    PASS = "pass"
    REPLAN = "replan"
    ABORT = "abort"


class StepValidation(BaseModel):
    decision: ValidatorDecision
    reason: str
    silent_failure_signals: list[str] = Field(default_factory=list)
    confidence: float = 1.0


class TrajectoryEvent(BaseModel):
    """One step + its validation, persisted in result for audit."""
    step: Step
    result: StepResult
    validation: StepValidation


class TaskInput(BaseModel):
    task: str
    secrets: dict[str, str] = Field(default_factory=dict)
    max_steps: int = 25
    max_seconds: int = 300  # wall-clock cap; honored by handlers.run_task loop
    starting_url: str | None = None  # if omitted, planner emits a navigate step


class TaskResult(BaseModel):
    ok: bool
    trajectory: list[TrajectoryEvent]
    extracted_content: Any = None
    selector_cache_hits: int = 0
    selector_cache_writes: int = 0
    healed_selector_count: int = 0
    duration_ms: int
    fail_reason: Literal[
        "completed",
        "step_cap_exceeded",
        "wall_clock_exceeded",
        "validator_aborted",
        "planner_failure",
        "unrecoverable_step_error",
    ] = "completed"
