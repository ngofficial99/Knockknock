"""Tests for the Wellfound listings scraper.

Wellfound's listings page is React-hydrated; in tests we inject a fake
renderer that returns a saved HTML fixture so Playwright never starts.
"""

from __future__ import annotations

from pathlib import Path

from knockknock.db.enums import CompanySizeBucket, JobSource
from knockknock.scrapers._playwright import PlaywrightFetcher
from knockknock.scrapers.wellfound import WellfoundScraper

FIXTURE = Path(__file__).parent.parent / "fixtures" / "wellfound_listings.html"


def _stub_fetcher() -> PlaywrightFetcher:
    html = FIXTURE.read_text()

    def renderer(url: str, *, wait_selector: str | None = None) -> str:
        return html

    return PlaywrightFetcher(_renderer=renderer)


def test_wellfound_scraper_filters_to_india_and_remote() -> None:
    """India/Remote-Bangalore cards are emitted; SF card is filtered out."""
    scraper = WellfoundScraper(
        location="Bangalore",
        role_types=["engineering"],
        remote=True,
        fetcher=_stub_fetcher(),
    )
    jobs = list(scraper.scrape())

    assert {j.source_job_id for j in jobs} == {
        "12345-backend-engineer",
        "22222-platform-engineer",
    }
    backend = next(j for j in jobs if j.source_job_id == "12345-backend-engineer")
    assert backend.source == JobSource.WELLFOUND
    assert backend.company_name == "Acme Labs"
    assert backend.company_domain == "acme-labs.com"
    assert backend.company_size_bucket == CompanySizeBucket.SERIES_A
    assert backend.title == "Backend Engineer"
    assert backend.location == "Bangalore · Remote"
    assert "Go, Postgres, Kafka" in backend.description
    assert backend.apply_url == "https://wellfound.com/jobs/12345-backend-engineer"
    assert backend.posted_at is None

    platform = next(j for j in jobs if j.source_job_id == "22222-platform-engineer")
    assert platform.company_size_bucket == CompanySizeBucket.SERIES_B


def test_wellfound_scraper_skips_malformed_cards() -> None:
    """Cards missing a job link or title are silently skipped, no crash."""

    def renderer(url: str, *, wait_selector: str | None = None) -> str:
        return (
            '<div data-test="JobSearchResults">'
            '<div data-test="StartupResult">'
            '<a data-test="startup-link" href="/company/x">X</a>'
            # No size, no job-link -> card is silently skipped.
            '<div data-test="JobListingSearchResult">'
            '<div data-test="job-location">Bangalore</div>'
            "</div></div></div>"
        )

    fetcher = PlaywrightFetcher(_renderer=renderer)
    scraper = WellfoundScraper(
        location="Bangalore",
        role_types=["engineering"],
        remote=True,
        fetcher=fetcher,
    )
    assert list(scraper.scrape()) == []


def test_wellfound_scraper_swallows_render_failure() -> None:
    """If Playwright raises, we log + return without postings rather than crash."""

    def renderer(url: str, *, wait_selector: str | None = None) -> str:
        raise RuntimeError("chromium boom")

    fetcher = PlaywrightFetcher(_renderer=renderer)
    scraper = WellfoundScraper(
        location="Bangalore",
        role_types=["engineering"],
        remote=True,
        fetcher=fetcher,
    )
    assert list(scraper.scrape()) == []


def test_wellfound_scraper_exposes_name_and_source() -> None:
    """Scraper Protocol requirements: ``name`` and ``source`` attrs are stable."""
    scraper = WellfoundScraper(
        location="Bangalore",
        role_types=["engineering"],
        remote=True,
        fetcher=_stub_fetcher(),
    )
    assert scraper.source == JobSource.WELLFOUND
    assert scraper.name == "wellfound"
