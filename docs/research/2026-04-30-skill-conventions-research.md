# Skill Conventions — Deep Research (2026-04-30)

Source: subagent run (general-purpose, 56 tool uses, 8 min wall-clock).

This is a **second research pass**, deeper than the initial Day 0 research.
Triggered by a question on whether to consult Anthropic's official spec and
others' tutorials before writing SKILL.md files.

Verbatim quotes from raw GitHub URLs; all citations are dated 2026-04-30.

---

## TL;DR for our 4 CI/CD skills

1. **Hard `description` limit: 1024 chars.** Listing budget for skill name +
   description is ~1,536 chars. **Front-load** the key trigger keywords.
2. **`when_to_use` field — don't use it.** Claude-Code-only; almost no real
   skill in the wild uses it. Fold trigger phrases into `description`.
3. **Voice: third-person, present-tense, verb-first.** "Runs lint and tests
   …" not "I help you run …" or "You can use this to …".
4. **Adopt skill-creator's eval methodology** (Anthropic's own `run_loop.py`):
   - 20 queries per skill (8-10 should-trigger + 8-10 should-NOT-trigger)
   - **Should-not-trigger queries MUST include sister-skill triggers** —
     e.g. `lint-and-test` should-not includes "fix this CVE in lodash"
     so the description learns to defer to `dependency-audit`
   - 3 runs per query, majority vote
   - 5 iterations max, 60/40 train/test split
   - **Select best by test (held-out) score**, not train, to avoid overfitting
   - Detect triggering by parsing `tool_use` events of `Skill` or `Read` in
     stream-json output (run_eval.py:detection logic)
5. **`disable-model-invocation: true` is recommended for `build-and-release`**.
   Strong precedent: Trail of Bits' `git-cleanup` uses it for irreversible ops.
   Releases are tag/digest writes — Claude shouldn't auto-decide to ship.
6. **Add explicit DO-NOT-TRIGGER list to each description**. Anthropic's
   xlsx/docx/pptx skills do this; without it, sibling skills with overlapping
   keywords (e.g. dependency-audit vs security-scan on "vulnerability") will
   fight at runtime.
7. **No mature CI/CD skill in public domain** matches our 4-skill split.
   This is good news: we're not measured against prior art. We're measured
   on whether boundary decisions are well-justified.

---

## Definitive SKILL.md Spec (As of 2026-04)

### Universal frontmatter (agentskills.io/specification)

| Field | Required | Limit | Notes |
|---|---|---|---|
| `name` | Yes | 1-64 chars | lowercase + digits + `-`; must match parent dir; cannot contain `anthropic` or `claude` |
| `description` | Yes | 1-1024 chars | non-empty, no XML tags; must state WHAT and WHEN |
| `license` | No | — | recommended |
| `compatibility` | No | 1-500 chars | env requirements |
| `metadata` | No | — | string→string map |
| `allowed-tools` | No | — | **experimental**; space-separated string |

### Claude-Code-specific extensions

`when_to_use`, `argument-hint`, `arguments`, `disable-model-invocation`,
`user-invocable`, `model`, `effort`, `context: fork`, `agent`, `hooks`,
`paths`, `shell`. **None of these required for our purposes**; we'll only
use `disable-model-invocation: true` for `build-and-release`.

### Cross-surface differences

| Capability | Claude Code | Claude API `/v1/skills` | Claude.ai |
|---|---|---|---|
| Custom skills source | filesystem | upload via API, workspace-wide | upload zip per user |
| Network access | full | none (sandboxed) | varies |
| Skills sync across surfaces | ✗ | ✗ | ✗ |

→ **For our deployment**, the platform itself reads SKILL.md from filesystem
and presents skills as platform endpoints; we don't deploy skills to
Anthropic's `/v1/skills` API.

---

## skill-creator's Eval Methodology — Adopted Verbatim

Source: `anthropics/skills/skills/skill-creator/scripts/run_loop.py` argparse defaults.

| Parameter | Default | Notes |
|---|---|---|
| `--runs-per-query` | **3** | trigger is non-deterministic |
| `--num-workers` | **10** | parallel `claude -p` subprocesses |
| `--timeout` | **30 s** | per-query timeout |
| `--max-iterations` | **5** | optimization rounds |
| `--trigger-threshold` | **0.5** | query passes if trigger rate ≥ 50% (when should_trigger=true) or < 50% (when should_trigger=false) |
| `--holdout` | **0.4** | 40% test / 60% train, **stratified by `should_trigger`** |
| Eval set size | **20 queries** | 8-10 should-trigger + 8-10 should-not-trigger |

