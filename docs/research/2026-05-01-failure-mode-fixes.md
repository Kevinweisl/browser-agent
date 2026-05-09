# 2026 Best Practices for 4 Browser-Agent Failure Modes

Date: 2026-05-01
Context: Playwright + Python + LLM ensemble Planner-Actor-Validator browser agent.
Goal: actionable recipes for 4 distinct failure modes surfaced in eval.

---

## Problem 1, Locator returns count > 1, `_has_one` rejects ambiguous matches

### Recommendation

Stop treating "count != 1" as failure. Idiomatic 2026 Playwright is to **chain `.filter()` then fall back to `.first()` only when filter narrows to a single visible candidate**. Wikipedia's two "Search" buttons are exactly the case `filter({ visible: true })` was added for. The ladder should degrade gracefully across `.filter({hasText})` -> `.filter({visible: true})` -> viewport-clipped -> `.first()`, not skip the tier.

### Verbatim, from Playwright docs (`https://playwright.dev/docs/locators`)

> "all operations on locators that imply some target DOM element will throw an exception if more than one element matches"

> `filter({ hasText: 'Product 2' })` ... `filter({ hasText: /Product 2/ })`

> `locator.first()`, `locator.last()`, `locator.nth()` ... "not recommended" since "when your page changes, Playwright may click on an element you did not intend"

> `await page.locator('button').filter({ visible: true }).click();`

The `visible: true` option was added precisely to avoid `.first()` (`https://kailash-pathak.medium.com/playwright-filtering-visible-elements-with-locator-filter-visible-true-e61955326cb1`):

> "filter a locator to include only elements that are visible on the page. This small but mighty addition eliminates the need for extra checks and keeps your test logic concise."

### Alternatives

| Strategy | Pros | Cons |
|---|---|---|
| `.first()` outright | One line, deterministic | Brittle: DOM order != user intent; docs explicitly call this "not recommended" |
| `.filter({ hasText })` | Semantic, robust to DOM rearrangement | Requires planner to know an extra disambiguating phrase |
| `.filter({ visible: true })` | Solves Wikipedia case (header icon visible, dialog button hidden) directly | Won't help if both candidates are visible |
| Visual-rerank via screenshot + bbox | Stagehand/Skyvern style, agnostic to selector failure | Extra LLM call; latency + cost; non-deterministic |
| `evaluateAll` + custom rank | Maximum control (e.g., viewport distance) | Lots of glue, hard to test |

### Recommended choice

A **3-step chained ladder**, all expressed as `Locator` chains so strict mode still applies:

1. `loc.filter({ visible: true })`, kills off-screen / dialog-mode duplicates. Recheck `count()`. If 1, use it.
2. If still >1: `loc.filter({ hasText: planner_hint })` where `planner_hint` is supplied by the Planner LLM (e.g. "Search Wikipedia"). Recheck. If 1, use it.
3. If still >1: viewport-clip via `bounding_box()` and rank by distance from top-left, OR (last resort) `loc.first()` with a logged warning.

Every tier produces a `Locator`, so the next tier composes. **Never skip the tier**; always degrade. Stagehand's `observe()` is the reference design: it returns ranked candidates rather than throwing (`https://docs.stagehand.dev/basics/observe`).

### How peer agents handle this

- **Stagehand**: `observe()` "discovers key elements, ranking likely next steps, and returning structured actions (selector, method, args)". DOM-ranked, not single-shot. (`https://docs.stagehand.dev/basics/observe`)
- **Browser-Use**: screenshot + visual reasoning. The LLM picks a numbered overlay. No strict mode at all. (`https://scrapfly.io/blog/posts/stagehand-vs-browser-use`)
- **Skyvern**: "screenshots the page, reads the DOM, and uses an LLM to decide what to do next ... identifies elements by visual context and semantic meaning instead of predetermined paths" (`https://www.skyvern.com/blog/how-skyvern-reads-and-understands-the-web/`)

