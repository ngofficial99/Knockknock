"""Unit tests for :class:`HunterClient`.

We stub Hunter's HTTPS endpoint with respx so no real network is hit.
The contract under test:

- ``find_founders(domain=...)`` returns only emails whose ``seniority``
  is ``executive`` AND whose ``confidence`` is above the floor. Junior /
  low-confidence entries are dropped so the chain only ever promotes
  likely founders.
- 5xx / 4xx auth errors raise :class:`HunterLookupError`; the enrich
  stage catches it and falls through to the pattern-guess fallback.
- 429 raises :class:`RateLimitedError` (subclass of
  :class:`HunterLookupError` via :class:`ExternalServiceError`) so the
  chain can decide whether to fall through or surface the rate-limit.
- Empty ``data.emails`` is a *successful* lookup with no hits -- NOT
  an exception.
- ``api_key`` is sent as a querystring parameter (Hunter's documented
  auth scheme), not a header.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from knockknock.clients.hunter import HunterClient, HunterLookupError
from knockknock.db.enums import PhonebookSource
from knockknock.exceptions import RateLimitedError


def _client() -> HunterClient:
    return HunterClient(api_key="test-key", http=httpx.Client())


@respx.mock
def test_hunter_returns_executive_emails() -> None:
    """Junior / low-seniority entries must be filtered out."""
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
    hits = _client().find_founders(domain="acme.io")
    assert len(hits) == 1
    assert hits[0].email == "aarav@acme.io"
    assert hits[0].name == "Aarav Singh"
    assert hits[0].source is PhonebookSource.HUNTER


@respx.mock
def test_hunter_drops_low_confidence_executives() -> None:
    """Executive seniority alone isn't enough -- confidence must clear floor."""
    payload = {
        "data": {
            "domain": "acme.io",
            "emails": [
                {
                    "value": "noise@acme.io",
                    "first_name": "Low",
                    "last_name": "Conf",
                    "position": "CEO",
                    "seniority": "executive",
                    "confidence": 30,
                },
            ],
        }
    }
    respx.get("https://api.hunter.io/v2/domain-search").respond(200, json=payload)
    assert _client().find_founders(domain="acme.io") == []


@respx.mock
def test_hunter_empty_response() -> None:
    respx.get("https://api.hunter.io/v2/domain-search").respond(
        200, json={"data": {"domain": "x.com", "emails": []}}
    )
    assert _client().find_founders(domain="x.com") == []


@respx.mock
def test_hunter_handles_missing_data_key() -> None:
    """Hunter occasionally returns 200 with no ``data`` envelope at all."""
    respx.get("https://api.hunter.io/v2/domain-search").respond(200, json={})
    assert _client().find_founders(domain="x.com") == []


@respx.mock
def test_hunter_sends_api_key_as_query_param() -> None:
    route = respx.get("https://api.hunter.io/v2/domain-search").respond(
        200, json={"data": {"domain": "acme.io", "emails": []}}
    )
    _client().find_founders(domain="acme.io")
    assert route.called
    request = route.calls.last.request
    assert request.url.params["api_key"] == "test-key"
    assert request.url.params["domain"] == "acme.io"
    assert request.url.params["seniority"] == "executive"


@respx.mock
def test_hunter_raises_on_rate_limit() -> None:
    respx.get("https://api.hunter.io/v2/domain-search").respond(429)
    with pytest.raises(RateLimitedError):
        _client().find_founders(domain="acme.io")


@respx.mock
def test_hunter_raises_on_5xx() -> None:
    respx.get("https://api.hunter.io/v2/domain-search").respond(503)
    with pytest.raises(HunterLookupError):
        _client().find_founders(domain="acme.io")


@respx.mock
def test_hunter_raises_on_4xx_auth_error() -> None:
    respx.get("https://api.hunter.io/v2/domain-search").respond(401)
    with pytest.raises(HunterLookupError, match="401"):
        _client().find_founders(domain="acme.io")
