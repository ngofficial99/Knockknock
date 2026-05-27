← [Index](00-index.md) · [Prev: phase-05-gemini-score.md](phase-05-gemini-score.md) · [Next: phase-07-resume-tailor.md](phase-07-resume-tailor.md)

## Phase 6: Phonebook (Apollo → Hunter → Pattern Guess) and Enrich Stage

**Outcome:** Every SCORED job has a `phonebook` row by the time it leaves the enrich stage. Lookup order is **cache-first** (existing `phonebook` row), then Apollo (founder org-search), then Hunter (domain-search), and finally a deterministic `careers@<domain>` fallback so `careers_email` is never null. Failures of Apollo/Hunter are tolerated; only the fallback failing (no domain) marks a job ENRICH_FAILED.

### Task 6.1: Phonebook value objects + email-pattern utility

**Files:**
- Create: `src/knockknock/phonebook/__init__.py`
- Create: `src/knockknock/phonebook/models.py`
- Create: `src/knockknock/phonebook/email_patterns.py`
- Create: `tests/test_phonebook/__init__.py`
- Create: `tests/test_phonebook/test_email_patterns.py`

- [ ] **Step 1: Write failing tests for the pattern guesser**

Create `tests/test_phonebook/__init__.py` as empty file.

Create `tests/test_phonebook/test_email_patterns.py`:

```python
from __future__ import annotations

import pytest

from knockknock.phonebook.email_patterns import (
    careers_fallback_email,
    guess_founder_email,
)


def test_careers_fallback_lowercases_domain() -> None:
    assert careers_fallback_email("Acme.IO") == "careers@acme.io"


def test_careers_fallback_strips_scheme_and_www() -> None:
    assert careers_fallback_email("https://www.example.com/") == "careers@example.com"


def test_careers_fallback_raises_on_empty_domain() -> None:
    with pytest.raises(ValueError, match="domain"):
        careers_fallback_email("")


def test_guess_founder_email_first_last() -> None:
    assert guess_founder_email("Aarav Singh", "acme.io") == "aarav.singh@acme.io"


def test_guess_founder_email_single_token_returns_none() -> None:
    # "Madonna" — single token, no reliable pattern guess.
    assert guess_founder_email("Madonna", "acme.io") is None


def test_guess_founder_email_strips_suffixes() -> None:
    assert guess_founder_email("Dr. Aarav K. Singh", "acme.io") == "aarav.singh@acme.io"


def test_guess_founder_email_handles_diacritics() -> None:
    assert guess_founder_email("Renée Müller", "acme.io") == "renee.muller@acme.io"


def test_guess_founder_email_returns_none_for_missing_domain() -> None:
    assert guess_founder_email("Aarav Singh", "") is None
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
uv run pytest tests/test_phonebook -v
```

Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement the pattern utilities**

Create `src/knockknock/phonebook/__init__.py` as empty file.

Create `src/knockknock/phonebook/email_patterns.py`:

```python
"""Deterministic email-pattern helpers. No I/O."""

from __future__ import annotations

import re
import unicodedata

# Tokens commonly attached to names that should be stripped before pattern guess.
_NAME_NOISE_RE = re.compile(
    r"^(dr|mr|mrs|ms|prof|sir)\.?$|^(jr|sr|ii|iii|iv|phd|md|mba)\.?$",
    re.IGNORECASE,
)
_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)


def _strip_diacritics(value: str) -> str:
    """Drop diacritics (é → e). Keep base ASCII chars only."""
    normalised = unicodedata.normalize("NFKD", value)
    return "".join(c for c in normalised if not unicodedata.combining(c))


def _normalise_domain(raw: str) -> str:
    """Lowercase, strip scheme, strip leading `www.`, drop trailing slash and path."""
    cleaned = raw.strip().lower()
    cleaned = _SCHEME_RE.sub("", cleaned)
    cleaned = cleaned.split("/", 1)[0]
    if cleaned.startswith("www."):
        cleaned = cleaned[4:]
    return cleaned


def careers_fallback_email(domain: str) -> str:
    """Return `careers@<normalised-domain>`. Raise if no domain.

    This is the *last-resort* address — we always have something to write into
    `phonebook.careers_email` even when every API lookup fails.
    """
    norm = _normalise_domain(domain)
    if not norm:
        raise ValueError("Cannot build careers fallback without a domain.")
    return f"careers@{norm}"


def guess_founder_email(full_name: str, domain: str) -> str | None:
    """Best-effort guess: `firstname.lastname@<domain>`.

    Returns None when we don't have enough signal: empty name, single-token
    name, or empty domain. The caller is responsible for downstream verification
    (we never claim accuracy here).
    """
    norm_domain = _normalise_domain(domain)
    if not norm_domain:
        return None

    stripped = _strip_diacritics(full_name).lower()
    tokens = [t for t in re.split(r"[\s\-]+", stripped) if t]
    tokens = [t.rstrip(".") for t in tokens if not _NAME_NOISE_RE.match(t)]
    # Drop single-char middle initials e.g. "k."
    tokens = [t for t in tokens if len(t) > 1]
    if len(tokens) < 2:
        return None

    first, last = tokens[0], tokens[-1]
    first = re.sub(r"[^a-z]", "", first)
    last = re.sub(r"[^a-z]", "", last)
    if not first or not last:
        return None
    return f"{first}.{last}@{norm_domain}"
```

