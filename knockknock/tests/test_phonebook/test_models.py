"""Unit tests for the phonebook value objects.

These are thin frozen dataclasses, but the tests pin down the
intentional API contract so callers can rely on:

- Both ``FounderHit`` and ``LookupOutcome`` are immutable (``frozen``).
- ``LookupOutcome.careers_email`` is required (the fallback always
  provides one); ``founder_*`` are optional.
- ``source`` is a ``PhonebookSource`` enum value, not a string.
"""

from __future__ import annotations

import dataclasses

import pytest

from knockknock.db.enums import PhonebookSource
from knockknock.phonebook.models import FounderHit, LookupOutcome


def test_founder_hit_is_frozen() -> None:
    hit = FounderHit(name="Aarav Singh", email="aarav@acme.io", source=PhonebookSource.APOLLO)
    with pytest.raises(dataclasses.FrozenInstanceError):
        hit.email = "other@acme.io"  # type: ignore[misc]


def test_founder_hit_allows_none_email() -> None:
    hit = FounderHit(name="Aarav Singh", email=None, source=PhonebookSource.APOLLO)
    assert hit.email is None
    assert hit.source is PhonebookSource.APOLLO


def test_lookup_outcome_requires_careers_email() -> None:
    outcome = LookupOutcome(
        founder_name=None,
        founder_email=None,
        careers_email="careers@acme.io",
        source=PhonebookSource.PATTERN_GUESS,
    )
    assert outcome.careers_email == "careers@acme.io"
    assert outcome.founder_name is None
    assert outcome.founder_email is None


def test_lookup_outcome_is_frozen() -> None:
    outcome = LookupOutcome(
        founder_name="Aarav Singh",
        founder_email="aarav@acme.io",
        careers_email="careers@acme.io",
        source=PhonebookSource.APOLLO,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.founder_email = "other@acme.io"  # type: ignore[misc]
