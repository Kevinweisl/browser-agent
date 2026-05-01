# Lessons captured (project-specific patterns and corrections)

A live document. New lessons go at the bottom dated; never delete past lessons
even when superseded — append a follow-up note instead. Reading order:
top-down chronologically.

---

## 2026-04-30 — Day 1 foundation

### L1. Postgres-only is enough for this scope
The original spec called for Inngest + Redis + Postgres. After Day 1 we
collapsed to Postgres + FastAPI alone; `SELECT ... FOR UPDATE SKIP LOCKED`
gives us multi-worker safety without a queue broker. **Lesson**: only add
infra when you can name the failure mode it prevents.

---

## 2026-05-01 — Day 4 LLM ensemble live integration

### L2. Always smoke-test each provider in isolation before voting
We discovered each NIM provider has quirks (DeepSeek wants
`chat_template_kwargs.thinking`, Nemotron wants `enable_thinking +
reasoning_budget`, Mistral rejects `reasoning_effort=low`). A per-provider
smoke test (`test_provider("deepseek")`, etc.) caught these one at a time.
**Lesson**: write a one-liner per-provider call before plumbing them through
voting. The wrapper hides failures behind ensemble fallback.

### L3. Reasoning-mode default to OFF
Reasoning mode (`THINKING_*=on`) adds 2-30× latency. Only worth it for
high-stakes discrete decisions (Validator). For Trigger eval and Extractor
augmentation, OFF is right. **Lesson**: don't enable reasoning by default
"to be safe" — measure first, add later.

### L4. Refactor decorators when you see the second copy
The 429 retry was inline in the LLM client first. When the SEC fetch path
asked for the same logic, we extracted `retry_async` to `shared/retry.py`.
**Lesson**: pulling DRY before the second use is premature; pulling at the
second use is the rule.

---

## 2026-05-01 — Day 5 trigger eval framework

### L5. Imperative redirect beats noun-only redirect for sister-skill disambiguation
Original SKILL.md descriptions said `Do NOT use for: container CVE
scanning` (noun-only). The router can't act on that. Changed to `For
container CVE scanning, use security-scan instead.` (imperative + named
target). Marc Bara's measurement: ~20× higher trigger correctness. **Lesson**:
always end disambiguation clauses with an imperative verb pointing to a
specific named target.

### L6. Front-load the decisive keyword ≤ 30 chars
Anthropic's own pdf and docx skills have decisive keywords at position 56
and 60. Trail of Bits and superpowers consistently hit ≤ 9. We landed all 4
CI/CD descriptions at 4-8. **Lesson**: what the router reads in the first
30 chars largely determines whether it picks your skill at all.

### L7. Tests should rotate concrete words; don't rely on fixed labels
The browser-smoke test originally clicked "More information..." link on
example.com. The site updated the text to "Learn more". Test failed silently
because no locator resolved. **Lesson**: concrete-string assertions need a
plan for upstream drift — either a regex with multiple alternatives or a
"page changed" detector that reports differently from "test logic broken".

### L8. NIM provider availability is not stable; treat as transient
DeepSeek's NIM endpoint timed out at 270+ s on 2026-05-01 (smoke test
proved it's the provider, not us). K=3 ensemble degraded to K=2
(Nemotron + Mistral) gracefully via `vote_role` config. **Lesson**: ensemble
voting must be config-driven (env var `{ROLE}_MODELS=...`), not code-driven.
Adding/removing providers should be a one-line .env change, no code edits.

---

## 2026-05-01 — Day 6 Task 2 browser agent

### L9. Research delta beats full re-research when first pass is recent
Day 1 had a comprehensive browser-agent SOTA research summary. By Day 6 it
was 1 day old. Instead of re-doing the whole pass, we ran a focused subagent
on **only what changed in the last 60 days** (3 min wall, 12 web queries).
Surfaced 2 actionable changes (WebVoyager saturated → BrowseComp; Stagehand
v3 deepLocator + action caching) and confirmed the rest of the design. **Lesson**:
when the prior research is < 1 week old, ask "what changed?", not "what's
the SOTA?".

### L10. Shift work AWAY from per-step LLM calls
The original design called for "Actor (Claude Sonnet)" — LLM in every step.
The Day 6 implementation moved LLM out of the Actor entirely: Planner emits
structured Steps with `selector_hints`; Actor is deterministic Playwright +
LocatorResolver; Validator (LLM K=2) judges outcomes. Saves ~80% of LLM
calls per task. **Lesson**: if a sub-component does well-defined dispatch,
make it deterministic; reserve LLM cycles for high-stakes strategy and
judgment, not execution mechanics.

### L11. asyncio.Semaphore is bound to its creating loop
The eval runner re-creates an event loop per filing via `asyncio.run()`
inside a sync wrapper. A semaphore cached at module level (single key:
provider name) ends up bound to loop A, then crashes on loop B with
"Semaphore is bound to a different event loop". Fix: key the cache on
`(id(loop), name)` so each loop gets a fresh semaphore lazily. **Lesson**:
when a process owns multiple event loops (sync→async wrappers, runner
batches), any cached `asyncio.*` primitive needs a per-loop dimension in
its cache key. Module-level caches are a footgun specifically here.

### L12. LLM-driven validator must NOT vet passive actions
The validator's prompt was tuned for catching silent failures on mutating
actions (click/type). When given an EXTRACT step (which by definition
doesn't mutate state), the validator interpreted "no_visible_state_change"
as "step failed" and returned REPLAN, sending the agent into infinite
re-extract loops. Fix: handlers.py short-circuits passive actions to PASS
on `result.success=True`. **Lesson**: only call the LLM judge on the
operation type its prompt is calibrated for; cheap deterministic shortcut
covers the rest.