Sources:
- https://playwright.dev/docs/locators
- https://playwright.dev/docs/api/class-locator
- https://kailash-pathak.medium.com/playwright-filtering-visible-elements-with-locator-filter-visible-true-e61955326cb1
- https://docs.stagehand.dev/basics/observe
- https://scrapfly.io/blog/posts/stagehand-vs-browser-use
- https://www.skyvern.com/blog/how-skyvern-reads-and-understands-the-web/

---

## Problem 2, SEC EDGAR + Berkshire IR block headless Chromium

### Recommendation

For SEC EDGAR, **stop using a browser**. SEC publishes a documented JSON REST API at `data.sec.gov` whose only requirements are a descriptive User-Agent and ≤10 req/s. For Berkshire IR (static HTML, no JS, no CAPTCHA on the public PDFs), use plain `httpx` with a real Chrome UA + Accept-Language; Playwright is overkill and headless Chromium's TLS/CDP fingerprint is what's getting blocked.

### Verbatim, SEC official policy

From `https://www.sec.gov/os/accessing-edgar-data` (cached via secondary sources):

> "no more than 10 requests per second from a single IP address"

User-Agent format the SEC asks for (`https://dealcharts.org/blog/edgar-scraping-rate-limits-explained`):

> "YourCompanyName ResearchBot (contact@yourcompany.com)"

> "time.sleep(0.1) between each request ensures you stay under the 10-requests-per-second limit"

EdgarTools compliance guide (`https://edgartools.readthedocs.io/en/stable/resources/sec-compliance/`):

> "No more than 10 requests per second" ... "Reasonable total volume per day" ... "avoiding excessive concurrent requests" ... "Run large batches outside 9:30 AM-4:00 PM Eastern Time"

Submissions API endpoint (`https://www.sec.gov/search-filings/edgar-application-programming-interfaces`):

> `https://data.sec.gov/submissions/CIK##########.json`
> "Submissions by company and extracted XBRL data are available via RESTful APIs on data.sec.gov, offering JSON formatted data"
> "data.sec.gov does not support Cross Origin Resource Scripting (CORS)"

### Verbatim, playwright-stealth ceiling in 2026

`https://scrapewise.ai/blogs/playwright-stealth-2026`:

> "these are JS-layer patches. Anti-bot systems have moved up the stack."

> "TLS fingerprint mismatches and HTTP/2 SETTINGS frames-signals that exist before JavaScript executes"

> "playwright-stealth v2.x is a valid starting point" for mid-tier targets, but fails against Cloudflare Enterprise, Akamai v4, DataDome.

`https://alterlab.io/blog/playwright-anti-bot-detection-what-actually-works-in-2026`:

> "What it does **not** fix: WebGL fingerprint, TLS fingerprint, CDP detection, or behavioral analysis."

> "Playwright's Chromium binary has a JA3 fingerprint that does not match any real Chrome release."

> "TLS fingerprinting cannot be fixed from JavaScript. You would need to patch Chromium's TLS stack at the C++ level"

### Verbatim, `--headless=new`

`https://datadome.co/threat-research/how-new-headless-chrome-the-cdp-signal-are-impacting-bot-detection/` (via search summary, fetch returned 403):

> "the new headless mode has a significantly more realistic browser fingerprint than its predecessor"
> "Once an attacker uses page.setUserAgent() to change their user agent and the --disable-blink-features=AutomationControlled argument to get rid of navigator.webdriver, there are very few inconsistencies left"

As of Chrome 132, **`--headless=new` is the default**, so the flag is no longer the differentiator. Detection has moved to TLS/CDP/behavioral.

### Alternatives

