"""Tests for the Lever public-postings scraper."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from knockknock.db.enums import CompanySizeBucket, JobSource
from knockknock.scrapers.ats_seed import AtsSeedCompany
from knockknock.scrapers.lever import LeverScraper

FIXTURE = Path(__file__).parent.parent / "fixtures" / "lever_groww_postings.json"


@respx.mock
def test_lever_scraper_filters_to_india_and_remote() -> None:
    """India + Remote-India postings emitted; NY posting filtered out."""
    payload = json.loads(FIXTURE.read_text())
    respx.get("https://api.lever.co/v0/postings/groww").mock(
        return_value=httpx.Response(200, json=payload)
    )

    client = httpx.Client(timeout=10)
    scraper = LeverScraper(
        seeds=[AtsSeedCompany(slug="groww", name="Groww")],
        http=client,
    )
    jobs = list(scraper.scrape())

    assert len(jobs) == 2
    bangalore = next(j for j in jobs if j.location == "Bengaluru")
    assert bangalore.source == JobSource.LEVER
    assert bangalore.source_job_id == "groww:abc-123"
    assert bangalore.company_name == "Groww"
    assert bangalore.company_domain == "groww.com"
    assert bangalore.company_size_bucket == CompanySizeBucket.UNKNOWN
    assert bangalore.title == "Backend Engineer (Distributed Systems)"
    assert bangalore.apply_url == "https://jobs.lever.co/groww/abc-123"
    assert "trading engine" in bangalore.description
    assert bangalore.posted_at is not None
    assert bangalore.posted_at.year == 2025  # 1748390400000ms == 2025-05-28 UTC


@respx.mock
def test_lever_scraper_skips_company_on_http_error() -> None:
    """5xx on one slug is retried, logged, and skipped; other slugs proceed."""
    respx.get("https://api.lever.co/v0/postings/broken").mock(return_value=httpx.Response(503))
    respx.get("https://api.lever.co/v0/postings/groww").mock(
        return_value=httpx.Response(200, json=[])
    )

    client = httpx.Client(timeout=5)
    scraper = LeverScraper(
        seeds=[
            AtsSeedCompany(slug="broken", name="Broken"),
            AtsSeedCompany(slug="groww", name="Groww"),
        ],
        http=client,
    )

    jobs = list(scraper.scrape())
    assert jobs == []


@respx.mock
def test_lever_scraper_exposes_name_and_source() -> None:
    """Scraper Protocol requirements: ``name`` and ``source`` attrs are stable."""
    client = httpx.Client(timeout=5)
    scraper = LeverScraper(seeds=[], http=client)
    assert scraper.source == JobSource.LEVER
    assert scraper.name == "lever"
