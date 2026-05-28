"""Unit tests for :func:`parse_score`.

The parser is the trust boundary between Gemini's untrusted text and our
typed :class:`JobScore`: it must tolerate the small amount of slop that
JSON-mode responses still leak (code fences, ragged whitespace) while
rejecting anything we can't safely persist (missing ``score``, non-JSON
bodies, non-integer scores).
"""

from __future__ import annotations

import pytest

from knockknock.scoring.parser import JobScore, parse_score


def test_parse_score_happy_path() -> None:
    raw = """{
        "score": 8,
        "rationale": "Strong python + distributed systems match.",
        "matched_must_have": ["Python","Go"],
        "matched_nice_to_have": ["Kafka","AWS"],
        "seniority_match": true,
        "company_stage_match": true
    }"""
    parsed = parse_score(raw)
    assert isinstance(parsed, JobScore)
    assert parsed.score == 8
    assert "Python" in parsed.matched_must_have
    assert "Kafka" in parsed.matched_nice_to_have
    assert parsed.seniority_match is True
    assert parsed.company_stage_match is True


def test_parse_score_clamps_out_of_range_high() -> None:
    raw = (
        '{"score": 11, "rationale": "x", '
        '"matched_must_have": [], "matched_nice_to_have": [], '
        '"seniority_match": false, "company_stage_match": false}'
    )
    parsed = parse_score(raw)
    assert parsed.score == 10  # clamped to upper bound


def test_parse_score_clamps_out_of_range_low() -> None:
    raw = (
        '{"score": 0, "rationale": "x", '
        '"matched_must_have": [], "matched_nice_to_have": [], '
        '"seniority_match": false, "company_stage_match": false}'
    )
    parsed = parse_score(raw)
    assert parsed.score == 1  # clamped to lower bound


def test_parse_score_rejects_non_json() -> None:
    with pytest.raises(ValueError, match="parse"):
        parse_score("not-json")


def test_parse_score_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="object"):
        parse_score("[1, 2, 3]")


def test_parse_score_rejects_missing_score() -> None:
    with pytest.raises(ValueError, match="score"):
        parse_score('{"rationale": "x"}')


def test_parse_score_rejects_non_integer_score() -> None:
    with pytest.raises(ValueError, match="integer"):
        parse_score('{"score": "high"}')


def test_parse_score_strips_code_fence() -> None:
    raw = (
        "```json\n"
        '    {"score": 5, "rationale": "ok", '
        '"matched_must_have": [], "matched_nice_to_have": [], '
        '"seniority_match": true, "company_stage_match": true}\n'
        "    ```"
    )
    parsed = parse_score(raw)
    assert parsed.score == 5


def test_parse_score_defaults_missing_lists_to_empty() -> None:
    """Optional fields are not required; missing → empty tuple."""
    raw = '{"score": 7, "rationale": "ok"}'
    parsed = parse_score(raw)
    assert parsed.matched_must_have == ()
    assert parsed.matched_nice_to_have == ()
    assert parsed.seniority_match is False
    assert parsed.company_stage_match is False