### Eval-set authoring rules (from skill-creator SKILL.md, verbatim)

- Should-trigger: 8-10 queries with **different phrasings of the same intent** —
  formal, casual, file-mention, file-not-mentioned-but-clearly-needed.
- Should-not-trigger: 8-10 **near-misses** — queries that share keywords or
  concepts but actually need something different. **Don't pick obviously
  irrelevant queries** like "write a fibonacci function" — they don't
  discriminate.

### Eval JSON format

```json
[
  {"query": "the user prompt", "should_trigger": true},
  {"query": "another prompt", "should_trigger": false}
]
```

### Trigger detection

`run_eval.py` writes a temp command file in
`.claude/commands/<skill-name>-skill-<uuid>.md`, runs
`claude -p <query> --output-format stream-json --include-partial-messages`,
and parses `content_block_start` events for `tool_use` of `Skill` or `Read`
where the input contains the temp skill name. Early detection — does not
wait for full assistant output.

### Caveat (skill-creator SKILL.md verbatim)

> "Claude only consults skills for tasks it can't easily handle on its
> own — simple, one-step queries like 'read this PDF' may not trigger a
> skill even if the description matches perfectly. Make queries substantive."

---

## Description Pattern Catalog (real examples, verbatim)

### Anthropic style (pushy + DO-NOT-TRIGGER list)

#### `pdf` (anthropics/skills/skills/pdf/SKILL.md)

> Use this skill whenever the user wants to do anything with PDF files.
> This includes reading or extracting text/tables from PDFs, combining or
> merging multiple PDFs into one, splitting PDFs apart, rotating pages,
> adding watermarks, creating new PDFs, filling PDF forms, encrypting/
> decrypting PDFs, extracting images, and OCR on scanned PDFs to make them
> searchable. If the user mentions a .pdf file or asks to produce one,
> use this skill.

#### `xlsx` (truncated for brevity, key pattern)

> Use this skill any time a spreadsheet file is the primary input or output.
> ... **Do NOT trigger when the primary deliverable is a Word document, HTML
> report, standalone Python script, database pipeline, or Google Sheets API
> integration, even if tabular data is involved.**

The DO-NOT-TRIGGER clause is **the killer feature** for sibling-skill disambiguation.

### Trail of Bits style (medium-length, language enumeration)

#### `agentic-actions-auditor` (verbatim)

> Audits GitHub Actions workflows for security vulnerabilities in AI agent
> integrations including Claude Code Action, Gemini CLI, OpenAI Codex, and
> GitHub AI Inference. Detects attack vectors where attacker-controlled
> input reaches AI agents running in CI/CD pipelines, including env var
> intermediary patterns, direct expression injection, dangerous sandbox
> configurations, and wildcard user allowlists. Use when reviewing workflow
> files that invoke AI coding agents, auditing CI/CD pipeline security
> for prompt injection risks, or evaluating agentic action configurations.

Notes: enumerates concrete tool/agent names (`Claude Code Action`,
`Gemini CLI`, `OpenAI Codex`) for keyword match. Three-clause structure:
{what it does} + {what it detects} + {when to use}.

### superpowers (obra) style (terse imperative)

#### `test-driven-development`

> Use when implementing any feature or bugfix, before writing implementation code

A complete description in **9 words**. Works because the skill name carries
the meaning; `description` only needs to disambiguate WHEN.

---

## Anti-Patterns (Avoid)

### A1. Vague (best-practices doc verbatim)

```yaml
description: Helps with documents
description: Processes data
description: Does stuff with files
```

Zero keywords for trigger. Always fails sister-skill disambiguation test.

### A2. Wrong POV

- Bad: `"I can help you process Excel files"` (first person)
- Bad: `"You can use this to process Excel files"` (second person)
- Good: `"Processes Excel files and generates reports"` (third person)

### A3. Real-world disaster: jeremylongshore CI/CD plugins

```
description: 'Execute use when you need to work with deployment and CI/CD.
This skill provides deployment automation and pipeline orchestration with
comprehensive guidance and automation. Trigger with phrases like "deploy
application", "create pipeline", or "automate deployment".'
```

