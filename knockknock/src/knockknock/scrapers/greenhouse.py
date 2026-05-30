"""Greenhouse public-board scraper.

Endpoint: ``GET https://boards-api.greenhouse.io/v1/boards/<slug>/jobs?content=true``
Auth: none.

The endpoint returns a flat ``{"jobs": [...]}`` payload. Each job has:
  - ``id``           : numeric Greenhouse posting ID
  - ``title``        : role title
  - ``updated_at``   : ISO 8601 UTC ("Z" suffix)
  - ``location.name``: free-form location string ("Bengaluru, India",
                       "Remote — India", "San Francisco, CA", ...)
  - ``absolute_url`` : public apply URL on ``boards.greenhouse.io``
  - ``content``      : HTML-encoded description body

The scraper iterates seed companies, calls the endpoint with a tenacity
retry on transient errors, parses the HTML description to plain text via
selectolax, and yields one :class:`ScrapedJob` per posting that passes a
Bangalore/Bengaluru/Remote/India location filter.

A 5xx for one company's board is retried then logged + skipped at the
per-seed level; the pipeline continues with the next seed company.

``company_domain`` is set to ``<slug>.com`` as a best-effort placeholder.
Phase 6's phonebook resolution replaces this with an authoritative
domain via Apollo/Hunter when possible.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterator
from datetime import datetime
from typing import Any, Final

import httpx
import structlog
from selectolax.parser import HTMLParser
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

_BASE: Final = "https://boards-api.greenhouse.io/v1/boards"
_INDIA_RE: Final = re.compile(r"(bangalore|bengaluru|india|remote)", re.IGNORECASE)


def _html_to_text(body: str) -> str:
    """Render an HTML job description body to plain text.

    Greenhouse returns the description as a JSON string with HTML entities
    encoded (``&lt;p&gt;...&lt;/p&gt;``). We first unescape the entities to
    recover the underlying HTML markup, then strip tags via selectolax.

    Block-level tags (``<p>``, ``<div>``, ``<li>``, ...) are turned into a
    single newline so paragraph structure survives downstream scoring;
    inline tags (``<b>``, ``<i>``, ``<a>``, ``<span>``) are joined with a
    space so that ``Build the <b>payment</b> rails`` reads as one phrase.
    """
    if not body:
        return ""
    unescaped = html.unescape(body)
    parser = HTMLParser(unescaped)
    parts: list[str] = []
    if parser.body is None:
        return ""
    for node in parser.body.traverse(include_text=True):
        if node.tag == "-text":
            text = (node.text() or "").strip()
            if text:
                parts.append(text)
        elif node.tag in _BLOCK_TAGS:
            parts.append("\n")
    joined = " ".join(part if part != "\n" else "\n" for part in parts)
    # Tidy: a space immediately before a newline becomes just the newline,
    # then collapse runs of blank lines.
    joined = re.sub(r" *\n *", "\n", joined)
    joined = re.sub(r"\n{3,}", "\n\n", joined)
    return joined.strip()


_BLOCK_TAGS: Final = frozenset(
    {
        "p",
        "div",
        "br",
        "li",
        "ul",
        "ol",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
        "header",
        "footer",
        "table",
        "tr",
    }
)


def _parse_updated_at(value: str | None) -> datetime | None:
    """Greenhouse returns a trailing ``Z``; ``fromisoformat`` handles offsets in 3.11+."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class GreenhouseScraper:
    """Yields one :class:`ScrapedJob` per India/Remote posting per seed company."""

    source: JobSource = JobSource.GREENHOUSE
    name: str = "greenhouse"

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
        url = f"{_BASE}/{slug}/jobs"
        resp = self._http.get(url, params={"content": "true"})
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise httpx.HTTPError(f"greenhouse {slug}: response not a JSON object")
        return data

    def scrape(self) -> Iterator[ScrapedJob]:
        for seed in self._seeds:
            try:
                payload = self._fetch(seed.slug)
            except httpx.HTTPError as exc:
                log.warning("greenhouse.fetch_failed", slug=seed.slug, error=str(exc))
                continue
            jobs_raw = payload.get("jobs", [])
            if not isinstance(jobs_raw, list):
                continue
            for raw in jobs_raw:
                yielded = self._maybe_build(seed, raw)
                if yielded is not None:
                    yield yielded

    def _maybe_build(self, seed: AtsSeedCompany, raw: dict[str, Any]) -> ScrapedJob | None:
        location = (raw.get("location") or {}).get("name") or ""
        if not self._location_filter.search(location):
            return None
        title = (raw.get("title") or "").strip()
        if not title:
            return None
        absolute_url = raw.get("absolute_url") or ""
        if not absolute_url:
            return None
        job_id = raw.get("id")
        if job_id is None:
            return None
        try:
            return ScrapedJob(
                source=JobSource.GREENHOUSE,
                source_job_id=f"{seed.slug}:{job_id}",
                company_name=seed.name,
                company_domain=f"{seed.slug}.com",
                company_size_bucket=CompanySizeBucket.UNKNOWN,
                title=title,
                location=location,
                apply_url=absolute_url,
                description=_html_to_text(raw.get("content") or ""),
                posted_at=_parse_updated_at(raw.get("updated_at")),
            )
        except ValueError as exc:
            log.warning(
                "greenhouse.invalid_job",
                slug=seed.slug,
                job_id=job_id,
                error=str(exc),
            )
            return None