Create `src/knockknock/phonebook/models.py`:

```python
"""Value objects passed between lookup providers and the enrich stage."""

from __future__ import annotations

from dataclasses import dataclass

from knockknock.db.enums import PhonebookSource


@dataclass(frozen=True, slots=True)
class FounderHit:
    """Single founder candidate from any provider."""

    name: str
    email: str | None
    source: PhonebookSource


@dataclass(frozen=True, slots=True)
class LookupOutcome:
    """Aggregated result of a lookup chain for one company."""

    founder_name: str | None
    founder_email: str | None
    careers_email: str
    source: PhonebookSource  # source for the *best* signal (founder if any, else fallback)
```

- [ ] **Step 4: Run tests until green**

```bash
uv run pytest tests/test_phonebook -v
```

Expected: PASS for all 7 tests.

- [ ] **Step 5: Lint + type check + commit**

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
git add src/knockknock/phonebook tests/test_phonebook
git commit -m "feat(phonebook): value objects + email pattern helpers"
```

### Task 6.2: Apollo client

**Files:**
- Create: `src/knockknock/clients/apollo.py`
- Create: `tests/test_clients/test_apollo.py`

We use Apollo's organization-search endpoint (`/v1/organizations/search`) — free tier returns up to 25 results per call, includes founder profiles with emails in many cases. We only need the org's founder list.

- [ ] **Step 1: Write failing tests with respx**

Create `tests/test_clients/test_apollo.py`:

```python
from __future__ import annotations

import httpx
import pytest
import respx

from knockknock.clients.apollo import ApolloClient, ApolloLookupError
from knockknock.db.enums import PhonebookSource


@pytest.mark.respx
@respx.mock
def test_apollo_returns_founders_for_domain() -> None:
    payload = {
        "organizations": [
            {
                "id": "org_1",
                "name": "Acme",
                "primary_domain": "acme.io",
                "people": [
                    {
                        "name": "Aarav Singh",
                        "title": "Co-Founder & CEO",
                        "email": "aarav@acme.io",
                    },
                    {
                        "name": "Bob Engineer",
                        "title": "Software Engineer",
                        "email": "bob@acme.io",
                    },
                ],
            }
        ]
    }
    respx.post("https://api.apollo.io/v1/organizations/search").respond(
        200, json=payload
    )
    client = ApolloClient(api_key="test-key", http=httpx.Client())
    hits = client.find_founders(domain="acme.io")
    assert len(hits) == 1  # only the founder, not the engineer
    assert hits[0].name == "Aarav Singh"
    assert hits[0].email == "aarav@acme.io"
    assert hits[0].source == PhonebookSource.APOLLO


@pytest.mark.respx
@respx.mock
def test_apollo_returns_empty_when_no_orgs() -> None:
    respx.post("https://api.apollo.io/v1/organizations/search").respond(
        200, json={"organizations": []}
    )
    client = ApolloClient(api_key="k", http=httpx.Client())
    assert client.find_founders(domain="missing.com") == []


@pytest.mark.respx
@respx.mock
def test_apollo_raises_on_5xx() -> None:
    respx.post("https://api.apollo.io/v1/organizations/search").respond(503)
    client = ApolloClient(api_key="k", http=httpx.Client())
    with pytest.raises(ApolloLookupError):
        client.find_founders(domain="acme.io")


