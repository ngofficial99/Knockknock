"""Wellfound (ex-AngelList Talent) job-listings scraper.

Wellfound does not publish a stable public JSON API; the job listings
page is server-rendered and hydrated with React. This scraper renders
the public search URL via :class:`PlaywrightFetcher`, then parses cards
with selectolax.

Selectors target stable ``data-test`` attributes:
  - ``[data-test="JobSearchResults"]``      — top-level results container
  - ``[data-test="StartupResult"]``         — one block per company
  - ``[data-test="startup-link"]``          — anchor with ``/company/<slug>``
  - ``[data-test="startup-size"]``          — e.g. ``"11-50 employees"``
  - ``[data-test="JobListingSearchResult"]``— one block per posting
  - ``[data-test="job-link"]``              — anchor with ``/jobs/<id-slug>``
  - ``[data-test="job-location"]``          — free-form location string
  - ``[data-test="job-description"]``       — short description blurb

Layout drift is inevitable, so the parser is **defensive**: any card
missing a required field is silently skipped (no crash), and a Playwright
render failure is logged + swallowed (the pipeline continues with other
scrapers).

``company_domain`` is set to ``<slug>.com`` as a best-effort placeholder;
Phase 6's phonebook resolution replaces this with the authoritative
domain via Apollo/Hunter when possible.

This scraper does not bypass any login or paywall — Wellfound's
job-search pages are publicly indexable. Live runs are gated behind
``pytest -m live`` (Task 10.11 smoke run); unit tests use a saved HTML
fixture via the injectable ``PlaywrightFetcher._renderer``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Final
from urllib.parse import quote

import structlog
from selectolax.parser import HTMLParser, Node

from knockknock.db.enums import CompanySizeBucket, JobSource
from knockknock.scrapers._playwright import PlaywrightFetcher
from knockknock.scrapers.base import ScrapedJob

log = structlog.get_logger(__name__)

_BASE: Final = "https://wellfound.com"
_INDIA_RE: Final = re.compile(r"(bangalore|bengaluru|india|remote)", re.IGNORECASE)
_SIZE_BUCKETS: Final[dict[str, CompanySizeBucket]] = {
    "1-10": CompanySizeBucket.SEED,
    "11-50": CompanySizeBucket.SERIES_A,
    "51-200": CompanySizeBucket.SERIES_B,
    "201-500": CompanySizeBucket.SERIES_C,
    "501-1000": CompanySizeBucket.LATE_STAGE,
}


def _size_bucket(label: str) -> CompanySizeBucket:
    """Map a Wellfound size label (``"11-50 employees"``) to a stage bucket."""
    cleaned = label.strip().lower().replace("employees", "").strip()
    for prefix, bucket in _SIZE_BUCKETS.items():
        if cleaned.startswith(prefix):
            return bucket
    return CompanySizeBucket.UNKNOWN


def _text_or_empty(node: Node | None) -> str:
    return node.text(strip=True) if node is not None else ""


def _attr_or_empty(node: Node | None, name: str) -> str:
    if node is None:
        return ""
    value = node.attributes.get(name)
    return value or ""


class WellfoundScraper:
    """Yields one :class:`ScrapedJob` per India/Remote posting from one search."""

    source: JobSource = JobSource.WELLFOUND
    name: str = "wellfound"

    def __init__(
        self,
        *,
        location: str,
        role_types: list[str],
        remote: bool,
        fetcher: PlaywrightFetcher,
    ) -> None:
        self._location = location
        self._role_types = role_types
        self._remote = remote
        self._fetcher = fetcher

    def _build_url(self) -> str:
        params = [f"location={quote(self._location)}"]
        if self._role_types:
            params.append("roles=" + ",".join(quote(r) for r in self._role_types))
        if self._remote:
            params.append("remote=true")
        return f"{_BASE}/jobs?{'&'.join(params)}"

    def scrape(self) -> Iterator[ScrapedJob]:
        url = self._build_url()
        try:
            html = self._fetcher.render(url, wait_selector='[data-test="JobSearchResults"]')
        except Exception as exc:
            log.warning("wellfound.render_failed", url=url, error=str(exc))
            return
        tree = HTMLParser(html)
        for startup in tree.css('[data-test="StartupResult"]'):
            yield from self._iter_startup_jobs(startup)

    def _iter_startup_jobs(self, startup: Node) -> Iterator[ScrapedJob]:
        startup_link = startup.css_first('[data-test="startup-link"]')
        company_name = _text_or_empty(startup_link)
        if not company_name:
            return
        company_href = _attr_or_empty(startup_link, "href")
        company_slug = company_href.removeprefix("/company/").strip("/")
        if not company_slug:
            return
        size_label = _text_or_empty(startup.css_first('[data-test="startup-size"]'))
        size_bucket = _size_bucket(size_label)

        for card in startup.css('[data-test="JobListingSearchResult"]'):
            location = _text_or_empty(card.css_first('[data-test="job-location"]'))
            if not _INDIA_RE.search(location):
                continue
            link = card.css_first('[data-test="job-link"]')
            href = _attr_or_empty(link, "href")
            title = _text_or_empty(link)
            description = _text_or_empty(card.css_first('[data-test="job-description"]'))
            if not (href and title):
                continue
            source_job_id = href.removeprefix("/jobs/").strip("/")
            if not source_job_id:
                continue
            try:
                yield ScrapedJob(
                    source=JobSource.WELLFOUND,
                    source_job_id=source_job_id,
                    company_name=company_name,
                    company_domain=f"{company_slug}.com",
                    company_size_bucket=size_bucket,
                    title=title,
                    location=location,
                    apply_url=f"{_BASE}{href}",
                    description=description,
                    posted_at=None,  # Wellfound lists relative time only; skip
                )
            except ValueError as exc:
                log.warning(
                    "wellfound.invalid_job",
                    company=company_name,
                    job_href=href,
                    error=str(exc),
                )
                continue
