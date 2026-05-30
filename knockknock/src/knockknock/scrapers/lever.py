"""Lever public-postings scraper.

Endpoint: ``GET https://api.lever.co/v0/postings/<slug>?mode=json``
Auth: none.

The endpoint returns a JSON array of postings. Each posting has:
  - ``id``               : opaque string id (e.g. ``"abc-123"``)
  - ``text``             : role title
  - ``categories.location``: free-form location string
  - ``categories.commitment``, ``categories.team``: present but unused here
  - ``hostedUrl``        : public apply URL on ``jobs.lever.co``
  - ``descriptionPlain`` : pre-rendered plain-text description body
  - ``createdAt``        : ms-since-epoch posting timestamp (UTC)

The scraper iterates seed companies, calls the endpoint with a tenacity
retry on transient errors, and yields one :class:`ScrapedJob` per posting
that passes a Bangalore/Bengaluru/Remote/India location filter.

A 5xx for one company's board is retried then logged + skipped at the
per-seed level; the pipeline continues with the next seed company.

``company_domain`` is set to ``<slug>.com`` as a best-effort placeholder;
Phase 6's phonebook resolution replaces this with an authoritative domain
via Apollo/Hunter when possible.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, Final

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from knockknock.db.enums import CompanySizeBucket, JobSource
from knockknock.scrapers.ats_seed import AtsSeedCompany
from knockknock.scrapers.base import ScrapedJob

log = structlog.get_logger(__name__)

_BASE: Final = "https://api.lever.co/v0/postings"
_INDIA_RE: Final = re.compile(r"(bangalore|bengaluru|india|remote)", re.IGNORECASE)


def _ms_to_datetime(ms: int | None) -> datetime | None:
    """Convert a ms-since-epoch int to a timezone-aware UTC datetime."""
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=UTC)
    except (OSError, ValueError, OverflowError):
        return None


class LeverScraper:
    """Yields one :class:`ScrapedJob` per India/Remote posting per seed company."""

    source: JobSource = JobSource.LEVER
    name: str = "lever"

    def __init__(
        self,
        seeds: list[AtsSeedCompany],
        http: httpx.Client,
        *,
        location_filter: re.Pattern[str] = _INDIA_RE,
    ) -> None:
        self._seeds = seeds
        self._http = http
        self._location_filter = location_filter

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.1, min=0.1, max=1.0),
        reraise=True,
    )
    def _fetch(self, slug: str) -> list[dict[str, Any]]:
        url = f"{_BASE}/{slug}"
        resp = self._http.get(url, params={"mode": "json"})
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            raise httpx.HTTPError(f"lever {slug}: response not a JSON array")
        return data

    def scrape(self) -> Iterator[ScrapedJob]:
        for seed in self._seeds:
            try:
                postings = self._fetch(seed.slug)
            except httpx.HTTPError as exc:
                log.warning("lever.fetch_failed", slug=seed.slug, error=str(exc))
                continue
            for raw in postings:
                yielded = self._maybe_build(seed, raw)
                if yielded is not None:
                    yield yielded

    def _maybe_build(self, seed: AtsSeedCompany, raw: dict[str, Any]) -> ScrapedJob | None:
        categories = raw.get("categories") or {}
        location = (categories.get("location") or "").strip()
        if not self._location_filter.search(location):
            return None
        title = (raw.get("text") or "").strip()
        if not title:
            return None
        hosted_url = raw.get("hostedUrl") or ""
        if not hosted_url:
            return None
        job_id = raw.get("id") or ""
        if not job_id:
            return None
        try:
            return ScrapedJob(
                source=JobSource.LEVER,
                source_job_id=f"{seed.slug}:{job_id}",
                company_name=seed.name,
                company_domain=f"{seed.slug}.com",
                company_size_bucket=CompanySizeBucket.UNKNOWN,
                title=title,
                location=location,
                apply_url=hosted_url,
                description=(raw.get("descriptionPlain") or "").strip(),
                posted_at=_ms_to_datetime(raw.get("createdAt")),
            )
        except ValueError as exc:
            log.warning(
                "lever.invalid_job",
                slug=seed.slug,
                job_id=job_id,
                error=str(exc),
            )
            return None