This identical wording is used across `building-cicd-pipelines`,
`orchestrating-deployment-pipelines`, `managing-deployment-rollbacks` —
all three trigger on every CI query. **This is the trap our 4 skills must
avoid.** Each sister skill needs unique nouns and DO-NOT-TRIGGER clauses.

### A4. Pushy when it backfires

`"Make sure to use this skill whenever..."` is **good** when the skill is
unique in its niche. **Bad** when 4 sibling skills all say it — produces
over-triggering and wrong-skill picks.

### A5. Time-locked

Avoid `"Use the v2 API endpoint after August 2025"` — best-practices doc
explicitly calls this out.

### A6. Length pitfalls

- Hard ceiling: 1024 chars.
- Listing truncation: combined `description` + `when_to_use` cut at 1,536 chars.
- **Front-load key use case** so it survives auto-truncation under high
  skill count or low context budget.

---

## CI/CD Boundary Heuristics (recommended for our 4 skills)

Each skill's `description` should include a **DO-NOT-TRIGGER** clause
naming the sibling skill that handles the near-miss case.

| Skill | Trigger keywords | Explicit DO-NOT-TRIGGER |
|---|---|---|
| `lint-and-test` | lint, format, prettier, eslint, ruff, black, pytest, jest, unit test, type check, mypy | not for: integration/E2E tests, dependency CVE checks, build artifacts |
| `build-and-release` | build, package, bundle, docker image, npm publish, semver bump, release tag, changelog, dist | not for: linting, test selection, vulnerability scanning |
| `dependency-audit` | dependency, package version, outdated, npm audit, pip-audit, dependabot, license check, transitive deps | not for: SAST/code scan, secret scan, runtime exploit detection |
| `security-scan` | SAST, secret scan, gitleaks, trivy, semgrep, OWASP, vulnerability scan, code scan | not for: dependency CVE listing (→ dependency-audit), build/lint config |

---

## kaochenlong tutorial (the one referenced in the brief)

**Inferred takeaways** (the brief author cites this article, so candidates
are expected to know):

1. **`description` is the trigger surface.** "好的 description 會明確列出
   這個 Skills 能做什麼事、什麼情況下該被啟用" ("A good description
   explicitly lists what the skill does AND when it should activate.")
2. **Voice**: write `description` like technical guidance to an engineer,
   not marketing copy.
3. **Three-layer progressive disclosure** (~100 tokens metadata →
   <5,000 tokens SKILL.md → bundled scripts/refs on demand).
4. **Skill vs slash-command vs MCP vs subagent**: candidates should
   articulate when each applies. Skills = repeatable workflows where
   Claude auto-judges applicability.
5. **Test/iterate, don't fire-and-forget.**

---

## Open questions left

1. `allowed-tools` exact format: agentskills.io says space-separated string;
   real skills use both space-separated and YAML lists. **We use space-
   separated** for compatibility (Trail of Bits convention).
2. Trigger-eval cost: not directly documented. 20 queries × 3 runs ×
   5 iterations × 7 skills ≈ 2100 `claude -p` invocations. Each is short.
   Total LLM cost ~$5-10 with stronger model.
3. Whether the homework rubric scores trigger eval pass-rate as a hard
   number or only narrative. Default: report both train and test pass rates
   per iteration + before/after descriptions.

---

## Citations (raw GitHub URLs)

- `anthropics/skills/skills/skill-creator/SKILL.md`
- `anthropics/skills/skills/skill-creator/scripts/run_loop.py`
- `anthropics/skills/skills/skill-creator/scripts/run_eval.py`
- `anthropics/skills/skills/skill-creator/scripts/improve_description.py`
- `anthropics/skills/skills/{pdf,pptx,xlsx,docx,...}/SKILL.md`
- `obra/superpowers/skills/{brainstorming,test-driven-development,...}/SKILL.md`
- `trailofbits/skills/plugins/{agentic-actions-auditor,...}/skills/.../SKILL.md`
- https://code.claude.com/docs/en/skills
- https://platform.claude.com/docs/en/agents-and-tools/agent-skills/{overview,best-practices}
- https://agentskills.io/specification
- https://kaochenlong.com/claude-code-skills (cited by interview brief)
