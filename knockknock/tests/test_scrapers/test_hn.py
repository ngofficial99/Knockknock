from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from knockknock.db.enums import JobSource
from knockknock.scrapers.hn import HNScraper

FIX = Path(__file__).parent.parent / "fixtures"

# Algolia recency endpoint - HNScraper queries this so we always pick
# the most recent "Who is hiring" threads rather than the popular ones.
SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"


@respx.mock
def test_hn_scraper_finds_latest_thread_and_parses_jobs() -> None:
    thread_payload = json.loads((FIX / "hn_thread.json").read_text())
    comments_payload = json.loads((FIX / "hn_comments.json").read_text())

    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=thread_payload))
    respx.get("https://hn.algolia.com/api/v1/items/9000001").mock(
        return_value=httpx.Response(200, json=comments_payload)
    )

    scraper = HNScraper(months_lookback=1)
    jobs = list(scraper.fetch())

    sources = {j.source for j in jobs}
    assert sources == {JobSource.HN}
    titles = {j.title for j in jobs}
    assert "Backend Engineer" in titles
    # company_domain is derived from the apply URL host when not in header
    assert any("acme.test" in j.company_domain for j in jobs)


@respx.mock
def test_hn_scraper_emits_jobs_when_header_lacks_paren_domain() -> None:
    """Most real Who-is-hiring comments don't put `(domain.tld)` in the header.

    The scraper should still emit them as long as there is a parseable
    pipe-separated header and at least one URL we can use for the domain.
    """
    thread_payload = json.loads((FIX / "hn_thread.json").read_text())
    comments_payload = json.loads((FIX / "hn_comments.json").read_text())

    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=thread_payload))
    respx.get("https://hn.algolia.com/api/v1/items/9000001").mock(
        return_value=httpx.Response(200, json=comments_payload)
    )

    scraper = HNScraper(months_lookback=1)
    jobs = list(scraper.fetch())
    job_ids = {j.source_job_id for j in jobs}
    # 9000011 (BetaCo) has no (domain.tld) but has a beta.test URL -> emitted.
    assert "9000011" in job_ids
    # 9000012 has no pipes at all -> skipped.
    assert "9000012" not in job_ids


@respx.mock
def test_hn_scraper_handles_5xx_with_retry_then_fails() -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(500))
    scraper = HNScraper(months_lookback=1)
    with pytest.raises(httpx.HTTPError):
        list(scraper.fetch())


# ---- Phase-3 follow-up: field-mapping bug fix (2026-05-28) ----
#
# Background: a 200-job live HN smoke run during Phase-4 feedback-loop tuning
# revealed the positional ``title=parts[1], location=parts[2]`` assignment
# breaks for many real "Who is hiring?" headers. Several common shapes:
#
#   "Company | Location | Mode | URL"          -> title was Location (wrong)
#   "Company | Mode | Location | type"         -> title was Mode (wrong)
#   "Company | Location | Mode | Role"         -> title was Location, role lost
#   "Company | Location | Mode"  (3 parts)     -> title was Location, no role
#
# The fixed parser classifies each pipe-separated segment heuristically by
# CONTENT (role tokens, location tokens, employment-type tokens) instead of
# trusting position. See ``src/knockknock/scrapers/hn.py`` for the matchers.


def _comment(comment_id: int, text: str, created_at_i: int = 1746090000) -> dict:
    """Helper to build a single HN-comment dict in the Algolia shape."""
    return {"id": comment_id, "author": "x", "created_at_i": created_at_i, "text": text}


def _parse_one(html: str) -> object:
    """Run the scraper's parser on a single comment HTML body."""
    scraper = HNScraper()
    return scraper._parse_comment(_comment(1, html))


def test_location_in_middle_segment_is_classified_as_location() -> None:
    """``Company | Location | Mode | Role`` -- role is last, location is parts[1]."""
    html = (
        "Acme | San Francisco, CA | ONSITE | Senior Backend Engineer<p>"
        'Apply at <a href="https://acme.test/jobs">link</a></p>'
    )
    job = _parse_one(html)
    assert job is not None
    assert job.title == "Senior Backend Engineer"
    assert "San Francisco" in job.location


def test_mode_only_header_keeps_location_segment_as_location() -> None:
    """``Company | REMOTE | DevOps Engineer`` -- mode is parts[1], role is parts[2]."""
    html = (
        "Acme | REMOTE | DevOps / Platform Engineer<p>"
        'Apply at <a href="https://acme.test/jobs">link</a></p>'
    )
    job = _parse_one(html)
    assert job is not None
    assert "Engineer" in job.title
    # "REMOTE" qualifies as a location/mode token; either accepting it as
    # the location or normalising to "Remote" is fine for downstream.
    assert "remote" in job.location.lower()


def test_role_first_then_location_is_still_supported() -> None:
    """``Company | Role | Location | Mode`` -- the original positional shape."""
    html = (
        "Acme | Backend Engineer | Bengaluru | ONSITE<p>"
        'Apply at <a href="https://acme.test/jobs">link</a></p>'
    )
    job = _parse_one(html)
    assert job is not None
    assert job.title == "Backend Engineer"
    assert "Bengaluru" in job.location


def test_three_part_header_with_location_then_mode_recovers_location() -> None:
    """``Company | Location | Mode`` (3 parts, no role).

    No role segment exists -- the parser must signal this so the pre-filter
    can reject the row, but the LOCATION must still be the real location
    (not the employment-mode segment), so location-side telemetry stays honest.
    """
    html = (
        "Acme | Bengaluru, India (Hybrid) | Full-time<p>"
        'Apply at <a href="https://acme.test/jobs">link</a></p>'
    )
    job = _parse_one(html)
    assert job is not None
    # title must NOT be the location segment; it must be a sentinel placeholder.
    assert "bengaluru" not in job.title.lower()
    assert "Bengaluru" in job.location


def test_employment_type_segment_is_not_picked_as_location() -> None:
    """Segments like 'Full-time', 'Part-time', 'Contract' must not become location."""
    html = (
        "Acme | Backend Engineer | Full-time | Remote (India)<p>"
        'Apply at <a href="https://acme.test/jobs">link</a></p>'
    )
    job = _parse_one(html)
    assert job is not None
    assert job.title == "Backend Engineer"
    # location must be the real geography, not "Full-time".
    assert "remote" in job.location.lower() or "india" in job.location.lower()
    assert "full-time" not in job.location.lower()


def test_url_segment_in_header_is_skipped() -> None:
    """A URL appearing as a pipe segment must not be picked as title/location."""
    html = (
        "Acme | https://acme.test/jobs | Backend Engineer | Bengaluru<p>"
        'See <a href="https://acme.test/jobs">link</a></p>'
    )
    job = _parse_one(html)
    assert job is not None
    assert "http" not in job.title.lower()
    assert "http" not in job.location.lower()
    assert job.title == "Backend Engineer"
    assert "Bengaluru" in job.location


def test_company_with_paren_yc_tag_still_extracts_company() -> None:
    """``Company (YC 19) | REMOTE | role-less header`` was producing title=REMOTE.

    The fix must not regress company-name extraction in the common YC-tag case.
    """
    html = (
        "BetaCo (YC W22) | REMOTE | Full-time<p>"
        'Apply at <a href="https://beta.test/jobs">link</a></p>'
    )
    job = _parse_one(html)
    assert job is not None
    assert "BetaCo" in job.company_name
    assert "remote" in job.location.lower()
