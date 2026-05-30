"""Ashby public job-board scraper.

Endpoint: ``GET https://api.ashbyhq.com/posting-api/job-board/<slug>?includeCompensation=false``
Auth: none.

The endpoint returns a ``{"jobs": [...]}`` payload. Each job has:
  - ``id``               : opaque string id (e.g. ``"ash-001"``)
  - ``title``            : role title
  - ``locationName``     : free-form location string
  - ``employmentType``   : ``"FullTime"`` / ``"PartTime"`` / ``"Contract"`` / ...
  - ``jobUrl``           : public apply URL on ``jobs.ashbyhq.com``
  - ``descriptionPlain`` : pre-rendered plain-text description body
  - ``publishedAt``      : ISO 8601 UTC (``"Z"`` suffix)

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
from datetime import datetime
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

_BASE: Final = "https://api.ashbyhq.com/posting-api/job-board"
_INDIA_RE: Final = re.compile(r"(bangalore|bengaluru|india|remote)", re.IGNORECASE)


def _parse_iso(value: str | None) -> datetime | None:
    """Ashby returns a trailing ``Z``; ``fromisoformat`` handles offsets in 3.11+."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class AshbyScraper:
    """Yields one :class:`ScrapedJob` per India/Remote posting per seed company."""

    source: JobSource = JobSource.ASHBY
    name: str = "ashby"

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
    def _fetch(self, slug: str) -> dict[str, Any]:
        url = f"{_BASE}/{slug}"
        resp = self._http.get(url, params={"includeCompensation": "false"})
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise httpx.HTTPError(f"ashby {slug}: response not a JSON object")
        return data

    def scrape(self) -> Iterator[ScrapedJob]:
        for seed in self._seeds:
            try:
                payload = self._fetch(seed.slug)
            except httpx.HTTPError as exc:
                log.warning("ashby.fetch_failed", slug=seed.slug, error=str(exc))
                continue
            jobs_raw = payload.get("jobs", [])
            if not isinstance(jobs_raw, list):
                continue
            for raw in jobs_raw:
                yielded = self._maybe_build(seed, raw)
                if yielded is not None:
                    yield yielded

    def _maybe_build(self, seed: AtsSeedCompany, raw: dict[str, Any]) -> ScrapedJob | None:
        location = (raw.get("locationName") or "").strip()
        if not self._location_filter.search(location):
            return None
        title = (raw.get("title") or "").strip()
        if not title:
            return None
        job_url = raw.get("jobUrl") or ""
        if not job_url:
            return None
        job_id = raw.get("id") or ""
        if not job_id:
            return None
        try:
            return ScrapedJob(
                source=JobSource.ASHBY,
                source_job_id=f"{seed.slug}:{job_id}",
                company_name=seed.name,
                company_domain=f"{seed.slug}.com",
                company_size_bucket=CompanySizeBucket.UNKNOWN,
                title=title,
                location=location,
                apply_url=job_url,
                description=(raw.get("descriptionPlain") or "").strip(),
                posted_at=_parse_iso(raw.get("publishedAt")),
            )
        except ValueError as exc:
            log.warning(
                "ashby.invalid_job",
                slug=seed.slug,
                job_id=job_id,
                error=str(exc),
            )
            return None
