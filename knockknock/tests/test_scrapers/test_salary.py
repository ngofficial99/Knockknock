"""Tests for the salary parser.

The parser is intentionally conservative: when in doubt, return ``None``.
We test both happy paths and the cases where we want non-detection.
"""

from __future__ import annotations

import pytest

from knockknock.scrapers._salary import parse_salary_from_text


@pytest.mark.parametrize(
    "text, expected_min, expected_max, expected_ccy, expected_period",
    [
        # USD ranges
        ("Compensation: $150k-200k base", 150_000, 200_000, "USD", "annual"),
        ("Pays $120,000 - $150,000 + equity", 120_000, 150_000, "USD", "annual"),
        ("$180k-$220k OTE", 180_000, 220_000, "USD", "annual"),
        ("Salary $130k", 130_000, 130_000, "USD", "annual"),
        # USD trailing-currency
        ("100-140k USD plus equity", 100_000, 140_000, "USD", "annual"),
        # EUR / GBP
        ("€80-120k base", 80_000, 120_000, "EUR", "annual"),
        ("£70k-£95k + benefits", 70_000, 95_000, "GBP", "annual"),
        # INR — LPA forms
        ("₹40-60 LPA fixed + ESOPs", 40 * 100_000, 60 * 100_000, "INR", "annual"),
        ("40-60 LPA", 40 * 100_000, 60 * 100_000, "INR", "annual"),
        ("INR 30L-50L per annum", 30 * 100_000, 50 * 100_000, "INR", "annual"),
        ("Rs 25L fixed", 25 * 100_000, 25 * 100_000, "INR", "annual"),
        # Hourly
        ("$50-75/hr contract", 50, 75, "USD", "hourly"),
        ("$80/hour for senior contractors", 80, 80, "USD", "hourly"),
        # Monthly
        ("$8000/month base", 8_000, 8_000, "USD", "monthly"),
    ],
)
def test_parser_extracts_expected_salary(
    text: str,
    expected_min: int,
    expected_max: int,
    expected_ccy: str,
    expected_period: str,
) -> None:
    result = parse_salary_from_text(text)
    assert result is not None, f"failed to parse: {text!r}"
    assert result.min_amount == expected_min
    assert result.max_amount == expected_max
    assert result.currency == expected_ccy
    assert result.period == expected_period
    # raw should contain the matched substring
    assert result.raw  # non-empty


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Competitive salary + equity",
        "DOE",
        "Salary depends on experience",
        # No currency hint at all — 5 years experience must NOT match as $5
        "Looking for engineers with 5 years experience",
        # Bare numbers without currency
        "Team of 50-100 engineers",
        # Implausibly low values rejected by plausibility filter
        "Office is at 123 Main St",
    ],
)
def test_parser_returns_none_for_non_salary_text(text: str) -> None:
    assert parse_salary_from_text(text) is None


def test_parser_picks_first_explicit_currency_match() -> None:
    """When multiple ranges appear, return the first explicit one."""
    text = "We are 50-100 people. Comp: $150k-200k. Some teams are 5-10 strong."
    result = parse_salary_from_text(text)
    assert result is not None
    assert result.min_amount == 150_000
    assert result.max_amount == 200_000
    assert result.currency == "USD"


def test_parser_handles_single_value_ranges() -> None:
    """A single number with currency should min==max."""
    result = parse_salary_from_text("Pays $180k")
    assert result is not None
    assert result.min_amount == 180_000
    assert result.max_amount == 180_000


def test_parser_normalises_max_below_min() -> None:
    """Defensive: if a malformed range parses with max < min, clamp."""
    # "$200k-150k" is exotic but let's not crash if we see it.
    result = parse_salary_from_text("$200k-150k")
    assert result is not None
    assert result.max_amount >= result.min_amount
