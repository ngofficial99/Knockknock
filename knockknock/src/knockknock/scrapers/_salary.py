"""Best-effort salary extraction from free-text job descriptions.

Used as a *soft signal* — when extraction fails we return ``None`` rather
than guessing. Callers should retain the raw matched substring in
``salary_raw`` so the audit harness can sanity-check the extractor.

Supported shapes (non-exhaustive):

* ``$150k``, ``$150,000``, ``$150-200k``, ``$150,000-$200,000``
* ``120-160k USD``, ``USD 120k-160k``, ``€80-120k``, ``£70-95k``
* ``₹40-60 LPA``, ``INR 40-60L``, ``Rs 40L``, ``40-60 LPA``
* hourly: ``$50-75/hr``, ``$60/hour`` (period=``hourly``)
* monthly: ``$8000/month``, ``₹3L/month`` (period=``monthly``)

We deliberately do NOT match bare numbers without a currency hint to keep
false-positive rate low. Equity-only / "competitive" / "DOE" return ``None``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SalaryGuess:
    """Result of a salary-extraction attempt.

    ``min_amount`` and ``max_amount`` are in the *minor* currency unit
    expressed in the source (e.g. dollars, rupees) — NOT cents. We keep
    them as integers because we don't need decimal precision for filter
    or scoring decisions.

    ``period`` is one of ``"annual"`` / ``"monthly"`` / ``"hourly"``.
    Indian "LPA" (lakhs per annum) is always annual.
    """

    min_amount: int
    max_amount: int
    currency: str  # ISO-ish: "USD" / "INR" / "EUR" / "GBP"
    period: str  # "annual" | "monthly" | "hourly"
    raw: str  # the matched substring, kept verbatim for auditing


# Currency token (symbol or 3-letter code). Order matters: longer first.
_CCY_SYM = {
    "$": "USD",
    "US$": "USD",
    "USD": "USD",
    "€": "EUR",
    "EUR": "EUR",
    "£": "GBP",
    "GBP": "GBP",
    "₹": "INR",
    "INR": "INR",
    "Rs.": "INR",
    "Rs": "INR",
    "RS": "INR",
}

# Range vs single-value.
# Layout: <ccy1>? <n1><s1>? (- <ccy>? <n2><s2>?)?  <ccy2>?  <period>?
# Notes:
#   * period token captured separately so LPA / /hr / /month attach cleanly
#   * "LPA" is captured as period because it doubles as currency-marker
#     (handled in parse_salary_from_text by upgrading to INR when seen)
# NOTE on the `L` suffix and "LPA":
# A bare `L` after a number can mean "lakhs" (Indian shorthand). But "LPA"
# is its own token meaning "lakhs per annum" and we want to capture that
# in the period slot so we can flip to INR even when no other currency hint
# is present. Use negative-lookahead `(?![Pp][Aa])` on the L suffix so the
# `L` in `LPA` is not consumed by `s1`/`s2`, leaving `LPA` for the period
# slot to capture.
_RANGE_RE = re.compile(
    r"(?P<ccy1>US\$|\$|€|£|₹|USD|EUR|GBP|INR|Rs\.?|RS)?\s*"
    r"(?P<n1>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
    r"(?P<s1>[kKmM]|[Ll](?![Pp][Aa]))?"
    r"(?:\s*[-\u2013]\s*(?:US\$|\$|€|£|₹|USD|EUR|GBP|INR|Rs\.?|RS)?\s*"
    r"(?P<n2>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
    r"(?P<s2>[kKmM]|[Ll](?![Pp][Aa]))?)?"
    r"(?:\s*(?P<ccy2>USD|EUR|GBP|INR))?"
    r"(?P<period>\s*(?:/hr|/hour|/yr|/year|/mo|/month|LPA|lpa))?",
    re.UNICODE,
)


def _to_int(num_str: str, suffix: str | None) -> int | None:
    """Convert ``"150"`` / ``"150,000"`` / ``"1.5"`` plus suffix into int.

    Returns ``None`` if the parsed value is implausibly low (< 1000 minor
    units for annual salaries — that's the filter against matching things
    like "we have 5 years experience").
    """
    try:
        raw = float(num_str.replace(",", ""))
    except ValueError:
        return None
    if suffix in ("k", "K"):
        raw *= 1_000
    elif suffix in ("m", "M"):
        raw *= 1_000_000
    elif suffix in ("L", "l"):  # Indian lakhs
        raw *= 100_000
    return int(raw)


def _looks_like_salary(value: int, period: str) -> bool:
    """Reject obviously-not-salary numbers (years of experience etc.)."""
    if period == "hourly":
        return 5 <= value <= 5_000  # $5/hr to $5000/hr is the plausibility envelope
    if period == "monthly":
        return 1_000 <= value <= 5_000_000  # ₹1k to ₹50L monthly
    # annual
    return 10_000 <= value <= 100_000_000  # $10k to $100M annual


def parse_salary_from_text(text: str) -> SalaryGuess | None:
    """Best-effort salary extractor. Returns ``None`` on uncertainty.

    We scan for the first plausible match. If multiple candidates exist
    in one comment we prefer the one with an explicit currency token,
    because those have the lowest false-positive rate.
    """
    if not text:
        return None

    candidates: list[SalaryGuess] = []
    for m in _RANGE_RE.finditer(text):
        ccy_token = (m.group("ccy1") or m.group("ccy2") or "").strip()
        period_token = (m.group("period") or "").strip().lower()
        # "LPA" in the period slot doubles as a currency-marker → upgrade to INR
        is_lpa = period_token in ("lpa",) or ccy_token in ("LPA", "lpa")
        currency = "INR" if is_lpa else _CCY_SYM.get(ccy_token, "")
        if not currency:
            # No currency hint at all — too risky, skip
            continue

        n1 = m.group("n1")
        s1 = m.group("s1")
        n2 = m.group("n2")
        s2 = m.group("s2")

        # Suffix inheritance: "$100-140k" gives s1=None, s2=k. The whole
        # range shares the suffix, so propagate s2 onto s1 (and vice-versa).
        if s1 is None and s2 is not None:
            s1 = s2
        if s2 is None and s1 is not None:
            s2 = s1
        # For LPA we treat unsuffixed numbers as lakhs even when no "L"
        # appears, because "40-60 LPA" is idiomatic shorthand.
        if is_lpa:
            if s1 is None:
                s1 = "L"
            if s2 is None:
                s2 = "L"

        min_amount = _to_int(n1, s1)
        if min_amount is None:
            continue

        if n2:
            max_amount = _to_int(n2, s2)
            if max_amount is None:
                max_amount = min_amount
        else:
            max_amount = min_amount

        if period_token in ("/hr", "/hour"):
            period = "hourly"
        elif period_token in ("/mo", "/month"):
            period = "monthly"
        else:
            period = "annual"

        if not _looks_like_salary(min_amount, period):
            continue
        if max_amount < min_amount:
            max_amount = min_amount

        raw_match = m.group(0).strip()
        candidates.append(
            SalaryGuess(
                min_amount=min_amount,
                max_amount=max_amount,
                currency=currency,
                period=period,
                raw=raw_match,
            )
        )

    if not candidates:
        return None
    # Prefer the first candidate (earliest in the text) — usually the most
    # explicit one in HN comments where salary leads the description.
    return candidates[0]
