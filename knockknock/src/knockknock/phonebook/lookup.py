"""Cache-first phonebook lookup chain.

Order: cache → Apollo → Hunter → pattern-guess → ``careers@<domain>``
fallback. Each stage either produces a :class:`LookupOutcome` (we stop)
or falls through to the next. Provider failures
(:class:`ExternalServiceError`) are logged and treated as fall-through;
they never propagate.

The chain only fails outright when there is no domain *and* no cached
row -- without a domain we cannot derive even the fallback
``careers@<domain>`` address, and the caller (enrich stage) needs to
mark the job ENRICH_FAILED rather than write a useless phonebook row.

Persistence: a successful lookup writes one ``PhonebookEntry`` row.
The caller owns the transaction commit; we only ``flush`` so the row
is visible inside the same session for any further reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import structlog
from sqlmodel import Session, select

from knockknock.db.enums import PhonebookSource
from knockknock.db.models import Company, PhonebookEntry
from knockknock.exceptions import ExternalServiceError
from knockknock.phonebook.email_patterns import careers_fallback_email, guess_founder_email
from knockknock.phonebook.models import FounderHit, LookupOutcome

log = structlog.get_logger(__name__)

# Locked constant so reviewers can see the order at a glance and tests
# can pin it down (re-ordering would silently change paid-API cost
# patterns -- a behaviour change worth explicit review).
_PROVIDER_ORDER: tuple[PhonebookSource, ...] = (
    PhonebookSource.APOLLO,
    PhonebookSource.HUNTER,
)

# Per-source confidence written to `phonebook.confidence`. Real provider
# hits get a high score; pattern-guess is medium (we deduced the
# address, didn't observe it); SEED fallback is low (careers@<domain>
# is best-effort). MANUAL/CACHE values are read from the existing row,
# not used for inserts.
_CONFIDENCE_BY_SOURCE: dict[PhonebookSource, int] = {
    PhonebookSource.APOLLO: 80,
    PhonebookSource.HUNTER: 80,
    PhonebookSource.PATTERN_GUESS: 50,
    PhonebookSource.SEED: 20,
    PhonebookSource.MANUAL: 90,
    PhonebookSource.CACHE: 70,
}


class _FounderLookup(Protocol):
    """Structural type for ApolloClient / HunterClient.

    Defined as Protocol so the chain doesn't depend on concrete client
    classes, which keeps tests stub-friendly without inheritance.
    """

    def find_founders(self, *, domain: str) -> list[FounderHit]: ...


@dataclass(slots=True)
class PhonebookLookup:
    """Composable lookup chain. Persists into ``phonebook`` on miss."""

    apollo: _FounderLookup
    hunter: _FounderLookup

    def resolve(self, *, session: Session, company: Company) -> LookupOutcome:
        """Return phonebook info for ``company``, hitting cache or APIs as needed.

        Raises :class:`ValueError` only when no cache row exists *and*
        the company has no domain -- in that case the chain cannot
        produce even a fallback row and the caller must mark the job
        ENRICH_FAILED.
        """
        cached = self._get_cached(session, company)
        if cached is not None:
            return cached

        domain = (company.domain or "").strip().lower()
        if not domain:
            raise ValueError(
                f"Cannot enrich company id={company.id} ({company.name!r}): no domain available."
            )

        providers: tuple[tuple[PhonebookSource, _FounderLookup], ...] = (
            (PhonebookSource.APOLLO, self.apollo),
            (PhonebookSource.HUNTER, self.hunter),
        )
        for provider_source, provider in providers:
            try:
                hits = provider.find_founders(domain=domain)
            except ExternalServiceError as exc:
                # Apollo/Hunter outages or rate-limits MUST NOT poison
                # the chain -- the whole point of having two providers
                # plus a deterministic fallback is to keep going.
                log.warning(
                    "phonebook.lookup_failed",
                    provider=provider_source.value,
                    domain=domain,
                    error=str(exc),
                )
                hits = []
            outcome = self._select_outcome(hits, domain=domain, source=provider_source)
            if outcome is not None:
                self._persist(session, company, outcome)
                return outcome

        # All API providers exhausted. Fall back to deterministic
        # careers@<domain>. SEED marks the row as "we know nothing, but
        # this address is good enough to try". The enrich stage will
        # decide whether to mark the job ENRICHED or ENRICH_FAILED.
        fallback = LookupOutcome(
            founder_name=None,
            founder_email=None,
            careers_email=careers_fallback_email(domain),
            source=PhonebookSource.SEED,
        )
        self._persist(session, company, fallback)
        return fallback

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_cached(self, session: Session, company: Company) -> LookupOutcome | None:
        stmt = select(PhonebookEntry).where(PhonebookEntry.company_id == company.id)
        existing = session.exec(stmt).first()
        if existing is None:
            return None
        # The DB schema allows ``careers_email`` to be NULL (legacy /
        # manual rows from before this chain existed), but our value
        # object guarantees it. Repair on read by deriving the fallback
        # from the company's domain. If even that fails (no domain) we
        # surface None which is still legal at this layer; the caller
        # will trip the "no domain" branch on next miss.
        careers = existing.careers_email
        if careers is None:
            domain = (company.domain or "").strip().lower()
            careers = careers_fallback_email(domain) if domain else ""
        return LookupOutcome(
            founder_name=existing.founder_name,
            founder_email=existing.founder_email,
            careers_email=careers,
            source=existing.source,
        )

    def _select_outcome(
        self,
        hits: list[FounderHit],
        *,
        domain: str,
        source: PhonebookSource,
    ) -> LookupOutcome | None:
        """Pick the strongest hit from a provider's results, or None if useless.

        Preference order *within* a provider's hits:
        1. First hit with a real email (provider source is preserved).
        2. First hit whose name yields a valid pattern-guess email
           (source becomes PATTERN_GUESS regardless of which provider
           originally surfaced the name).
        3. None (fall through to next provider).
        """
        if not hits:
            return None

        for hit in hits:
            if hit.email:
                return LookupOutcome(
                    founder_name=hit.name,
                    founder_email=hit.email,
                    careers_email=careers_fallback_email(domain),
                    source=source,
                )

        for hit in hits:
            guessed = guess_founder_email(hit.name, domain)
            if guessed:
                return LookupOutcome(
                    founder_name=hit.name,
                    founder_email=guessed,
                    careers_email=careers_fallback_email(domain),
                    source=PhonebookSource.PATTERN_GUESS,
                )
        return None

    def _persist(self, session: Session, company: Company, outcome: LookupOutcome) -> None:
        """Insert phonebook row. Caller's transaction commits."""
        confidence = _CONFIDENCE_BY_SOURCE.get(outcome.source, 20)
        session.add(
            PhonebookEntry(
                company_id=company.id,
                founder_name=outcome.founder_name,
                founder_email=outcome.founder_email,
                careers_email=outcome.careers_email,
                source=outcome.source,
                confidence=confidence,
            )
        )
        session.flush()
