"""Hunter.io domain-search client. Fallback when Apollo returns nothing.

Phase 6 Step 2 of the phonebook chain. Given a company domain, we GET
``/v2/domain-search`` with ``seniority=executive&department=management``
and return :class:`FounderHit` entries for any executive-tier email
whose Hunter ``confidence`` clears :data:`_MIN_CONFIDENCE`. Junior
employees and low-confidence guesses are silently filtered out.

Failure model:

- 200 with empty ``data.emails`` (or missing ``data`` envelope)
  → return ``[]``. NOT an error. The chain falls through to the
  pattern-guess fallback.
- 429 → raise :class:`RateLimitedError`. The chain catches the broader
  :class:`ExternalServiceError` and falls through.
- 5xx / 4xx auth → raise :class:`HunterLookupError`.
- Network/transport errors → tenacity retries twice with exponential
  backoff; on final failure, the wrapped exception is re-raised as
  :class:`HunterLookupError`.

Hunter free tier is tight (25 searches / month). The lookup chain only
calls Hunter when Apollo returned no usable founder, so this client
should see traffic only on Apollo misses.
"""

from __future__ import annotations

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
from knockknock.exceptions import ExternalServiceError, RateLimitedError
from knockknock.phonebook.models import FounderHit

log = structlog.get_logger(__name__)

_BASE_URL = "https://api.hunter.io/v2/domain-search"

# Hunter returns a 0-100 confidence score per email. 60 is the floor
# Hunter itself uses to mark an email "verifiable"; below that the
# guess is statistically weak and we'd rather miss than promote noise.
_MIN_CONFIDENCE = 60


class HunterLookupError(ExternalServiceError):
    """Raised when Hunter returns an unrecoverable error (5xx/4xx).

    Wrapped in :class:`ExternalServiceError` so the enrich stage can
    catch the broader "any provider failed" category without naming
    Hunter specifically. 429 raises :class:`RateLimitedError` instead;
    both inherit from :class:`ExternalServiceError`.
    """


@dataclass(slots=True)
class HunterClient:
    """Thin synchronous wrapper around Hunter's domain-search API.

    The HTTP client is injected so tests can use respx and production
    can pool connections across calls.
    """

    api_key: str
    http: httpx.Client
    timeout_seconds: float = 8.0

    def find_founders(self, *, domain: str) -> list[FounderHit]:
        """Return executive-tier hits for ``domain``. Empty list if none.

        See module docstring for the full failure-model contract.
        """
        try:
            payload = self._get_with_retries(domain)
        except (HunterLookupError, RateLimitedError):
            raise
        except RetryError as exc:
            raise HunterLookupError(
                f"Hunter transport failure for {domain}: {exc.last_attempt.exception()}"
            ) from exc
        except httpx.HTTPError as exc:
            raise HunterLookupError(f"Hunter transport error for {domain}: {exc}") from exc

        data = payload.get("data") or {}
        emails = data.get("emails") or []

        founders: list[FounderHit] = []
        for entry in emails:
            seniority = (entry.get("seniority") or "").lower()
            if seniority != "executive":
                continue
            confidence = int(entry.get("confidence") or 0)
            if confidence < _MIN_CONFIDENCE:
                continue
            email = (entry.get("value") or "").strip() or None
            first = (entry.get("first_name") or "").strip()
            last = (entry.get("last_name") or "").strip()
            # Fall back to position (e.g. "Chief Executive Officer") if
            # Hunter omitted the name -- gives the downstream drafter
            # *something* to address even when we lack a real name.
            name = " ".join(p for p in (first, last) if p) or (entry.get("position") or "").strip()
            if not (email or name):
                continue
            founders.append(FounderHit(name=name, email=email, source=PhonebookSource.HUNTER))
        return founders

    @retry(
        # Only retry on transport-level errors. RateLimitedError and
        # HunterLookupError are business-level decisions that should
        # bubble out immediately without burning retries.
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(2),
        wait=wait_exponential(min=1, max=4),
        reraise=True,
    )
    def _get_with_retries(self, domain: str) -> dict[str, Any]:
        response = self.http.get(
            _BASE_URL,
            params={
                "domain": domain,
                "api_key": self.api_key,
                "seniority": "executive",
                "department": "management",
                "limit": 5,
            },
            timeout=self.timeout_seconds,
        )

        if response.status_code == 429:
            raise RateLimitedError(f"Hunter rate-limited: {response.text[:200]}")
        if response.status_code >= 500:
            raise HunterLookupError(f"Hunter {response.status_code}: {response.text[:200]}")
        if response.status_code >= 400:
            raise HunterLookupError(f"Hunter {response.status_code}: {response.text[:200]}")
        return dict(response.json())
