"""Silent-failure detection cascade.

Order is cheap → expensive (browser-agent research §5):

    Layer 1: ariaSnapshot diff       (cheap, semantically meaningful)
    Layer 2: URL + page title diff   (cheap)
    Layer 3: network response check  (cheap, requires page tracing)
    Layer 4: healed-selector log     (free; flags drift for audit)
    Layer 5: LLM trajectory verifier (expensive, only when 1-3 say "no change"
             on a step that was supposed to mutate state)

The validator module owns Layer 5; this module owns Layers 1-4 and exposes
their results as a list of signal strings the validator consumes.
"""

from __future__ import annotations

import difflib

from .schema import ActionType, PageSnapshot, Step

# Action types that SHOULD mutate visible state. If none of layers 1-3 detect
# any change after one of these, the step is suspicious.
_MUTATING_ACTIONS = {ActionType.CLICK, ActionType.TYPE, ActionType.SELECT,
                     ActionType.NAVIGATE}


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
      - "url_changed"
      - "title_changed"
      - "dom_changed"
      - "aria_snapshot_changed"
      - "no_visible_state_change_after_mutating_action"  ← suspicious
      - "no_visible_state_change_after_passive_action"   ← informational
    """
    signals: list[str] = []
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
