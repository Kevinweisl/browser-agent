"""Silent-failure detection cascade.

Order is cheap → expensive (browser-agent research §5):

    Layer 0: page-content failure markers  (cheap text scan — Wikipedia 404,
             "page not found" pages return HTTP 200 with valid URL but
             semantically failed content; navigation looks successful at the
             HTTP layer but is broken)
    Layer 1: ariaSnapshot diff       (cheap, semantically meaningful)
    Layer 2: URL + page title diff   (cheap)
    Layer 3: network response check  (cheap, requires page tracing)
    Layer 4: healed-selector log     (free; flags drift for audit)
    Layer 5: LLM trajectory verifier (expensive, only when 1-3 say "no change"
             on a step that was supposed to mutate state)

The validator module owns Layer 5; this module owns Layers 0-4 and exposes
their results as a list of signal strings the validator consumes. handlers.py
short-circuits to REPLAN when Layer 0 fires — no point asking an LLM whether
"this is the article you were looking for" when the page itself says it
isn't.
"""

from __future__ import annotations

import difflib

from .schema import ActionType, PageSnapshot, Step

# Action types that SHOULD mutate visible state. If none of layers 1-3 detect
# any change after one of these, the step is suspicious.
_MUTATING_ACTIONS = {ActionType.CLICK, ActionType.TYPE, ActionType.SELECT,
                     ActionType.NAVIGATE}


# Layer 0 — page-content failure markers. A page that returns HTTP 200 with
# valid URL but contains one of these strings has semantically failed:
# Wikipedia's "no such article" template, generic 404 pages, soft-blocked
# error views. The agent must NOT declare victory on these. List is
# substring-matched case-insensitive on the post snapshot's visible text.
#
# Adding a new indicator: keep them short, distinctive, and rare enough that
# they don't collide with legitimate page content (e.g. "Search results for"
# on a results page contains "results" but never these phrases).
_CONTENT_FAILURE_MARKERS: tuple[str, ...] = (
    "does not have an article with this exact name",  # Wikipedia 404 template
    "page not found",
    "404 not found",
    "this page does not exist",
    "the requested page could not be found",
    "no such page",
    "you don't have permission",          # generic 403-ish soft block
    "access denied",
    "service unavailable",
)


def detect_content_failure(post: PageSnapshot) -> list[str]:
    """Layer 0: scan post.text_excerpt for canonical "page failed to deliver
    content" markers. Returns list of matched markers (empty if page looks
    fine). Cheap — bounded substring search on already-truncated 20K excerpt.
    """
    if not post.text_excerpt:
        return []
    text_lo = post.text_excerpt.lower()
    return [m for m in _CONTENT_FAILURE_MARKERS if m in text_lo]


def is_content_failed(signals: list[str]) -> bool:
    """Did Layer 0 trigger? handlers uses this to short-circuit to REPLAN
    without spending an LLM validator call on a page the page itself
    declares broken."""
    return any(s.startswith("page_content_failed:") for s in signals)


def aria_snapshot_diff(pre: PageSnapshot, post: PageSnapshot) -> str | None:
    """Return a unified diff if the ariaSnapshot changed, else None.

    Empty pre or post strings are treated as "snapshot disabled" and skip
    this layer (no signal either way).
    """
    if not pre.aria_snapshot or not post.aria_snapshot:
        return None
    if pre.aria_snapshot == post.aria_snapshot:
        return ""  # explicitly "no change"
    diff = difflib.unified_diff(
        pre.aria_snapshot.splitlines(),
        post.aria_snapshot.splitlines(),
        lineterm="", n=1,
    )
    return "\n".join(diff)


def url_changed(pre: PageSnapshot, post: PageSnapshot) -> bool:
    return pre.url != post.url


def title_changed(pre: PageSnapshot, post: PageSnapshot) -> bool:
    return pre.title != post.title


def dom_changed(pre: PageSnapshot, post: PageSnapshot) -> bool:
    return pre.dom_hash != post.dom_hash


def collect_signals(step: Step, pre: PageSnapshot, post: PageSnapshot) -> list[str]:
    """Return human-readable signals about whether the step caused state change.

    Each signal is one of:
      - "page_content_failed:<marker>"  ← Layer 0; one signal per matched marker
      - "url_changed"
      - "title_changed"
      - "dom_changed"
      - "aria_snapshot_changed"
      - "no_visible_state_change_after_mutating_action"  ← suspicious
      - "no_visible_state_change_after_passive_action"   ← informational
    """
    signals: list[str] = []
    # Layer 0 — page-content failure (catches "structurally succeeded but
    # semantically broken" cases like Wikipedia 404 / soft-block pages).
    for marker in detect_content_failure(post):
        signals.append(f"page_content_failed:{marker}")
    if url_changed(pre, post):
        signals.append("url_changed")
    if title_changed(pre, post):
        signals.append("title_changed")
    if dom_changed(pre, post):
        signals.append("dom_changed")
    aria_diff = aria_snapshot_diff(pre, post)
    if aria_diff and aria_diff != "":
        signals.append("aria_snapshot_changed")

    any_change = any(s.endswith("_changed") for s in signals)
    if not any_change:
        if step.action_type in _MUTATING_ACTIONS:
            signals.append("no_visible_state_change_after_mutating_action")
        else:
            signals.append("no_visible_state_change_after_passive_action")
    return signals


def negative_oracle_violations(step: Step, post: PageSnapshot) -> list[str]:
    """Check the step's per-step success_criteria against the post snapshot."""
    violations: list[str] = []
    text_lower = post.text_excerpt.lower()
    for must in step.success_criteria.must_appear:
        if must.lower() not in text_lower:
            violations.append(f"missing must_appear: {must!r}")
    for must_not in step.success_criteria.must_not_appear:
        if must_not.lower() in text_lower:
            violations.append(f"present must_not_appear: {must_not!r}")
    if step.success_criteria.url_must_change and post.url == "":
        violations.append("url_must_change but post URL empty")
    return violations


def is_suspicious(signals: list[str]) -> bool:
    """Should the validator escalate to LLM trajectory verifier?"""
    return "no_visible_state_change_after_mutating_action" in signals
