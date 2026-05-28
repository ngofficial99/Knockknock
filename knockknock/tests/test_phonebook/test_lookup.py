"""Integration tests for :class:`PhonebookLookup`.

The chain orchestrates: cache → Apollo → Hunter → pattern-guess →
`careers@<domain>` fallback. Each step has a distinct *successful*
exit path and a distinct *fall-through* condition; these tests pin
down every transition.

Provider failures (Apollo/Hunter raising :class:`ExternalServiceError`)
are tolerated -- the chain logs and falls through to the next provider.
The only failure that propagates is "no domain *and* no usable lookup"
because that genuinely means the caller cannot enrich this company
and must surface an ENRICH_FAILED.

These tests need a real Postgres for ``PhonebookEntry`` writes; they
auto-skip when ``KNOCKKNOCK_TEST_DATABASE_URL`` isn't set.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from sqlmodel import Session

from knockknock.db.enums import CompanySizeBucket, PhonebookSource
from knockknock.db.models import Company, PhonebookEntry
from knockknock.exceptions import ExternalServiceError
from knockknock.phonebook.lookup import _PROVIDER_ORDER, PhonebookLookup
from knockknock.phonebook.models import FounderHit


@dataclass
class _StubProvider:
    """Stand-in for ApolloClient/HunterClient implementing the duck-type contract."""

    hits: list[FounderHit] = field(default_factory=list)
    fail: bool = False
    calls: int = 0

    def find_founders(self, *, domain: str) -> list[FounderHit]:
        self.calls += 1
        if self.fail:
            raise ExternalServiceError(f"stub provider down for {domain}")
        return list(self.hits)


def _make_company(db_session: Session, *, name: str = "Acme", domain: str = "acme.io") -> Company:
    company = Company(name=name, domain=domain, size_bucket=CompanySizeBucket.UNKNOWN)
    db_session.add(company)
    db_session.flush()
    db_session.refresh(company)
    return company


def test_provider_order_is_apollo_then_hunter() -> None:
    """Locked constant so reviewers can see the order at a glance."""
    assert _PROVIDER_ORDER == (PhonebookSource.APOLLO, PhonebookSource.HUNTER)


def test_lookup_uses_cache_and_skips_apis(db_session: Session) -> None:
    """Pre-existing phonebook row short-circuits the chain (no API calls)."""
    company = _make_company(db_session)
    db_session.add(
        PhonebookEntry(
            company_id=company.id,
            founder_name="Old Founder",
            founder_email="old@acme.io",
            careers_email="careers@acme.io",
            source=PhonebookSource.MANUAL,
            confidence=90,
        )
    )
    db_session.flush()
    apollo = _StubProvider()
    hunter = _StubProvider()
    chain = PhonebookLookup(apollo=apollo, hunter=hunter)
    outcome = chain.resolve(session=db_session, company=company)
    assert outcome.founder_email == "old@acme.io"
    assert outcome.founder_name == "Old Founder"
    assert outcome.source is PhonebookSource.MANUAL
    assert apollo.calls == 0
    assert hunter.calls == 0


def test_lookup_uses_apollo_with_email(db_session: Session) -> None:
    """Apollo's first email-bearing hit wins; Hunter is not called."""
    company = _make_company(db_session)
    apollo = _StubProvider(
        hits=[FounderHit("Aarav Singh", "aarav@acme.io", PhonebookSource.APOLLO)]
    )
    hunter = _StubProvider()
    chain = PhonebookLookup(apollo=apollo, hunter=hunter)
    outcome = chain.resolve(session=db_session, company=company)
    assert outcome.founder_email == "aarav@acme.io"
    assert outcome.founder_name == "Aarav Singh"
    # careers_email is the comma-joined fallback so the drafter can Cc
    # both safe aliases (Task 6.x: hr@ addition).
    assert outcome.careers_email == "careers@acme.io, hr@acme.io"
    assert outcome.source is PhonebookSource.APOLLO
    assert hunter.calls == 0


def test_lookup_apollo_name_only_falls_through_to_pattern_guess(
    db_session: Session,
) -> None:
    """Apollo free tier returns name w/o email → derive via pattern guess."""
    company = _make_company(db_session)
    apollo = _StubProvider(hits=[FounderHit("Aarav Singh", None, PhonebookSource.APOLLO)])
    hunter = _StubProvider()
    chain = PhonebookLookup(apollo=apollo, hunter=hunter)
    outcome = chain.resolve(session=db_session, company=company)
    assert outcome.founder_email == "aarav.singh@acme.io"
    assert outcome.founder_name == "Aarav Singh"
    assert outcome.source is PhonebookSource.PATTERN_GUESS


def test_lookup_apollo_fails_then_hunter(db_session: Session) -> None:
    """Apollo throwing ExternalServiceError must not poison the chain."""
    company = _make_company(db_session)
    apollo = _StubProvider(fail=True)
    hunter = _StubProvider(hits=[FounderHit("Bina Rao", "bina@acme.io", PhonebookSource.HUNTER)])
    chain = PhonebookLookup(apollo=apollo, hunter=hunter)
    outcome = chain.resolve(session=db_session, company=company)
    assert outcome.founder_email == "bina@acme.io"
    assert outcome.source is PhonebookSource.HUNTER
    # Apollo was tried once before falling through.
    assert apollo.calls == 1
    assert hunter.calls == 1


def test_lookup_all_fail_returns_careers_fallback(db_session: Session) -> None:
    """Both providers down → deterministic ``careers@<domain>`` row."""
    company = _make_company(db_session)
    apollo = _StubProvider(fail=True)
    hunter = _StubProvider(fail=True)
    chain = PhonebookLookup(apollo=apollo, hunter=hunter)
    outcome = chain.resolve(session=db_session, company=company)
    assert outcome.founder_email is None
    assert outcome.founder_name is None
    # Both safe aliases, comma-joined; drafter splits on ", " for Cc.
    assert outcome.careers_email == "careers@acme.io, hr@acme.io"
    assert outcome.source is PhonebookSource.SEED


def test_lookup_persists_row_on_first_miss(db_session: Session) -> None:
    """Second resolve() must hit the cache, not re-call providers."""
    company = _make_company(db_session)
    apollo = _StubProvider(
        hits=[FounderHit("Aarav Singh", "aarav@acme.io", PhonebookSource.APOLLO)]
    )
    hunter = _StubProvider()
    chain = PhonebookLookup(apollo=apollo, hunter=hunter)
    chain.resolve(session=db_session, company=company)
    chain.resolve(session=db_session, company=company)
    assert apollo.calls == 1  # second call cache-hit
    assert hunter.calls == 0


def test_lookup_raises_when_no_domain(db_session: Session) -> None:
    """Empty domain ⇒ we cannot even derive ``careers@<domain>``."""
    company = _make_company(db_session, domain="")
    apollo = _StubProvider()
    hunter = _StubProvider()
    chain = PhonebookLookup(apollo=apollo, hunter=hunter)
    with pytest.raises(ValueError, match="domain"):
        chain.resolve(session=db_session, company=company)
    # No domain means we never even called providers (waste of credits).
    assert apollo.calls == 0
    assert hunter.calls == 0
