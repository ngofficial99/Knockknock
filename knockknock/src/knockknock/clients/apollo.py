"""Apollo.io organization-search client. Returns founder candidates only.

Phase 6 Step 1 of the phonebook chain. Given a company domain, we POST
to ``/v1/organizations/search`` and return only ``FounderHit`` entries
whose ``title`` matches a curated founder/executive regex (CEO,
Co-Founder, CTO, ...). Engineers and other non-founder employees are
silently filtered out so the rest of the chain only ever has to reason
about likely founders.

Failure model:

- 200 with an empty ``organizations`` list, OR
- 200 with orgs but no people matching the founder regex
  → return ``[]``. NOT an error. The chain falls through to Hunter.
- 429 (rate-limited), 5xx (server error), or 4xx auth error
  → raise :class:`ApolloLookupError`. The chain catches and falls
  through.
- Network/transport errors (DNS, connect, read timeout)
  → tenacity retries twice with exponential backoff; on final
  failure, the underlying exception bubbles up wrapped as
  :class:`ApolloLookupError`.

We intentionally do NOT raise on "Apollo doesn't know this company" --
that's a normal outcome for niche startups and must not poison the
chain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx
import structlog
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from knockknock.db.enums import PhonebookSource
from knockknock.exceptions import ExternalServiceError
from knockknock.phonebook.models import FounderHit

log = structlog.get_logger(__name__)

_BASE_URL = "https://api.apollo.io/v1/organizations/search"

# Title regex covering founders + C-suite execs. We deliberately stay
# narrow: VPs and Heads-of-X are common but not necessarily authoritative
# hiring contacts, and we'd rather miss a hit than promote the wrong
# person. The Phase 6 plan reserves room to widen this later.
_FOUNDER_TITLE_RE = re.compile(
    r"\b(founder|co[-\s]?founder|ceo|cto|cpo|coo|chief executive|chief technology)\b",
    re.IGNORECASE,
)


class ApolloLookupError(ExternalServiceError):
    """Raised when Apollo returns an unrecoverable error (429/5xx/4xx).

    Wrapped in :class:`ExternalServiceError` so the enrich stage can
    catch the broader "any provider failed" category without naming
    Apollo specifically.
    """


@dataclass(slots=True)
class ApolloClient:
    """Thin synchronous wrapper around Apollo's organization-search API.

    The HTTP client is injected so tests can use respx and production
    can pool connections across calls.
    """

    api_key: str
    http: httpx.Client
    timeout_seconds: float = 8.0

    def find_founders(self, *, domain: str) -> list[FounderHit]:
        """Return likely founders for ``domain``. Empty list if none.

        See module docstring for the full failure-model contract.
        """
        try:
            payload = self._post_with_retries(domain)
        except ApolloLookupError:
            raise
        except RetryError as exc:
            # Transport-level failures after all retries.
            raise ApolloLookupError(
                f"Apollo transport failure for {domain}: {exc.last_attempt.exception()}"
            ) from exc
        except httpx.HTTPError as exc:
            # Non-retryable transport error or single-attempt failure.
            raise ApolloLookupError(f"Apollo transport error for {domain}: {exc}") from exc

        organizations = payload.get("organizations") or []
        if not organizations:
            return []

        # Apollo's fuzzy match sometimes returns lookalike companies
        # (acme.io → acme-clone.io). Sort so the org whose primary_domain
        # matches our requested domain comes first; only that one is used.
        target = domain.lower()
        organizations.sort(
            key=lambda o: 0 if (o.get("primary_domain") or "").lower() == target else 1
        )
        org = organizations[0]
        people = org.get("people") or []

        founders: list[FounderHit] = []
        for person in people:
            title = (person.get("title") or "").strip()
            if not _FOUNDER_TITLE_RE.search(title):
                continue
            name = (person.get("name") or "").strip()
            if not name:
                continue
            email = (person.get("email") or "").strip() or None
            founders.append(FounderHit(name=name, email=email, source=PhonebookSource.APOLLO))
        return founders

    @retry(
        # Only retry on transport-level errors. ApolloLookupError is a
        # business-level decision (rate limit, auth) and should bubble
        # out immediately without burning retries.
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(2),
        wait=wait_exponential(min=1, max=4),
        reraise=True,
    )
    def _post_with_retries(self, domain: str) -> dict[str, Any]:
        try:
            response = self.http.post(
                _BASE_URL,
                json={"q_organization_domains": domain, "page": 1, "per_page": 5},
                headers={"X-Api-Key": self.api_key, "Content-Type": "application/json"},
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError:
            raise

        if response.status_code == 429:
            raise ApolloLookupError(f"Apollo rate-limited: {response.text[:200]}")
        if response.status_code >= 500:
            raise ApolloLookupError(f"Apollo {response.status_code}: {response.text[:200]}")
        if response.status_code >= 400:
            raise ApolloLookupError(f"Apollo {response.status_code}: {response.text[:200]}")
        return dict(response.json())
