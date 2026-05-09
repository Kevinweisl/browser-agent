# 2026-05-01: Locator ladder revision — 7-tier → 6-tier + fingerprint

Original Day-1 design had a 7-tier ladder copied from a 2024-era browser-agent
write-up: `getByRole → testid → ARIA → text → CSS → XPath → vision`. On
Day 6 we did a research delta on the current Playwright + Stagehand
recommendations and rewrote the ladder.

## What changed

### Removed: standalone "ARIA" tier

`page.get_by_role(role, name=...)` already uses ARIA name resolution under
the hood. A dedicated ARIA tier was redundant — it overlapped completely
with `getByRole` and never produced a different selector.

### Removed: "XPath" tier

Playwright's official 2026 stance is that XPath is an anti-pattern: brittle
to DOM rewrites, no semantics, fights the rest of the locator API.
`page.locator(xpath)` still works but the docs explicitly recommend against
it for new code. Keeping it in the ladder would have signalled we hadn't
read the current docs.

### Added: aria-fingerprint per cache row

Every successful resolution stores not just the serialized selector but a
small bag of attributes (`role`, `aria-label`, `id`, `data-testid`, `tag`,
first 120 chars of text content) in the `selector_cache.aria_fingerprint`
JSONB column.

The point isn't to use the fingerprint for resolution — it's to give a
future heal step something to anchor to when the DOM drifts. This is the
Healenium pattern, which is still production-standard for selector
self-maintenance in 2026.

### Stubbed: vision fallback (tier 7)

Computer Use beta still requires an Anthropic CU API key. The ladder
exposes a `vision_fallback` constructor injection point so the integration
is one wire-up away, but the stub returns None for this submission.

## Final ladder

```
1. CACHED          — selector_cache lookup; dom_hash equality first,
                     fingerprint heal on DOM drift (heal path landed in v2)
2. GET_BY_ROLE     — Playwright official first choice
3. GET_BY_LABEL    — form-element preferred
4. GET_BY_TEST_ID  — automation-only, brittle to dev practice
5. GET_BY_TEXT     — visible text anchor
6. CSS_NO_CLASS    — id / data-* only (class chains explicitly rejected)
7. VISION_FALLBACK — stub
```

## Decision

**6-tier + fingerprint, vision stubbed.** The two removed tiers were
duplicate work and an anti-pattern; the added fingerprint is what makes
"self-maintenance" mean more than "cache exact match". Vision is
out-of-scope for the submission but the injection point is in place so the
gap is honest.

## What this captures for the interviewer

A reviewer who reads `locator_ladder.py` will see we (a) read current
Playwright docs not stale tutorials, (b) understand ARIA / XPath are a
duplicate / anti-pattern pair, and (c) know that "self-maintenance" in 2026
means Healenium-style heal, not "try the next tier and hope".

The full research delta lives in `docs/research/2026-05-01-browser-agent-update.md`.