@pytest.mark.respx
@respx.mock
def test_apollo_raises_on_rate_limit() -> None:
    respx.post("https://api.apollo.io/v1/organizations/search").respond(429)
    client = ApolloClient(api_key="k", http=httpx.Client())
    with pytest.raises(ApolloLookupError, match="rate"):
        client.find_founders(domain="acme.io")
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
uv run pytest tests/test_clients/test_apollo.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement the client**

Create `src/knockknock/clients/apollo.py`:

```python
"""Apollo.io organization-search client. Returns founder candidates only."""

from __future__ import annotations

import re
from dataclasses import dataclass

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

_BASE_URL = "https://api.apollo.io/v1/organizations/search"
_FOUNDER_TITLE_RE = re.compile(
    r"\b(founder|co[-\s]?founder|ceo|cto|cpo|coo|chief executive|chief technology)\b",
    re.IGNORECASE,
)


class ApolloLookupError(ExternalServiceError):
    """Raised when Apollo returns an unrecoverable error."""


@dataclass(slots=True)
class ApolloClient:
    api_key: str
    http: httpx.Client
    timeout_seconds: float = 8.0

    def find_founders(self, *, domain: str) -> list[FounderHit]:
        """Return up to a few founder candidates for a domain.

        Empty list = looked up successfully but no founders found.
        Raises ApolloLookupError on transport/auth/rate-limit/server errors.
        """
        try:
            payload = self._post_with_retries(domain)
        except RetryError as exc:
            raise ApolloLookupError(f"Apollo retries exhausted for {domain}: {exc}") from exc

        organizations = payload.get("organizations") or []
        if not organizations:
            return []

        # Pick the org whose primary_domain best matches the requested domain.
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
            email = (person.get("email") or "").strip() or None
            if not name:
                continue
            founders.append(
                FounderHit(name=name, email=email, source=PhonebookSource.APOLLO)
            )
        return founders

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError,)),
        stop=stop_after_attempt(2),
        wait=wait_exponential(min=2, max=5),
        reraise=True,
    )
    def _post_with_retries(self, domain: str) -> dict:
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
            raise RateLimitedError(f"Apollo rate-limited: {response.text[:200]}")
        if response.status_code >= 500:
            raise httpx.HTTPStatusError(
                "Apollo 5xx",
                request=response.request,
                response=response,
            )
        if response.status_code >= 400:
            raise ApolloLookupError(
                f"Apollo {response.status_code}: {response.text[:200]}"
            )
        return response.json()
```

- [ ] **Step 4: Run tests until green**

```bash
uv run pytest tests/test_clients/test_apollo.py -v
```

Expected: PASS for all 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/knockknock/clients/apollo.py tests/test_clients/test_apollo.py
git commit -m "feat(clients): apollo founder-lookup client"
```

### Task 6.3: Hunter client

**Files:**
- Create: `src/knockknock/clients/hunter.py`
- Create: `tests/test_clients/test_hunter.py`

Hunter's `domain-search` endpoint takes a domain and returns up to 10 emails per call with a `seniority` filter. We request `seniority=executive&department=management` to bias towards founders.

- [ ] **Step 1: Write failing tests**

Create `tests/test_clients/test_hunter.py`:

```python
from __future__ import annotations

import httpx
import pytest
import respx

from knockknock.clients.hunter import HunterClient, HunterLookupError
from knockknock.db.enums import PhonebookSource


@pytest.mark.respx
@respx.mock
def test_hunter_returns_executive_emails() -> None:
    payload = {
        "data": {
            "domain": "acme.io",
            "emails": [
                {
                    "value": "aarav@acme.io",
                    "first_name": "Aarav",
                    "last_name": "Singh",
                    "position": "Founder",
                    "seniority": "executive",
                    "confidence": 92,
                },
                {
                    "value": "bob@acme.io",
                    "first_name": "Bob",
                    "last_name": "Engineer",
                    "position": "SWE",
                    "seniority": "junior",
                    "confidence": 80,
                },
            ],
        }
    }
    respx.get("https://api.hunter.io/v2/domain-search").respond(200, json=payload)
    client = HunterClient(api_key="k", http=httpx.Client())
    hits = client.find_founders(domain="acme.io")
    assert len(hits) == 1
    assert hits[0].email == "aarav@acme.io"
    assert hits[0].source == PhonebookSource.HUNTER


@pytest.mark.respx
@respx.mock
def test_hunter_raises_on_429() -> None:
    respx.get("https://api.hunter.io/v2/domain-search").respond(429)
    client = HunterClient(api_key="k", http=httpx.Client())
    with pytest.raises(HunterLookupError):
        client.find_founders(domain="acme.io")


