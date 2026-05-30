"""Tests for the Greenhouse public-board scraper."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from knockknock.db.enums import CompanySizeBucket, JobSource
from knockknock.scrapers.ats_seed import AtsSeedCompany
from knockknock.scrapers.greenhouse import GreenhouseScraper

FIXTURE = Path(__file__).parent.parent / "fixtures" / "greenhouse_razorpay_jobs.json"


@respx.mock
def test_greenhouse_scraper_filters_to_india_and_remote() -> None:
    """India + Remote postings are emitted; SF posting is filtered out."""
    payload = json.loads(FIXTURE.read_text())
    respx.get("https://boards-api.greenhouse.io/v1/boards/razorpay/jobs").mock(
        return_value=httpx.Response(200, json=payload)
    )

    client = httpx.Client(timeout=10)
    scraper = GreenhouseScraper(
        seeds=[AtsSeedCompany(slug="razorpay", name="Razorpay")],
        http=client,
    )
    jobs = list(scraper.scrape())

    assert len(jobs) == 2
    bangalore = next(j for j in jobs if "Bengaluru" in j.location)
    assert bangalore.source == JobSource.GREENHOUSE
    assert bangalore.source_job_id == "razorpay:4567890"
    assert bangalore.company_name == "Razorpay"
    assert bangalore.company_domain == "razorpay.com"
    assert bangalore.title == "Senior Backend Engineer"
    assert "payment rails for India" in bangalore.description
    assert bangalore.apply_url == "https://boards.greenhouse.io/razorpay/jobs/4567890"
    assert bangalore.company_size_bucket == CompanySizeBucket.UNKNOWN


@respx.mock
def test_greenhouse_scraper_skips_company_on_http_error() -> None:
    """5xx on one slug is retried, then logged and skipped; other slugs proceed."""
    respx.get("https://boards-api.greenhouse.io/v1/boards/broken/jobs").mock(
        return_value=httpx.Response(503)
    )
    respx.get("https://boards-api.greenhouse.io/v1/boards/razorpay/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": []})
    )

    client = httpx.Client(timeout=5)
    scraper = GreenhouseScraper(
        seeds=[
            AtsSeedCompany(slug="broken", name="Broken"),
            AtsSeedCompany(slug="razorpay", name="Razorpay"),
        ],
        http=client,
    )

    # 5xx is retried then swallowed at the per-seed level; pipeline continues.
    jobs = list(scraper.scrape())
    assert jobs == []


@respx.mock
def test_greenhouse_scraper_exposes_name_and_source() -> None:
    """Scraper Protocol requirements: ``name`` and ``source`` attrs are stable."""
    client = httpx.Client(timeout=5)
    scraper = GreenhouseScraper(seeds=[], http=client)
    assert scraper.source == JobSource.GREENHOUSE
    assert scraper.name == "greenhouse"
