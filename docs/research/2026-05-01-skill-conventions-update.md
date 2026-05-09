# Skill Conventions, Verification & Gap-Fill (2026-05-01)

Source: subagent run (general-purpose, ~18 tool uses, 2.7 min wall-clock).
Triggered by Day 5 audit of the 4 CI/CD SKILL.md files we authored earlier.

This file records ONLY the delta vs the prior research at
`docs/research/2026-04-30-skill-conventions-research.md` (289 lines, dated
yesterday). Do NOT duplicate prior content.

---

## Currency check (no structural changes since 2026-04-30)

`anthropics/skills` repo activity in the past 4 weeks: all commits hit
`claude-api/` skill content (Managed Agents memory stores, license updates).
No changes to:
- `skill-creator/` (the canonical eval methodology source)
- `pdf` / `xlsx` / `docx` / `pptx` (our description-style references)
- The SKILL.md frontmatter spec at agentskills.io

**One actionable finding**: PR #898 (2026-04-13) fixed a YAML rendering bug
where `description:` without quotes broke `npx skills` install. We use
`description: |` block scalar (multi-line), which is unaffected. No change
needed.

**Empirical validation of our DO-NOT-TRIGGER pattern**: issue
anthropics/claude-code#43259 (2026-04) reports Anthropic's own `docx` skill
over-triggering on `.md` output requests. The proposed community fix uses
the exact `Do NOT trigger when ...` phrasing we already adopted. Our
methodology is now ahead of `docx`'s current shipped description.

---

## Two new findings beyond prior research

### 1. Imperative redirect beats noun-only redirect

Marc Bara's 650-trial study (Medium, 2026-03):
> "ALWAYS invoke this skill when... Do not <alternative action> directly.
> Use this skill first."

Reports ~20× higher trigger rate vs "Use when..." for **single skills**.

**We do NOT blanket-apply this.** Across our 4 sister skills, all 4 saying
"ALWAYS invoke" recreates the jeremylongshore disaster (anti-pattern A3).
What we DO adopt is the **imperative redirect** for sister-skill
disambiguation: change `Do NOT use for: X (use sister-skill)` (noun-only) to
`For X, use sister-skill instead.` (imperative + named target).

The router can't act on a noun "use sister-skill"; it needs a verb pointing
to a specific named target.

### 2. Front-load quantification: ≤30 chars, ideally ≤15

Measured first-decisive-keyword position across 10 real skills:

| Skill | Position |
|---|---|
| `agentic-actions-auditor` | 7 |
| `release-notes-drafter` | 7 |
| `test-driven-development` | 9 |
| `xlsx` | 33 |
| `pdf` | 56 |
| `doc-coauthoring` | 60 |

Median: ~9. **Anthropic's pdf and docx fail this metric** at 56 and 60.
Trail of Bits and superpowers consistently hit ≤9. Our 4 CI/CD skills all
land at 4-8, better than Anthropic's own examples on this dimension.

---

## kaochenlong tutorial, verbatim Chinese passages

Prior research only inferred. Verbatim:

> 「好的 description 會明確列出這個 Skills 能做什麼事、什麼情況下該被啟用。」
> 「description 要寫得像在跟 Agent 解釋『什麼時候該用這個技能』」
> 「Agent 讀到 description 之後,才能正確判斷『這個對話跟這個 Skills 有沒有關』。」

The article does NOT discuss trigger eval or DO-NOT-TRIGGER patterns. It
stops at "third-person + clear", basic. Our methodology is meaningfully
deeper than the brief's referenced tutorial. Citing it as the floor we
build above is appropriate.

---

## Audit + edits applied to our 4 SKILL.md files

Each skill received one targeted edit. Diffs committed in
`feat(d5): refine 4 CI/CD SKILL.md descriptions per audit`.

| Skill | Edit | Rationale |
|---|---|---|
| **lint-and-test** | Add `mypy`, `prettier`, `ESLint + jest`, `type-check` triggers; convert `(use sister)` to `For X, use sister instead.` | Boundary heuristics from prior research line 237 listed these but description didn't include them. Imperative redirect (new finding 1). |
| **build-and-release** | Drop the wasted-words explanation of `disable-model-invocation` (router skips this skill anyway); use the freed ~30 chars for `tag v1.2.3`, `twine upload`, `ghcr`, `github release` triggers. | Wasted words anti-pattern (A6 length pitfall in prior research). The router doesn't need to know WHY disable-model-invocation matters, it just acts on the flag. |
| **dependency-audit** | Replace abstract `at the dependency tree level` with natural-language phrases: `is lodash safe`, `any CVEs in our packages`, `scan our requirements.txt`. | Users don't say "at the dependency tree level"; they say "is lodash safe". The kaochenlong principle: write like the user, not like the spec. |
| **security-scan** | Add explicit disambiguation hint: `These are queries about CODE WE WROTE, not LIBRARIES WE IMPORTED. For known CVEs in third-party packages (lodash, requests, etc.), use dependency-audit instead.` | Mirrors the killer pattern from xlsx's "primary input or output" but in plainer English. Resolves the most likely sister-skill conflict (security-scan ↔ dependency-audit). |

All 4 stayed within the 1024-char budget after edits (637-787 chars). All 4
passed the front-load threshold (decisive keyword ≤ 8 chars from start).

---

## What we deliberately did NOT change

1. **Hello, sec-extract-10k, browser-task descriptions**, out of audit scope
   (Task 2 / Task 3, not Task 1). Their descriptions are well-formed at
   276 / 885 / 888 chars. Re-audit if Day 6 trigger eval surfaces issues.

2. **`disable-model-invocation: true` on `build-and-release`**, kept. Prior
   research line 31 documented Trail of Bits' `git-cleanup` precedent. The
   only change was removing the IN-DESCRIPTION explanation of WHY (waste of
   description budget; the YAML field is what matters).

3. **The "Multi-tool pipeline" / "Multi-tool orchestration" headings inside
   each SKILL.md body**, these support `docs/per-task/task1-skills-platform.md`
   "Design Patterns Demonstrated" table. Body content doesn't compete for
   the 1024-char description budget; it explains the implementation for
   future maintainers.

---

## Open question for trigger eval interpretation

After applying these edits, run `python evals/skill-trigger/runner.py` and
observe whether:
- TPR per skill ≥ 0.8 (should-trigger queries get the right skill)
- FPR per skill ≤ 0.2 (should-not-trigger queries don't pick this skill)

If TPR is below 0.8 on any skill, the most likely cause is missing trigger
keywords (under-specification), fix is to add 1-2 more natural phrases.

If FPR is above 0.2, the most likely cause is sister-skill conflict, fix
is to strengthen the imperative redirect or add a more explicit
disambiguation clause (like the `code we wrote vs libraries we imported`
hint we just added to security-scan).

---

## Sources (delta only)

- https://github.com/anthropics/skills/commits/main (verified via subagent)
- https://github.com/anthropics/skills/pull/898 (YAML rendering bug fix)
- https://github.com/anthropics/claude-code/issues/43259 (`docx` over-trigger)
- https://github.com/anthropics/skills/blob/main/skills/doc-coauthoring/SKILL.md
- https://medium.com/@marc.bara.iniesta/claude-skills-have-two-reliability-problems-not-one-299401842ca8
- https://kaochenlong.com/claude-code-skills (verbatim quoted)
- https://github.com/obra/superpowers/blob/main/skills/writing-skills/SKILL.md