@pytest.mark.respx
@respx.mock
def test_hunter_empty_response() -> None:
    respx.get("https://api.hunter.io/v2/domain-search").respond(
        200, json={"data": {"domain": "x.com", "emails": []}}
    )
    client = HunterClient(api_key="k", http=httpx.Client())
    assert client.find_founders(domain="x.com") == []
```

- [ ] **Step 2: Run tests; confirm failure; implement**

Create `src/knockknock/clients/hunter.py`:

```python
"""Hunter.io domain-search client. Fallback for Apollo misses."""

from __future__ import annotations

from dataclasses import dataclass

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
_MIN_CONFIDENCE = 60


class HunterLookupError(ExternalServiceError):
    """Raised when Hunter returns an unrecoverable error."""


@dataclass(slots=True)
class HunterClient:
    api_key: str
    http: httpx.Client
    timeout_seconds: float = 8.0

    def find_founders(self, *, domain: str) -> list[FounderHit]:
        try:
            payload = self._get_with_retries(domain)
        except RetryError as exc:
            raise HunterLookupError(f"Hunter retries exhausted for {domain}: {exc}") from exc

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
            name = " ".join(p for p in (first, last) if p) or (entry.get("position") or "")
            if not (email or name):
                continue
            founders.append(
                FounderHit(name=name, email=email, source=PhonebookSource.HUNTER)
            )
        return founders

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError,)),
        stop=stop_after_attempt(2),
        wait=wait_exponential(min=2, max=5),
        reraise=True,
    )
    def _get_with_retries(self, domain: str) -> dict:
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
            raise httpx.HTTPStatusError(
                "Hunter 5xx",
                request=response.request,
                response=response,
            )
        if response.status_code >= 400:
            raise HunterLookupError(
                f"Hunter {response.status_code}: {response.text[:200]}"
            )
        return response.json()
```

> **Note on `RateLimitedError`:** both clients raise `RateLimitedError` (defined in Phase 0 exceptions hierarchy). The lookup chain in 6.4 catches it the same way as the more generic `ExternalServiceError` — meaning Apollo being rate-limited cleanly falls through to Hunter.

- [ ] **Step 3: Run tests until green; commit**

```bash
uv run pytest tests/test_clients/test_hunter.py -v
uv run ruff check src tests
uv run mypy
git add src/knockknock/clients/hunter.py tests/test_clients/test_hunter.py
git commit -m "feat(clients): hunter domain-search client"
```

### Task 6.4: Phonebook lookup chain

**Files:**
- Create: `src/knockknock/phonebook/lookup.py`
- Create: `tests/test_phonebook/test_lookup.py`

Chain semantics:

1. **Cache:** if `phonebook` row already exists for the company, return it as-is (no API calls).
2. **Apollo:** request founders; if any has an email, use it; if any has a name but no email, run `guess_founder_email` against the company domain.
3. **Hunter:** same as above, but only if Apollo returned nothing usable.
4. **Pattern guess (no API call):** if we still have no email but we have *any* founder name remembered from earlier scrapes (e.g. attached to job description metadata), guess from that. (For now we don't carry founder names through scrapes, so this step often no-ops — kept as an extension point.)
5. **Fallback:** always set `careers_email = careers@<domain>`.

- [ ] **Step 1: Write failing chain tests**

Create `tests/test_phonebook/test_lookup.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session

from knockknock.db.enums import PhonebookSource
from knockknock.db.models import Company, PhonebookEntry
from knockknock.exceptions import ExternalServiceError
from knockknock.phonebook.lookup import PhonebookLookup, _PROVIDER_ORDER
from knockknock.phonebook.models import FounderHit


@dataclass
class _StubApollo:
    hits: list[FounderHit]
    fail: bool = False
    calls: int = 0

    def find_founders(self, *, domain: str) -> list[FounderHit]:
        self.calls += 1
        if self.fail:
            raise ExternalServiceError("apollo down")
        return list(self.hits)


@dataclass
class _StubHunter:
    hits: list[FounderHit]
    fail: bool = False
    calls: int = 0

    def find_founders(self, *, domain: str) -> list[FounderHit]:
        self.calls += 1
        if self.fail:
            raise ExternalServiceError("hunter down")
        return list(self.hits)


def _make_company(db_session: Session, *, name: str = "Acme", domain: str = "acme.io") -> Company:
    company = Company(name=name, domain=domain)
    db_session.add(company)
    db_session.flush()
    return company