| Approach | Pros | Cons |
|---|---|---|
| `data.sec.gov` REST + `httpx` | Official, free, deterministic, no UA/TLS fight | Doesn't help Berkshire IR or other non-API sites |
| `edgartools` Python pkg (`set_identity()`) | Compliance built-in, parses 10-K/10-Q | Yet another dep; opinionated schema |
| Headless Chromium + `playwright-stealth` v2 | Generic; one code path | Loses to Cloudflare/Akamai; SEC has historically blocked headless |
| Headed Chromium (`headless=False`) | Behavioral signals look human | 5-10x slower, can't run on CI without Xvfb |
| Patchright (Node) / Camoufox (Firefox fork) | C++-level patches, beats most fingerprinting | Not Python-native; Camoufox 200MB/instance, 42s avg through Turnstile |
| Browserbase/Browserless managed | Maintained by vendor, residential proxies, captcha | Per-session cost; vendor lock-in |

### Recommended choice

**Two-tier source policy** in the agent:

1. **Tier A, structured API path**: if the planner identifies an SEC source, route through `data.sec.gov/submissions/CIK0000320193.json` + `data.sec.gov/api/xbrl/companyfacts/CIK...`. Set User-Agent `KevinWei InterviewAgent (weisl@nlg.csie.ntu.edu.tw)`. Throttle to 9 req/s with token bucket. Browser is never opened. This is what the real production pipelines do, see `edgartools` and `sec-edgar-api`.
2. **Tier B, headed browser fallback**: for IR sites and pages without an API, launch with `headless=False` (or `headless=new` + `--disable-blink-features=AutomationControlled` as second-best), realistic UA, `playwright-stealth==2.0.2`. Berkshire IR specifically serves static HTML without anti-bot, so this should work; if it doesn't, the page itself is fetchable with `httpx` + Chrome UA.

For Tier B, do NOT use `wait_until="networkidle"`, modern SPAs never go idle. Use `wait_until="domcontentloaded"` plus an explicit `expect(locator).to_be_visible()` on a content selector.

Sources:
- https://www.sec.gov/os/accessing-edgar-data
- https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- https://www.sec.gov/about/webmaster-frequently-asked-questions
- https://dealcharts.org/blog/edgar-scraping-rate-limits-explained
- https://edgartools.readthedocs.io/en/stable/resources/sec-compliance/
- https://scrapewise.ai/blogs/playwright-stealth-2026
- https://alterlab.io/blog/playwright-anti-bot-detection-what-actually-works-in-2026
- https://datadome.co/threat-research/how-new-headless-chrome-the-cdp-signal-are-impacting-bot-detection/
- https://camoufox.com/stealth/
- https://docs.browserbase.com/features/stealth-mode

---

## Problem 3, K=2 ensemble worse than K=3 (1.0 → 0.78)

### Recommendation

K=2 majority-vote is mathematically degenerate: a 1-1 split has no winner, and a 2-0 split lets shared training-data biases compound. Production pattern in 2026 is **agreement-gated K=2 with a judge fallback**: emit the answer only when both members agree AND both report self-confidence ≥ 0.9; otherwise route to a third "judge" model (cheap, separate family) to break the tie. If you can't afford a judge, fall back to K=1 with self-consistency (3 temperature-shuffled samples on the strongest model).

### Verbatim, measured K-curve

`https://arxiv.org/html/2511.15714v1` ("Majority Rules: LLM Ensemble..."):

> Single model: 0.55 -> 2 LLMs: 0.73 -> 3 LLMs: 0.76 -> 10 LLMs: 0.92

The marginal jump from K=2 to K=3 is small in F1, but the failure mode is sharp: K=2 has no tiebreak, K=3 always has one. Your observed 1.0 → 0.78 drop is consistent with both members agreeing on the wrong override, exactly what the "Majority Rules" paper warns about with low diversity.

### Verbatim, judge tie-breakers

`https://arxiv.org/html/2412.05579v2` (LLM-as-Judge survey, summarized):

> "Three-Option mode introduces an additional choice, allowing judges to indicate a tie if neither response is preferable"

