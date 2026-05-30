"""Tests for the YC Work-at-a-Startup scraper.

YC's workatastartup.com gates job descriptions behind login; we only scrape
the public listings page (titles + companies + locations). Descriptions are
synthesized from public data so the scoring stage has something to work with.
"""

from __future__ import annotations

from pathlib import Path

from knockknock.db.enums import CompanySizeBucket, JobSource
from knockknock.scrapers._playwright import PlaywrightFetcher
from knockknock.scrapers.yc_waas import YcWaasScraper

FIXTURE = Path(__file__).parent.parent / "fixtures" / "yc_waas_listings.html"


def _stub_fetcher() -> PlaywrightFetcher:
    html = FIXTURE.read_text()

    def renderer(url: str, *, wait_selector: str | None = None) -> str:
        return html

    return PlaywrightFetcher(_renderer=renderer)


def test_yc_waas_scraper_emits_india_jobs() -> None:
    """India-tagged + 'India · Remote' jobs emitted; 'Remote (US)' filtered out."""
    scraper = YcWaasScraper(location="India", role="engineer", fetcher=_stub_fetcher())
    jobs = list(scraper.scrape())

    assert {j.source_job_id for j in jobs} == {
        "acme:9001-backend-engineer",
        "zeta:9100-fullstack",
    }
    backend = next(j for j in jobs if j.source_job_id == "acme:9001-backend-engineer")
    assert backend.source == JobSource.YC_WAAS
    assert backend.company_name == "Acme"
    assert backend.company_domain == "acme.com"
    assert backend.company_size_bucket == CompanySizeBucket.SEED
    assert backend.title == "Backend Engineer"
    assert backend.location == "Bengaluru, India"
    assert (
        backend.apply_url
        == "https://www.workatastartup.com/companies/acme/jobs/9001-backend-engineer"
    )
    # Description synthesized from public data only -- no login bypass.
    assert "Backend Engineer" in backend.description
    assert "Acme" in backend.description
    assert "S25" in backend.description


def test_yc_waas_scraper_swallows_render_failure() -> None:
    """If Playwright raises, we log + return without postings rather than crash."""

    def renderer(url: str, *, wait_selector: str | None = None) -> str:
        raise RuntimeError("chromium boom")

    fetcher = PlaywrightFetcher(_renderer=renderer)
    scraper = YcWaasScraper(location="India", role="engineer", fetcher=fetcher)
    assert list(scraper.scrape()) == []


def test_yc_waas_scraper_skips_cards_missing_required_fields() -> None:
    """Cards without a job-link/title are silently skipped."""

    def renderer(url: str, *, wait_selector: str | None = None) -> str:
        return (
            '<div class="directory-list">'
            '<div class="company-card">'
            '<a class="company-name" href="/companies/x">X (YC S25)</a>'
            '<div class="company-batch">S25</div>'
            '<div class="job-row">'
            '<div class="job-location">Bengaluru</div>'
            # Missing job-link entirely -> skip
            "</div></div></div>"
        )

    fetcher = PlaywrightFetcher(_renderer=renderer)
    scraper = YcWaasScraper(location="India", role="engineer", fetcher=fetcher)
    assert list(scraper.scrape()) == []


def test_yc_waas_scraper_exposes_name_and_source() -> None:
    """Scraper Protocol requirements: ``name`` and ``source`` attrs are stable."""
    scraper = YcWaasScraper(location="India", role="engineer", fetcher=_stub_fetcher())
    assert scraper.source == JobSource.YC_WAAS
    assert scraper.name == "yc_waas"
