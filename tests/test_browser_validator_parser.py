"""Tests for the validator's response parser (pure logic, no LLM)."""

from __future__ import annotations

import pytest

from workers.browser.schema import ValidatorDecision
from workers.browser.validator import _parse_validator


def test_parser_pass():
    raw = '{"decision": "pass", "reason": "URL changed and text matched"}'
    assert _parse_validator(raw) == ValidatorDecision.PASS


def test_parser_replan():
    raw = '{"decision": "replan", "reason": "no DOM change"}'
    assert _parse_validator(raw) == ValidatorDecision.REPLAN


def test_parser_abort():
    raw = '{"decision": "abort", "reason": "captcha"}'
    assert _parse_validator(raw) == ValidatorDecision.ABORT


def test_parser_strips_code_fence():
    raw = '```json\n{"decision": "pass", "reason": "ok"}\n```'
    assert _parse_validator(raw) == ValidatorDecision.PASS


def test_parser_extracts_from_prose():
    raw = ('Sure, here is my decision:\n'
           '{"decision": "abort", "reason": "blocked"}\n'
           'Hope this helps!')
    assert _parse_validator(raw) == ValidatorDecision.ABORT


def test_parser_rejects_unknown_decision():
    raw = '{"decision": "yes", "reason": "..."}'
    with pytest.raises(ValueError):
        _parse_validator(raw)


def test_parser_rejects_non_json():
    with pytest.raises(ValueError):
        _parse_validator("not even close")


def test_parser_case_insensitive():
    raw = '{"decision": "PASS", "reason": "ok"}'
    assert _parse_validator(raw) == ValidatorDecision.PASS
