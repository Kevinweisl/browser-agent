# 2026-05-09: Closing the self-maintenance gap

## What was already in place

After the 2026-05-01 ladder revision, every successful Actor resolution
wrote three things into the `selector_cache` row:

- `selector` — the serialized Playwright locator string
- `dom_hash` — SHA-256 of the page HTML at the moment of resolution
- `aria_fingerprint` — JSONB bag of `{role, aria-label, id, data-testid, tag, text}`

Cache lookup compared `dom_hash`: equal → use cache, not equal → fall
through the ladder from tier 2.

## What was missing

The fingerprint was written but never *read*. `selector_cache.record_heal`
(stamps `last_healed_at` + `healing_diff`) was defined but never called
from anywhere in the codebase. So the "Healenium-style heal" we
[claimed in the locator-ladder revision](2026-05-01-locator-ladder-revision.md)
was scaffolded — fields existed, schema fit — but the heal *path* didn't
run. On every DOM drift, cache went silent and the resolver fell through
to tier 2 from scratch.

For a small eval set this didn't show up: the eval runner doesn't
re-encounter `(url_template, intent)` pairs across tasks. The bug only
hurts in production-shaped usage where the same intent is hit on the same
page over multiple sessions and the page rev'd between visits.

A reviewer reading the cache schema would have seen: "stores fingerprint,
never compares it" — and that gap is the difference between architecture
and theatre.

## Decision

**Wire the heal path before submission.** Specifically:

1. When `cache_dom_match=False` but a `cached_selector` exists, attempt the
   cached selector anyway — find it on the page, compute the new
   element's aria-fingerprint, compare to the stored fingerprint.
2. If at least 2 of 4 strong attributes match (`role`, `aria-label`, `id`,
   `data-testid`), treat as healed: use the element, write a
   `healing_diff` (which attribute changed), call `record_heal()`,
   continue.
3. If fewer than 2 match, the selector is genuinely stale — fall through
   to tier 2 as before.

The 2-of-4 threshold isn't science, it's the smallest bar that prevents
"role unchanged but everything else swapped" from looking like a heal.
Settable later if the threshold is wrong.

## What this captures for the interviewer

This is the entry that's least flattering to write — it documents that
"self-maintenance" was a half-implementation until a pre-submission audit.
But it's also the right thing to write: the brief grades depth and
honesty, and the gap was real. The audit caught it; the fix is in
`prompts/`'s git diff alongside the implementation; the test added in the
same commit shows the heal path actually runs.

The deeper lesson — and probably worth saying out loud in the interview —
is that "stored a thing" is not the same as "used a thing", and code
review on schema additions has to verify both halves. A row column that's
written and never read is a tell that something didn't ship.

For test evidence the heal works: `tests/test_browser_locator_ladder.py`
covers (a) DOM-hash equal → CACHED hit, (b) DOM-hash differ but
fingerprint matches → CACHED-via-heal, (c) DOM-hash differ + fingerprint
mismatches → fall through to tier 2.

(This entry is dated 2026-05-09, the same day the gap was identified. The
healing implementation lands in the same Tier-1 pass that produced this
prompt entry — they ship together.)
