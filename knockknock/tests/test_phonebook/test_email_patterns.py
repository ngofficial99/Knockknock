"""Unit tests for the deterministic email-pattern helpers.

These are the safety-net of the phonebook chain: after Apollo and Hunter
have failed, ``careers_fallback_email`` MUST always return a writable
recipient string so the enrich stage never leaves a job without a
contact. The fallback returns BOTH ``careers@<domain>`` AND
``hr@<domain>`` as a comma-joined list (``"careers@x, hr@x"``); the
drafter (Phase 8) splits on ``", "`` to populate Cc. This gives us two
shots at hitting a real inbox at small companies where one of the two
aliases may not even exist.

``guess_founder_email`` is best-effort -- returning ``None`` is fine;
returning something obviously wrong (single-token names, missing
domain) is not.
"""

from __future__ import annotations

import pytest

from knockknock.phonebook.email_patterns import (
    careers_fallback_email,
    guess_founder_email,
)


def test_careers_fallback_returns_comma_list_of_careers_and_hr() -> None:
    """Both aliases, comma-joined, careers first (preferred), then hr."""
    assert careers_fallback_email("acme.io") == "careers@acme.io, hr@acme.io"


def test_careers_fallback_lowercases_domain() -> None:
    assert careers_fallback_email("Acme.IO") == "careers@acme.io, hr@acme.io"


def test_careers_fallback_strips_scheme_and_www() -> None:
    assert (
        careers_fallback_email("https://www.example.com/") == "careers@example.com, hr@example.com"
    )


def test_careers_fallback_strips_path() -> None:
    assert careers_fallback_email("https://acme.io/jobs/123") == "careers@acme.io, hr@acme.io"


def test_careers_fallback_raises_on_empty_domain() -> None:
    with pytest.raises(ValueError, match="domain"):
        careers_fallback_email("")


def test_careers_fallback_raises_on_whitespace_only_domain() -> None:
    with pytest.raises(ValueError, match="domain"):
        careers_fallback_email("   ")


def test_guess_founder_email_first_last() -> None:
    assert guess_founder_email("Aarav Singh", "acme.io") == "aarav.singh@acme.io"


def test_guess_founder_email_single_token_returns_none() -> None:
    # "Madonna" -- single token, no reliable pattern guess.
    assert guess_founder_email("Madonna", "acme.io") is None


def test_guess_founder_email_strips_titles_and_suffixes() -> None:
    assert guess_founder_email("Dr. Aarav K. Singh", "acme.io") == "aarav.singh@acme.io"
    assert guess_founder_email("Aarav Singh Jr.", "acme.io") == "aarav.singh@acme.io"


def test_guess_founder_email_handles_diacritics() -> None:
    assert guess_founder_email("Renée Müller", "acme.io") == "renee.muller@acme.io"


def test_guess_founder_email_returns_none_for_missing_domain() -> None:
    assert guess_founder_email("Aarav Singh", "") is None


def test_guess_founder_email_returns_none_for_blank_name() -> None:
    assert guess_founder_email("   ", "acme.io") is None


def test_guess_founder_email_normalises_domain_scheme() -> None:
    assert guess_founder_email("Aarav Singh", "https://Acme.IO/") == "aarav.singh@acme.io"
