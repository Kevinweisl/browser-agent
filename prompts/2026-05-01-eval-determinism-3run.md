# 2026-05-01: Three-run determinism check

## The question

v7 of the browser-agent hit 10/10 on the eval. The Planner is K=1 LLM with
`temperature=0.2` (no seed). Every Validator vote is K=2 with
`temperature=0.1`. So every run has stochastic components.

A 10/10 single run could be (a) the system genuinely passes the eval set,
(b) lucky single-shot. Without further evidence, we couldn't tell which.

## What we did

Ran the full 10-task eval three consecutive times on the night of 2026-05-01.

| Run | Pass rate | Total wall time |
|---|---|---|
| 1 | 10 / 10 | ~478 s |
| 2 | 10 / 10 | ~502 s |
| 3 | 10 / 10 | ~428 s |

Pass-rate determinism: **30 / 30 (100%)** across the three runs.

## What's stable, what isn't

- **Pass / fail outcome**: deterministic on this eval set. No flakes across
  the three runs.
- **Step count per task**: drifts slightly. The Planner produces equivalent
  but not identical plans across runs (e.g., 2-step vs 3-step decomposition
  of the same task).
- **Wall-clock per task**: high variance, up to 10× spread on
  network-bound tasks. Run 2's gen-002 took 67s, run 1's took 6s — same
  task, same plan, network noise. Latency is not deterministic; pass-rate
  is.
- **Validator votes**: the trajectory log shows the same PASS / REPLAN
  decisions across runs even when the Planner produced slightly different
  step text. The Validator is reading the post-state, not the plan.

## Decision

**Three runs is enough evidence to claim "pass-rate determinism" without
overclaiming "the agent is deterministic".** The README and task2 doc both
state explicitly that latency is non-deterministic; that's not a bug, it's
network noise dominating the wall clock on small DOMs.

## What this captures for the interviewer

A reviewer who looks at `last_run.json` sees 10/10 — that's a single run.
The 3-run check turns that into "we replicated this; it isn't a fluke",
which is the difference between a credible result and a posted screenshot.

The eval set is small (10 tasks, intentional — see
`docs/per-task/task2-browser-agent.md` for why we didn't chase WebVoyager
or BrowseComp), so 3-run × 10 = 30 trials is a reasonable verification
budget. We did not run more because the marginal information per
additional run drops fast and the LLM cost adds up.

The reviewer's "held-out test" the brief mentions will run on different
tasks against the same agent. That's the more important determinism
question — the architecture, not this specific eval set. Three repeat runs
on this set is the cheap signal we control; the held-out is the real
signal.
