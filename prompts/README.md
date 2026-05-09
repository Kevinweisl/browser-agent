# prompts/

Curated record of the Claude Code dialogues that shaped this repo. Not full
transcripts; one entry per non-trivial decision, written as: **what I asked →
options Claude surfaced → what we picked → why → outcome**. The brief
explicitly says these will be read, so the goal is to make the design
history navigable, not exhaustive.

## How to read this folder

Read in chronological order if you want the narrative arc. Read by topic if
you want a specific decision.

| Date | Topic | Decision in one line |
|---|---|---|
| 2026-04-30 | [architecture-three-component](2026-04-30-architecture-three-component.md) | Use Planner / Actor / Validator three-component loop instead of a single monolithic LLM agent |
| 2026-05-01 | [locator-ladder-revision](2026-05-01-locator-ladder-revision.md) | Drop standalone "ARIA" tier and "XPath" tier; add Healenium-style aria-fingerprint to cache row |
| 2026-05-01 | [k2-vs-k3-validator](2026-05-01-k2-vs-k3-validator.md) | Settle on K=2 ensemble validator after K=3 regressed in v2/v3 |
| 2026-05-01 | [sec-httpx-fallback](2026-05-01-sec-httpx-fallback.md) | Route SEC EDGAR hosts through `httpx` not Playwright; "self-correction" includes recognising when the browser is the wrong primitive |
| 2026-05-01 | [eval-determinism-3run](2026-05-01-eval-determinism-3run.md) | Run the 10-task eval three times to separate single-shot luck from reproducible pass-rate |
| 2026-05-09 | [self-maintenance-completion](2026-05-09-self-maintenance-completion.md) | Wire aria-fingerprint healing path that was scaffolded but unused; close the gap between "store fingerprint" and "use fingerprint" |
| 2026-05-09 | [edge-case-eval](2026-05-09-edge-case-eval.md) | Three deliberate-failure tasks (stale-url / empty-search / ambiguous-edit); 0/3 → 1/3 across three iterations; detection without recovery is half a fix |

## What's NOT here

- Full LLM transcripts. The interesting part is the decision, not the rambling.
- Every minor edit. Tiny refactors / typo fixes don't get a prompt entry.
- Detailed code review back-and-forth. Those land in commit messages.
- System prompts (the `planner.txt` / `validator.txt` that drive the LLMs at
  runtime). Those live next to the code in `src/workers/browser/prompts/`,
  not here — different audience.

## Conventions

- Filename: `YYYY-MM-DD-topic-as-kebab.md`. Date when the decision was made.
- Each file has a `## Decision` line so you can scan-read.
- Quoted user prompts are paraphrased to compress; the actual prompts ran in
  Claude Code locally.
