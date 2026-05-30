"""Tests for the Ashby public job-board scraper."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from knockknock.db.enums import CompanySizeBucket, JobSource
from knockknock.scrapers.ashby import AshbyScraper
from knockknock.scrapers.ats_seed import AtsSeedCompany

FIXTURE = Path(__file__).parent.parent / "fixtures" / "ashby_postman_jobs.json"


@respx.mock
def test_ashby_scraper_filters_by_location() -> None:
    """Bangalore postings emitted (both roles); London is filtered out."""
    payload = json.loads(FIXTURE.read_text())
    respx.get("https://api.ashbyhq.com/posting-api/job-board/postman").mock(
        return_value=httpx.Response(200, json=payload)
    )

    client = httpx.Client(timeout=10)
    scraper = AshbyScraper(
        seeds=[AtsSeedCompany(slug="postman", name="Postman")],
        http=client,
    )
    jobs = list(scraper.scrape())

    # London is filtered out by location; Phase 4 owns title/role filtering, so
    # we keep both Bangalore roles here.
    assert {j.source_job_id for j in jobs} == {"postman:ash-001", "postman:ash-002"}
    backend = next(j for j in jobs if j.source_job_id == "postman:ash-001")
    assert backend.source == JobSource.ASHBY
    assert backend.company_name == "Postman"
    assert backend.company_domain == "postman.com"
    assert backend.company_size_bucket == CompanySizeBucket.UNKNOWN
    assert backend.title == "Staff Backend Engineer"
    assert backend.location == "Bangalore"
    assert backend.apply_url == "https://jobs.ashbyhq.com/postman/ash-001"
    assert "API platform" in backend.description
    assert backend.posted_at is not None
    assert backend.posted_at.year == 2026


@respx.mock
def test_ashby_scraper_skips_company_on_http_error() -> None:
    """5xx on one slug is retried, logged, and skipped; other slugs proceed."""
    respx.get("https://api.ashbyhq.com/posting-api/job-board/broken").mock(
        return_value=httpx.Response(503)
    )
    respx.get("https://api.ashbyhq.com/posting-api/job-board/postman").mock(
        return_value=httpx.Response(200, json={"jobs": []})
    )

    client = httpx.Client(timeout=5)
    scraper = AshbyScraper(
        seeds=[
            AtsSeedCompany(slug="broken", name="Broken"),
            AtsSeedCompany(slug="postman", name="Postman"),
        ],
        http=client,
    )

    jobs = list(scraper.scrape())
    assert jobs == []


@respx.mock
def test_ashby_scraper_exposes_name_and_source() -> None:
    """Scraper Protocol requirements: ``name`` and ``source`` attrs are stable."""
    client = httpx.Client(timeout=5)
    scraper = AshbyScraper(seeds=[], http=client)
    assert scraper.source == JobSource.ASHBY
    assert scraper.name == "ashby"