def test_provider_order_is_apollo_then_hunter() -> None:
    assert _PROVIDER_ORDER == (PhonebookSource.APOLLO, PhonebookSource.HUNTER)


def test_lookup_uses_cache_and_skips_apis(db_session: Session) -> None:
    company = _make_company(db_session)
    db_session.add(
        PhonebookEntry(
            company_id=company.id,
            founder_name="Old Founder",
            founder_email="old@acme.io",
            careers_email="careers@acme.io",
            source=PhonebookSource.MANUAL,
        )
    )
    db_session.flush()
    apollo = _StubApollo(hits=[])
    hunter = _StubHunter(hits=[])
    chain = PhonebookLookup(apollo=apollo, hunter=hunter)
    outcome = chain.resolve(session=db_session, company=company)
    assert outcome.founder_email == "old@acme.io"
    assert outcome.source == PhonebookSource.MANUAL
    assert apollo.calls == 0
    assert hunter.calls == 0


def test_lookup_uses_apollo_with_email(db_session: Session) -> None:
    company = _make_company(db_session)
    apollo = _StubApollo(
        hits=[FounderHit("Aarav Singh", "aarav@acme.io", PhonebookSource.APOLLO)]
    )
    hunter = _StubHunter(hits=[])
    chain = PhonebookLookup(apollo=apollo, hunter=hunter)
    outcome = chain.resolve(session=db_session, company=company)
    assert outcome.founder_email == "aarav@acme.io"
    assert outcome.source == PhonebookSource.APOLLO
    assert hunter.calls == 0


def test_lookup_apollo_name_only_falls_through_to_pattern_guess(db_session: Session) -> None:
    company = _make_company(db_session)
    # Apollo finds a founder but no email; we should guess.
    apollo = _StubApollo(
        hits=[FounderHit("Aarav Singh", None, PhonebookSource.APOLLO)]
    )
    hunter = _StubHunter(hits=[])
    chain = PhonebookLookup(apollo=apollo, hunter=hunter)
    outcome = chain.resolve(session=db_session, company=company)
    assert outcome.founder_email == "aarav.singh@acme.io"
    assert outcome.source == PhonebookSource.PATTERN_GUESS


def test_lookup_apollo_fails_then_hunter(db_session: Session) -> None:
    company = _make_company(db_session)
    apollo = _StubApollo(hits=[], fail=True)
    hunter = _StubHunter(
        hits=[FounderHit("Bina Rao", "bina@acme.io", PhonebookSource.HUNTER)]
    )
    chain = PhonebookLookup(apollo=apollo, hunter=hunter)
    outcome = chain.resolve(session=db_session, company=company)
    assert outcome.founder_email == "bina@acme.io"
    assert outcome.source == PhonebookSource.HUNTER


def test_lookup_all_fail_returns_careers_fallback(db_session: Session) -> None:
    company = _make_company(db_session)
    apollo = _StubApollo(hits=[], fail=True)
    hunter = _StubHunter(hits=[], fail=True)
    chain = PhonebookLookup(apollo=apollo, hunter=hunter)
    outcome = chain.resolve(session=db_session, company=company)
    assert outcome.founder_email is None
    assert outcome.careers_email == "careers@acme.io"
    # Source reflects what we wrote — fallback uses SEED to mark deterministic.
    assert outcome.source == PhonebookSource.SEED


def test_lookup_raises_when_no_domain_and_all_lookups_empty(db_session: Session) -> None:
    company = _make_company(db_session, domain="")
    apollo = _StubApollo(hits=[])
    hunter = _StubHunter(hits=[])
    chain = PhonebookLookup(apollo=apollo, hunter=hunter)
    import pytest
    with pytest.raises(ValueError, match="domain"):
        chain.resolve(session=db_session, company=company)
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
uv run pytest tests/test_phonebook/test_lookup.py -v
```

Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement the lookup chain**

Create `src/knockknock/phonebook/lookup.py`:

```python
"""Cache-first phonebook lookup chain: cache → Apollo → Hunter → guess → fallback."""

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

_PROVIDER_ORDER: tuple[PhonebookSource, ...] = (PhonebookSource.APOLLO, PhonebookSource.HUNTER)


class _FounderLookup(Protocol):
    def find_founders(self, *, domain: str) -> list[FounderHit]: ...


