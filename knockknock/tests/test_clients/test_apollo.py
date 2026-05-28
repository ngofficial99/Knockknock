"""Unit tests for :class:`ApolloClient`.

We stub Apollo's HTTPS endpoint with respx so no real network is hit.
The contract under test:

- ``find_founders(domain=...)`` returns only people whose ``title``
  matches the founder regex (CEO/Founder/Co-founder/CTO/etc.) -- regular
  engineers are filtered out so the chain only ever promotes likely
  founders.
- ``email`` is preserved if Apollo provided one (Apollo free-tier often
  returns the name but no email; that's fine, downstream guesser can
  derive one).
- 5xx and 429 responses raise :class:`ApolloLookupError`; the enrich
  stage catches it and falls through to Hunter.
- Empty ``organizations`` is a *successful* lookup with no hits -- NOT
  an exception. The chain treats it the same as 200-with-no-founders.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from knockknock.clients.apollo import ApolloClient, ApolloLookupError
from knockknock.db.enums import PhonebookSource


def _client() -> ApolloClient:
    """Build a client with a transport-only httpx.Client.

    respx's @respx.mock decorator intercepts at the transport layer so
    any non-mocked URL would raise -- we don't need a real base_url.
    """
    return ApolloClient(api_key="test-key", http=httpx.Client())


@respx.mock
def test_apollo_returns_founders_only() -> None:
    """Non-founder titles (e.g. Engineer) must be filtered out."""
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
    respx.post("https://api.apollo.io/v1/organizations/search").respond(200, json=payload)
    hits = _client().find_founders(domain="acme.io")
    assert len(hits) == 1
    assert hits[0].name == "Aarav Singh"
    assert hits[0].email == "aarav@acme.io"
    assert hits[0].source is PhonebookSource.APOLLO


@respx.mock
def test_apollo_keeps_founder_without_email() -> None:
    """Apollo free tier often omits emails -- name-only hits still count."""
    payload = {
        "organizations": [
            {
                "primary_domain": "acme.io",
                "people": [
                    {"name": "Aarav Singh", "title": "Founder", "email": None},
                ],
            }
        ]
    }
    respx.post("https://api.apollo.io/v1/organizations/search").respond(200, json=payload)
    hits = _client().find_founders(domain="acme.io")
    assert len(hits) == 1
    assert hits[0].email is None


@respx.mock
def test_apollo_returns_empty_when_no_orgs() -> None:
    respx.post("https://api.apollo.io/v1/organizations/search").respond(
        200, json={"organizations": []}
    )
    assert _client().find_founders(domain="missing.com") == []


@respx.mock
def test_apollo_returns_empty_when_org_has_no_founders() -> None:
    """200 with only non-founder people is a clean empty, not an error."""
    payload = {
        "organizations": [
            {
                "primary_domain": "acme.io",
                "people": [
                    {"name": "Bob Engineer", "title": "Software Engineer"},
                ],
            }
        ]
    }
    respx.post("https://api.apollo.io/v1/organizations/search").respond(200, json=payload)
    assert _client().find_founders(domain="acme.io") == []


@respx.mock
def test_apollo_prefers_org_with_matching_primary_domain() -> None:
    """When Apollo returns multiple orgs, the one whose primary_domain
    matches our requested domain is the source of truth -- others are
    ignored (Apollo's fuzzy match sometimes returns the wrong company)."""
    payload = {
        "organizations": [
            {
                "primary_domain": "acme-clone.io",
                "people": [{"name": "Wrong Person", "title": "Founder"}],
            },
            {
                "primary_domain": "acme.io",
                "people": [{"name": "Aarav Singh", "title": "Founder"}],
            },
        ]
    }
    respx.post("https://api.apollo.io/v1/organizations/search").respond(200, json=payload)
    hits = _client().find_founders(domain="acme.io")
    assert len(hits) == 1
    assert hits[0].name == "Aarav Singh"


@respx.mock
def test_apollo_sends_api_key_header() -> None:
    route = respx.post("https://api.apollo.io/v1/organizations/search").respond(
        200, json={"organizations": []}
    )
    _client().find_founders(domain="acme.io")
    assert route.called
    request = route.calls.last.request
    assert request.headers["x-api-key"] == "test-key"


@respx.mock
def test_apollo_raises_on_5xx() -> None:
    respx.post("https://api.apollo.io/v1/organizations/search").respond(503)
    with pytest.raises(ApolloLookupError):
        _client().find_founders(domain="acme.io")


@respx.mock
def test_apollo_raises_on_rate_limit() -> None:
    respx.post("https://api.apollo.io/v1/organizations/search").respond(429)
    with pytest.raises(ApolloLookupError, match="rate"):
        _client().find_founders(domain="acme.io")


@respx.mock
def test_apollo_raises_on_4xx_auth_error() -> None:
    respx.post("https://api.apollo.io/v1/organizations/search").respond(401)
    with pytest.raises(ApolloLookupError, match="401"):
        _client().find_founders(domain="acme.io")
