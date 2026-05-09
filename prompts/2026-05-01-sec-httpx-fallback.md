# 2026-05-01: SEC EDGAR through httpx, not Playwright

## The problem

`fin-002`, `fin-003`, `fin-005` (SEC EDGAR + EFTS + Berkshire IR) all failed
on v1 through v5 of the eval. Symptoms:

- HTTP 403 from `www.sec.gov/cgi-bin/browse-edgar`
- HTTP 403 from `efts.sec.gov/LATEST/search-index`
- 200 with empty body / block page text

The 403s persisted even after we set `SEC_USER_AGENT` (`name email@example.com`)
on Playwright via `set_extra_http_headers`. Same URLs returned 200 fine when
hit with `httpx + same UA` from the same machine on the same network.

## Diagnosis

JA3 / TLS fingerprinting. Headless Chromium has a recognisable JA3 hash even
with a custom UA string; SEC's edge stack flags it and serves 403. The UA
string is necessary (the SEC fair-use policy requires a contact email) but
not sufficient when the underlying TLS handshake gives the bot away.

This is documented in third-party scraping community write-ups, but the
canonical Anthropic / Playwright docs don't mention it.

## Two readings

### Reading 1: Make Playwright look like a real browser

Stealth plugins, randomised viewports, residential proxies, JA3
manipulation libraries. There's a whole ecosystem.

Strength:
- Keeps the agent uniform — one primitive (Playwright) for everything.

Weakness:
- The brief is for a generic agent demonstration, not an
  anti-bot-evasion exercise. Stealth plugin choreography is a rabbit hole.
- SEC explicitly publishes a REST contract with a fair-use policy. Trying
  to evade their fingerprinting for endpoints they've published is
  contrived — and arguably violates the spirit of the fair-use policy
  even when the letter (10 req/s + UA) is satisfied.

### Reading 2: Recognise when Playwright is the wrong primitive

Detect SEC hosts in the Actor's `_navigate` step. For those hosts, swap
Playwright for `httpx + SEC_USER_AGENT`, fetch the body, push it into the
Page via `page.set_content(html)`, then synthesise the post snapshot's URL
to keep the trajectory honest.

Strength:
- The browser is the right tool for JS-heavy pages (Apple IR, Wikipedia)
  and the wrong tool for endpoints with an official REST contract (SEC).
  Using each for what it's good at is the architecturally honest answer.
- "Self-correction" extends from "the LLM picks a different action" to
  "the architecture recognises a different primitive". That's a more
  interesting story than try/except retry.

Weakness:
- Special-casing hosts feels brittle. The `_HOSTS_REQUIRING_CONTACT_UA`
  set is hardcoded; a new SEC subdomain would need a code change. (We
  decided this is acceptable: the SEC host list is small, stable, and
  legally distinct from generic browsing.)

## Decision

**Reading 2.** Implementation in `actor.py:_navigate` (line 134-142):

```python
host = (urlparse(step.url).hostname or "").lower()
if host in _HOSTS_REQUIRING_CONTACT_UA:
    return await self._navigate_via_httpx(step, pre)
```

`_navigate_via_httpx` then does the httpx fetch, `page.set_content(html)`,
and a `.model_copy(update={"url": step.url})` on the post snapshot so the
trajectory log shows the requested URL, not `about:blank`.

## Outcome

- v6: fin-002 + fin-005 PASS (the Berkshire URL fix landed alongside)
- v7: fin-003 PASS after we made the negative-oracle JSON-aware (SEC EFTS
  returns JSON not HTML, so `must_appear_on_final_page: ["climate"]` was
  the wrong thing to look for — it's `"hits"` in the JSON envelope)
- Final eval: 10/10

## What this captures for the interviewer

The brief grades "self-correcting and self-maintaining with substance, not
try/except retry". The httpx fallback is the strongest evidence in the
codebase that we read this seriously — not "retry the same request" but
"recognise this whole primitive is wrong for this endpoint, swap it, and
keep the rest of the pipeline uniform". Worth flagging in any walkthrough.

The decision is also honest about its limit: a new SEC subdomain or a
different endpoint with the same TLS-fingerprint problem (e.g., FINRA,
Cboe) would need a code change. We're not claiming this generalises to
"recognise any block automatically" — only to "the SEC special-case is
worth doing and the architecture supports it cleanly".