@dataclass(slots=True)
class PhonebookLookup:
    """Composable lookup chain. Persists into `phonebook` on miss."""

    apollo: _FounderLookup
    hunter: _FounderLookup

    def resolve(self, *, session: Session, company: Company) -> LookupOutcome:
        cached = self._get_cached(session, company)
        if cached is not None:
            return cached

        domain = (company.domain or "").strip().lower()

        # Apollo first, then Hunter.
        for provider_source, provider in (
            (PhonebookSource.APOLLO, self.apollo),
            (PhonebookSource.HUNTER, self.hunter),
        ):
            if not domain:
                break  # No domain ⇒ no point calling external providers.
            try:
                hits = provider.find_founders(domain=domain)
            except ExternalServiceError as exc:
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

        # All API providers exhausted. Try fallback.
        if not domain:
            raise ValueError(
                f"Cannot enrich company id={company.id} ({company.name!r}): no domain available."
            )
        fallback = LookupOutcome(
            founder_name=None,
            founder_email=None,
            careers_email=careers_fallback_email(domain),
            source=PhonebookSource.SEED,
        )
        self._persist(session, company, fallback)
        return fallback

    def _get_cached(self, session: Session, company: Company) -> LookupOutcome | None:
        stmt = select(PhonebookEntry).where(PhonebookEntry.company_id == company.id)
        existing = session.exec(stmt).first()
        if existing is None:
            return None
        return LookupOutcome(
            founder_name=existing.founder_name,
            founder_email=existing.founder_email,
            careers_email=existing.careers_email,
            source=existing.source,
        )

    def _select_outcome(
        self, hits: list[FounderHit], *, domain: str, source: PhonebookSource
    ) -> LookupOutcome | None:
        """Pick the strongest hit from a provider's results, or None if useless."""
        if not hits:
            return None

        # Prefer hits that already include an email.
        for hit in hits:
            if hit.email:
                return LookupOutcome(
                    founder_name=hit.name,
                    founder_email=hit.email,
                    careers_email=careers_fallback_email(domain),
                    source=source,
                )

        # No emails returned, but we have at least one name → pattern guess.
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
        session.add(
            PhonebookEntry(
                company_id=company.id,
                founder_name=outcome.founder_name,
                founder_email=outcome.founder_email,
                careers_email=outcome.careers_email,
                source=outcome.source,
            )
        )
        session.flush()
```

- [ ] **Step 4: Run tests until green**

```bash
uv run pytest tests/test_phonebook -v
```

Expected: PASS for all 7 tests (the 6 from `test_lookup.py` + the earlier provider-order constant test).

- [ ] **Step 5: Type-check + lint + commit**

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
git add src/knockknock/phonebook/lookup.py tests/test_phonebook/test_lookup.py
git commit -m "feat(phonebook): cache-first apollo→hunter→guess→fallback chain"
```

### Task 6.5: Enrich stage

**Files:**
- Create: `src/knockknock/pipeline/enrich.py`
- Create: `tests/test_pipeline/test_enrich_stage.py`

The stage:
1. Selects SCORED jobs ordered by `score DESC, discovered_at ASC` so highest-fit jobs get enriched first.
2. For each job, calls `PhonebookLookup.resolve(...)`.
3. On success → status ENRICHED, audit event.
4. On `ValueError` (no domain) or any unexpected error → status ENRICH_FAILED with `NO_EMAIL_FOUND` reason, audit event, continue.

- [ ] **Step 1: Write failing stage tests**