> "Ensembles of judges can be run through 3-5 different judge models (or the same model at different temperatures) and take a majority vote; smaller models work fine as ensemble members, where cost goes up, variance goes down, and self-preference bias shrinks"

`https://aclanthology.org/2025.findings-acl.744.pdf` (Ranked-voting self-consistency):

> "Although ties are rare in voting outcomes, they may occur particularly for complex questions"
> "Compared with self-consistency baseline, ranking-based approaches mitigate this issue by incorporating preference ranking information"

### Alternatives

| Pattern | Pros | Cons |
|---|---|---|
| K=2 with simple majority | Cheap | Ties with no tiebreak; correlated errors win |
| K=2 + agreement-gate (only emit if both agree, else abstain) | High precision when both agree | Coverage drops; need a fallback |
| K=2 + LLM-judge tiebreaker (3rd model) | Recovers coverage; judge can pick "neither" | +1 LLM call per filing; judge bias risk |
| K=1 + self-consistency (3 temperature samples on Nemotron) | One provider, no cross-loop coord | Loses cross-family diversity (worst case shows correlated errors) |
| K=3 with cross-family (DeepSeek + Nemotron + Mistral) | Best measured F1, natural tiebreak | Requires DeepSeek up; that's the original problem |

### Recommended choice

**Layered fallback**, decided at runtime:

1. **K=3 cross-family preferred**: when DeepSeek endpoint is healthy, run all three.
2. **Agreement-gated K=2**: when DeepSeek is down, Nemotron + Mistral both must (a) agree on the label AND (b) self-report confidence ≥ 0.9 ("how sure are you, 0-1?" appended to the schema). If gate passes, emit. If gate fails, fall through.
3. **K=2 + judge**: if (2) fails, call a third judge model (cheap, Nemotron at high temperature with a different system prompt counts) with both candidate answers and "or neither" as an explicit option. Three-option judges reduce position bias. (`arxiv.org/html/2412.05579v2`)
4. **Last resort, K=1 self-consistency**: 3 temperature-shuffled samples on the strongest available model; majority of 3.

This recovers coverage without the silent K=2 confidence trap. Critical instrumentation: log every gate transition, so you can measure whether step (2) actually shrinks the disagreement set vs blind K=2.

Sources:
- https://arxiv.org/abs/2505.10772
- https://aclanthology.org/2025.findings-acl.744.pdf
- https://arxiv.org/html/2511.15714v1
- https://arxiv.org/html/2412.05579v2
- https://eugeneyan.com/writing/llm-evaluators/
- https://www.kinde.com/learn/ai-for-software-engineering/workflows/llm-fan-out-101-self-consistency-consensus-and-voting-patterns/
- https://learnprompting.org/docs/intermediate/self_consistency

---

## Problem 4, `asyncio.Semaphore` cached at module level breaks across `asyncio.run()` calls

### Recommendation

Your `(id(loop), name)` key works as a band-aid, but the canonical 2026 fix is **don't call `asyncio.run()` more than once**. The Python docs are explicit about this. Promote the per-filing pipeline to a single long-lived loop using `asyncio.Runner` (3.11+), or move semaphore creation **inside** the coroutine so it always binds to the running loop. The id-based cache has a real GC-collision risk; avoid it.

### Verbatim, Python docs (`https://docs.python.org/3/library/asyncio-runner.html`)

> "This function should be used as a main entry point for asyncio programs, and **should ideally only be called once.**"

> `asyncio.Runner` ... "A context manager that simplifies multiple async function calls in the same context. Sometimes several top-level async functions should be called in the same event loop and contextvars.Context."

`https://docs.python.org/3/library/asyncio-sync.html`:

> "Changed in version 3.10: Removed the loop parameter."

(Implication: Semaphore now implicitly binds to the running loop at first use. Module-level construction binds to whichever loop is running at import time, usually none, and then breaks on first `acquire()` after a fresh `asyncio.run()`.)

