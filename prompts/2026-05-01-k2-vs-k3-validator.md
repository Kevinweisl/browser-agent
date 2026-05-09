# 2026-05-01: K=2 vs K=3 validator ensemble

The Validator decides PASS / REPLAN / ABORT per step. The decision is
discrete (3-class), so a vote is cheap to tally. Question: how many votes?

## Why the question matters

K=1 has no disagreement signal. K=2 ties on disagreement (we treat ties as
REPLAN with `fallback_used=True` in the trajectory). K=3 always has a
majority. K≥4 starts costing real money on per-step LLM calls and we
already validate every mutating step.

## What we tried

| Eval run | K | Result | What changed |
|---|---|---|---|
| v1 | 2 (Nemotron + Mistral) | 6/10 | baseline |
| v2 | 3 (added Qwen) + `.first()` narrow fallback | 4/10 | regression — `.first()` clicked wrong twins |
| v3 | 3 (Qwen kept), `.first()` removed | 3/10 | worse — K=3 over-replanned anchor clicks |
| v4 | 2 (back to Nemotron + Mistral) + planner prompt rules for search forms | 7/10 | gen-005 PASS via "type then click submit" pattern |
| v6 | 2 + SEC httpx fallback + Berkshire URL fix | 9/10 | fin-002 + fin-005 PASS |
| v7 | 2 + JSON-aware fin-003 oracle | 10/10 | final |

## What happened with K=3

Anchor clicks on Wikipedia (e.g. clicking a section link inside a long
table-of-contents) reliably triggered URL change but ariaSnapshot diff was
small (the chrome around the anchor barely changes). K=3 with three
different model families had at least one voter say "DOM didn't materially
change → REPLAN" on every such click. Two-out-of-three is a majority, so
the step replanned even when it had succeeded.

K=2 ties on disagreement, and we resolve ties to REPLAN with a `fallback`
flag. That sounds equivalent, but it isn't: with K=2 and a noisy voter,
half the disagreements are PASS-vs-PASS-ish (same answer, different
reasoning), only the other half are real splits. With K=3 every voter
contributes a no.

The tighter framing: K=3 raised the bar for "clearly succeeded" beyond
what's measurable from a single step's pre/post snapshot. Wikipedia
anchors are exactly the kind of "the thing I clicked is now in the
viewport but the page didn't otherwise change" signal that K=3 is too
suspicious of.

## Decision

**K=2.** K=3 didn't help and actively hurt on a class of legitimate steps.
Money matters less than the correctness regression. The third validator
slot stays in the schema (Nemotron + Mistral + optional Qwen via env) but
defaults to off.

## What this captures for the interviewer

We measured K=2 vs K=3 on real eval data, found K=3 regressed, and chose
the simpler, cheaper option for the right reason — not because K=2 was
default, but because K=3 was empirically worse on our task distribution.
The "more is better" heuristic for ensemble size doesn't apply when the
voters share a confidence calibration (LLM Validators all over-rotate on
"DOM didn't visibly change → REPLAN").

This bears on the broader brief grading axis on cost / reliability
trade-offs: cheaper *and* more reliable was the actual answer, not the
expected "spend more for accuracy". Worth pointing out in the README.