Create `tests/test_pipeline/test_enrich_stage.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlmodel import Session

from knockknock.db.enums import (
    JobStatus,
    PhonebookSource,
    PipelineStage,
    RejectionReason,
)
from knockknock.db.models import (
    Company,
    JobApplication,
    JobApplicationEvent,
    PhonebookEntry,
)
from knockknock.phonebook.lookup import PhonebookLookup
from knockknock.phonebook.models import FounderHit
from knockknock.pipeline.enrich import EnrichStage


@dataclass
class _StubFounderLookup:
    hits: list[FounderHit]

    def find_founders(self, *, domain: str) -> list[FounderHit]:
        return list(self.hits)


def _make_company(db_session: Session, **kwargs: object) -> Company:
    defaults = {"name": "Acme", "domain": "acme.io"}
    defaults.update(kwargs)  # type: ignore[arg-type]
    company = Company(**defaults)  # type: ignore[arg-type]
    db_session.add(company)
    db_session.flush()
    return company


def _make_scored_job(
    db_session: Session,
    company: Company,
    *,
    score: int = 8,
    title: str = "Backend Engineer",
    source_job_id: str = "j-1",
) -> JobApplication:
    job = JobApplication(
        company_id=company.id,
        title=title,
        location="Bangalore",
        description="x",
        apply_url="https://acme.io/jobs/1",
        source="HN_WHO_IS_HIRING",
        source_job_id=source_job_id,
        status=JobStatus.SCORED,
        score=score,
        discovered_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.flush()
    return job


def test_enrich_advances_jobs_in_score_desc_order(db_session: Session) -> None:
    a = _make_company(db_session, name="LowCo", domain="low.io")
    b = _make_company(db_session, name="HighCo", domain="high.io")
    job_low = _make_scored_job(db_session, a, score=6, title="x", source_job_id="low")
    job_high = _make_scored_job(db_session, b, score=9, title="y", source_job_id="high")

    apollo = _StubFounderLookup(
        hits=[FounderHit("F", "f@high.io", PhonebookSource.APOLLO)]
    )
    hunter = _StubFounderLookup(hits=[])
    chain = PhonebookLookup(apollo=apollo, hunter=hunter)
    stage = EnrichStage(chain=chain)
    result = stage.run(session=db_session, run_id=1)
    db_session.refresh(job_low)
    db_session.refresh(job_high)
    assert result.advanced == 2
    assert job_high.status == JobStatus.ENRICHED
    assert job_low.status == JobStatus.ENRICHED
    # The first event row should be the HIGH-score job (FIFO inside the stage).
    events = (
        db_session.query(JobApplicationEvent)
        .order_by(JobApplicationEvent.id.asc())
        .all()
    )
    assert events[0].job_application_id == job_high.id


def test_enrich_handles_no_domain_company(db_session: Session) -> None:
    company = _make_company(db_session, domain="")
    job = _make_scored_job(db_session, company)
    chain = PhonebookLookup(
        apollo=_StubFounderLookup(hits=[]),
        hunter=_StubFounderLookup(hits=[]),
    )
    stage = EnrichStage(chain=chain)
    result = stage.run(session=db_session, run_id=1)
    db_session.refresh(job)
    assert job.status == JobStatus.ENRICH_FAILED
    assert result.rejected == 1
    event = (
        db_session.query(JobApplicationEvent)
        .filter_by(job_application_id=job.id)
        .one()
    )
    assert event.stage == PipelineStage.ENRICH
    assert event.rejection_reason == RejectionReason.NO_EMAIL_FOUND


def test_enrich_writes_phonebook_row(db_session: Session) -> None:
    company = _make_company(db_session)
    _make_scored_job(db_session, company)
    chain = PhonebookLookup(
        apollo=_StubFounderLookup(
            hits=[FounderHit("Aarav Singh", "aarav@acme.io", PhonebookSource.APOLLO)]
        ),
        hunter=_StubFounderLookup(hits=[]),
    )
    stage = EnrichStage(chain=chain)
    stage.run(session=db_session, run_id=1)
    entry = (
        db_session.query(PhonebookEntry).filter_by(company_id=company.id).one()
    )
    assert entry.founder_email == "aarav@acme.io"
    assert entry.careers_email == "careers@acme.io"
```

- [ ] **Step 2: Confirm failure**

```bash
uv run pytest tests/test_pipeline/test_enrich_stage.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement the stage**

Create `src/knockknock/pipeline/enrich.py`:

```python
"""Enrich stage: SCORED → ENRICHED / ENRICH_FAILED."""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlmodel import Session, select

