# 2026-04-30: Planner / Actor / Validator three-component loop

The brief asks for a browser agent that "self-corrects" and "self-maintains"
with substance, "not just try/except retry". The first design question: one
LLM call per turn, or split the responsibility?

## Two readings

### Reading 1: Single agent, ReAct-style

One LLM that thinks, picks the next tool, observes the result, repeats. Like
the canonical ReAct loop or a stripped-down Computer Use agent. Cheap to
prototype: one prompt, one parser, one loop.

Strength:
- Minimal moving parts. Easy to debug — there's only one mind.
- The agent has full context of what it tried, so "self-correction" is
  whatever the model decides to do next turn.

Weakness:
- "Self-correction" and "did this step actually advance the goal?" are
  collapsed into the same call. A model that's confident-but-wrong has no
  external check.
- "Silent failure" detection (the brief grades this) requires a separate
  judgment from the actor's own narration of what it did. Same model, same
  prompt, same blind spot.
- Stateful loops with a single LLM are how agents wedge themselves into
  retry storms.

### Reading 2: Planner / Actor / Validator split

Planner decomposes the NL task into ordered Steps once (K=1 LLM). Actor
executes each step deterministically against Playwright with a locator
ladder (no LLM). Validator decides PASS / REPLAN / ABORT per step (K=2
LLM ensemble). On REPLAN, history + reason go back to Planner for a fresh
plan from the current page state.

Strength:
- The Validator is a different model role than the Actor's planner. Silent
  failure has an outside check that doesn't share the Actor's blind spot.
- Locator selection is mechanical, not LLM-driven — selectors are cheap and
  deterministic, the LLM should not be picking CSS selectors.
- "Diagnose then change strategy" maps cleanly to: Validator emits a reason →
  Planner sees the reason and produces a different plan. The reason is
  structured, not free-form, so the Planner can react to it.

Weakness:
- Three components × per-step Validator calls = more LLM tokens per task.
- Replan loops can still be wasteful if the Validator over-rejects (this
  happened in v3, see [k2-vs-k3-validator](2026-05-01-k2-vs-k3-validator.md)).

## Decision

**Reading 2.** The grading axis on "self-correction with substance, not
try/except" rules out the single-agent shape — the brief is asking for a
visible separation between "do" and "judge". The replan signal must come
from somewhere outside the Actor's own confidence.

## How it shows up in the code

- `planner.py` — `plan_task()` initial plan, `replan(history, reason)` for
  re-plans. Different temperatures (0.2 → 0.4) so replan explores more.
- `actor.py` — pure mechanics. Snapshot, resolve locator, execute the
  Playwright action, snapshot again. Returns a `StepResult` with no
  judgment; the Actor never decides "ok".
- `validator.py` + `silent_failure.py` — the cheap-signal cascade
  (URL/DOM/aria diff, mutating-action-no-change) plus the K=2 LLM vote.
- `handlers.py:run_task` — the loop. On REPLAN, replan_history is built
  with structured `{intent, error, validation_reason, signals}`; that
  structure is what makes "diagnosis → strategy change" reviewable.

## Outcome

The split paid off in two places that wouldn't have worked in Reading 1:

1. **Passive-action short-circuit** — `extract` / `screenshot` / `wait_for`
   skip the LLM Validator and pass on cheap signals alone (`handlers.py:35`).
   That's only legal because the Actor and Validator are different;
   bypassing one doesn't risk leaving the other in an inconsistent state.
2. **httpx fallback for SEC** — when the Actor recognises a SEC host, it
   swaps Playwright for httpx and pretends the page was navigated normally
   (see [sec-httpx-fallback](2026-05-01-sec-httpx-fallback.md)). The
   Validator doesn't care that the navigation didn't go through Chromium;
   it sees the post snapshot and votes. Architecturally clean.