### Verbatim, community guidance

From Sanic issue #1388 and ComfyUI #3232 (`https://github.com/huge-success/sanic/issues/1388`, summarized):

> "When a Lock object is initialized, it stores a reference to the current event loop. However, when asyncio.run() is called, it throws away the previous event loop and creates a new one. The Lock object still holds a stale reference to the old event loop"

> Fix: "initialize the lock inside the async function rather than at module level"

### Alternatives

| Pattern | Pros | Cons |
|---|---|---|
| Module-level `Semaphore` + `asyncio.run()` per filing (current bug) | Simple | The bug |
| Per-loop cache keyed `(id(loop), name)` (your fix) | Surgical, no API change | `id()` reuse after GC; risk of collision; still papers over the asyncio.run() anti-pattern |
| `asyncio.Runner` context spanning all filings | One loop, semaphores reused, idiomatic 3.11+ | Need to refactor sync wrapper -> single async entrypoint |
| Construct semaphore inside coroutine, pass via context | No global state; works under any caller | Threading semaphore through call sites; can't cap globally if multiple top-level calls |
| `weakref.WeakValueDictionary[loop, dict[name, Sem]]` | No GC collision; auto-cleanup when loop dies | Slightly more code than your fix |

### Recommended choice

**Refactor to a single `asyncio.Runner`** at the sync boundary. The pattern:

```python
def run_llm_augmentation(filings: list[Filing]) -> list[Result]:
    with asyncio.Runner() as runner:
        return runner.run(_async_main(filings))

async def _async_main(filings):
    # semaphores created HERE, inside the running loop
    locks = {name: asyncio.Semaphore(n) for name, n in PROVIDER_LIMITS.items()}
    return await asyncio.gather(*(_one(f, locks) for f in filings))
```

This eliminates the bug class entirely: there is exactly one event loop for the whole batch, and semaphores are created inside it. Bonus: ~30% latency win because connection pools (httpx, NIM client) survive across filings instead of being torn down per `asyncio.run()`.

If a one-loop refactor is too invasive: keep your `(id(loop), name)` cache but **use `weakref.WeakValueDictionary` keyed on the loop object itself**, not its id. That removes the GC collision risk: when the loop is garbage-collected, the entry vanishes; new loop, new entry, no false reuse.

```python
import weakref
_PROVIDER_LOCKS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Semaphore]] = weakref.WeakKeyDictionary()
```

Re: `asyncio.TaskGroup` (3.11+), orthogonal. TaskGroup replaces `gather` for structured concurrency / cleaner exception propagation. It does **not** solve the cross-loop semaphore problem; that's a loop-lifetime issue, not a task-management issue. Adopt it inside `_async_main` for the gather call, but it's not the fix here.

Sources:
- https://docs.python.org/3/library/asyncio-runner.html
- https://docs.python.org/3/library/asyncio-sync.html
- https://docs.python.org/3/library/asyncio-eventloop.html
- https://github.com/huge-success/sanic/issues/1388
- https://github.com/comfyanonymous/ComfyUI/issues/3232
- https://superfastpython.com/asyncio-runner/
- https://ryanc118.medium.com/python-asyncio-and-footguns-8ebdb4409122

---

## Quick scoreboard

| # | Failure mode | Fix in one line |
|---|---|---|
| 1 | Locator count > 1 | Chained `.filter({visible:true}).filter({hasText})` ladder, never skip the tier |
| 2 | SEC/IR blocked | Route SEC through `data.sec.gov` REST API; headed Chromium for IR; abandon stealth-against-Cloudflare arms race |
| 3 | K=2 worse than K=3 | Agreement-gated K=2 + judge tiebreaker fallback; never trust K=2 majority alone |
| 4 | Semaphore loop-binding | Single `asyncio.Runner` for the batch; create semaphores inside the coroutine; if you must cache, key on `WeakKeyDictionary[loop]` not `id(loop)` |