from knockknock.db.enums import JobStatus, PipelineStage, RejectionReason
from knockknock.db.models import Company, JobApplication, JobApplicationEvent
from knockknock.phonebook.lookup import PhonebookLookup
from knockknock.pipeline.stage import StageResult

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class EnrichStage:
    """Materialise a `phonebook` row for every SCORED job, FIFO within score tier."""

    chain: PhonebookLookup
    name: str = "enrich"

    def run(self, *, session: Session, run_id: int) -> StageResult:
        stmt = (
            select(JobApplication, Company)
            .join(Company, Company.id == JobApplication.company_id)
            .where(JobApplication.status == JobStatus.SCORED)
            .order_by(
                JobApplication.score.desc(),
                JobApplication.discovered_at.asc(),
            )
        )

        advanced = 0
        rejected = 0
        errors = 0

        for job, company in session.exec(stmt).all():
            try:
                outcome = self.chain.resolve(session=session, company=company)
            except ValueError as exc:
                # Domain missing → genuinely unenrichable.
                _record_failure(
                    session,
                    job=job,
                    run_id=run_id,
                    detail=str(exc),
                    reason=RejectionReason.NO_EMAIL_FOUND,
                )
                rejected += 1
                continue
            except Exception as exc:  # noqa: BLE001 - log but don't crash whole stage.
                errors += 1
                log.error(
                    "enrich.unexpected_error",
                    job_id=job.id,
                    error_class=type(exc).__name__,
                    error=str(exc),
                )
                # Leave job at SCORED; next run retries.
                continue

            from_status = job.status
            job.status = JobStatus.ENRICHED
            session.add(
                JobApplicationEvent(
                    job_application_id=job.id,
                    pipeline_run_id=run_id,
                    stage=PipelineStage.ENRICH,
                    from_status=from_status,
                    to_status=JobStatus.ENRICHED,
                    detail=f"source={outcome.source.value}",
                )
            )
            session.flush()
            advanced += 1

        log.info(
            "enrich.summary",
            advanced=advanced,
            rejected=rejected,
            errors=errors,
        )
        return StageResult(
            stage=PipelineStage.ENRICH,
            advanced=advanced,
            rejected=rejected,
            errors=errors,
        )


def _record_failure(
    session: Session,
    *,
    job: JobApplication,
    run_id: int,
    detail: str,
    reason: RejectionReason,
) -> None:
    from_status = job.status
    job.status = JobStatus.ENRICH_FAILED
    session.add(
        JobApplicationEvent(
            job_application_id=job.id,
            pipeline_run_id=run_id,
            stage=PipelineStage.ENRICH,
            from_status=from_status,
            to_status=JobStatus.ENRICH_FAILED,
            rejection_reason=reason,
            detail=detail,
        )
    )
    session.flush()
```

- [ ] **Step 4: Run tests until green**

```bash
uv run pytest tests/test_pipeline/test_enrich_stage.py -v
```

Expected: PASS for all 3 tests.

- [ ] **Step 5: Run full suite + lint + commit**

```bash
uv run pytest -v
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
git add src/knockknock/pipeline/enrich.py tests/test_pipeline/test_enrich_stage.py
git commit -m "feat(pipeline): enrich stage with cache-first phonebook lookup"
```

### Task 6.6: Wire enrich stage into CLI

**Files:**
- Modify: `src/knockknock/__main__.py`

- [ ] **Step 1: Update CLI to build Apollo + Hunter clients and append `EnrichStage`**

Edit `src/knockknock/__main__.py`. After the `ScoreStage` is appended, add:

```python
import httpx

from knockknock.clients.apollo import ApolloClient
from knockknock.clients.hunter import HunterClient
from knockknock.phonebook.lookup import PhonebookLookup
from knockknock.pipeline.enrich import EnrichStage


# ...inside the pipeline command, after ScoreStage is appended...
apollo_key = secrets.get("apollo-api-key")
hunter_key = secrets.get("hunter-api-key")
http_client = httpx.Client()
apollo = ApolloClient(api_key=apollo_key, http=http_client)
hunter = HunterClient(api_key=hunter_key, http=http_client)
phonebook_chain = PhonebookLookup(apollo=apollo, hunter=hunter)
stages.append(EnrichStage(chain=phonebook_chain))
```

> Reuse the single `secrets` client built earlier in this command. Don't construct a second one.

> **Note on lifecycle:** `httpx.Client()` should be closed at process exit. For now we rely on `--once` semantics where the CLI exits cleanly. When we extract `build_pipeline()` in Phase 11, we'll wrap clients in a context manager.

- [ ] **Step 2: Smoke-run CLI**

```bash
KNOCKKNOCK_SECRET_APOLLO_API_KEY=fake \
KNOCKKNOCK_SECRET_HUNTER_API_KEY=fake \
KNOCKKNOCK_SECRET_GEMINI_API_KEY=fake \
uv run knockknock pipeline run --once
```

Expected: pipeline finishes; `enrich` stage reports `advanced=0` (no SCORED rows yet without real Gemini key).

- [ ] **Step 3: Commit**

```bash
git add src/knockknock/__main__.py
git commit -m "feat(cli): wire enrich stage with apollo + hunter clients"
```

---

← [Index](00-index.md) · [Prev: phase-05-gemini-score.md](phase-05-gemini-score.md) · [Next: phase-07-resume-tailor.md](phase-07-resume-tailor.md)
