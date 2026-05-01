# Day 6 end decision — Task 2 + Task 3 orchestration demo (deferred plan)

**Decision date**: 2026-05-01 end of Day 6.
**Question**: Should we add a Day 7 orchestration demo wrapping Task 2 (browser
agent) + Task 3 (SEC 10-K extractor) into a single end-to-end flow?

**Decision**: **NO — defer.**

## What an orchestration demo would look like

```
NL request: "Find Apple's most recent 10-K filing on SEC EDGAR and extract Item 1A risk factors"
         ↓
   Browser-task skill (Task 2)
   - Navigate to https://www.sec.gov/cgi-bin/browse-edgar?CIK=0000320193&type=10-K
   - Find the most-recent 10-K row, extract its accession number
         ↓
   Sec-extract-10k skill (Task 3)
   - Run extraction pipeline on (cik, accession)
         ↓
   Return Item 1A text + status + char ranges
```

This demonstrates platform value: skills compose, the gateway routes by
`worker_target`, and the result of one skill feeds into the next.

## Why we're saying no

1. **Browser finance pack is 2/5.** fin-002 (SEC EDGAR browse-edgar) and
   fin-003 (SEC EFTS full-text search) both failed at navigate with
   `validator_aborted` — the validator unanimously decided the post-navigate
   text excerpt was a block / empty render. Building an orchestration demo
   on top of a 40%-success-rate dependency means the demo itself runs at
   ~40% × 100% = 40% — that's worse storytelling than ship-as-is.

2. **The integration narrative is already implicit.** All three skills
   (`browser-task`, `sec-extract-10k`, the four CI/CD skills) run on the
   same FastAPI gateway + worker pool. The `worker_target` routing happens
   per skill. A reviewer reading `docs/design/...` §2 sees this without
   needing a runtime demo. Adding "skill A calls skill B" plumbing without
   solving the underlying browser failures would be ceremony without
   substance.

3. **Better uses of remaining time** (in priority order):
   - Fix the `_has_one` tie-breaker that caused gen-005 to exhaust replan
     budget. (~30 min — improves locator robustness on real sites.)
   - Add `wait_until="networkidle"` + retry logic on navigate steps that
     come back with empty `text_excerpt`. (~30 min — would unblock fin-005
     Berkshire IR.)
   - Write the final per-task READMEs / submission summary. (~1 h.)
   - Optional: Zeabur deployment if Kevin registers.

4. **Submission deadline ~2026-05-07** — opportunity cost matters. A demo
   that requires 4-6 hours of polish + has 40% success rate competes with
   high-leverage cleanup work.

## What we ARE doing instead

- Document this decision (this file) so the integration story is told in
  prose, not in flaky runtime.
- Continue documenting Day 5 / Day 6 lessons in `tasks/lessons.md`.
- Sharpen the existing tasks before Day 7.

## What we'd do if we DID build it

For posterity — if the deadline were 2 weeks away instead of 6 days:

1. New skill `find-filing` (or extend `browser-task` with a `task_template`
   field) that returns `(cik, accession)` from a NL query.
2. Fix SEC anti-bot: rotate UA + use `wait_until="networkidle"` + add
   `Accept-Language` header that matches the real Chrome.
3. New gateway endpoint `POST /skills/orchestrate` that takes a
   high-level task, plans which skills to call, and chains them. Or simpler:
   a synchronous wrapper script that does it imperatively for the demo.
4. Eval: 5 SEC-flavored end-to-end tasks (e.g., "Latest 10-K Item 1A for
   $TICKER"). Score on `(cik, accession)` correctness × extraction recall.

This is on the roadmap in `docs/design/...md` §1 "Optional Day 7" but is
explicitly **not** part of the submission.
