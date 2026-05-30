"""Scraper protocol and the dataclass it must produce."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable
from urllib.parse import urlparse

from knockknock.db.enums import CompanySizeBucket, JobSource
from knockknock.exceptions import KnockknockError


class ScraperError(KnockknockError):
    """A scraper failed in a way that's expected to be transient or recoverable.

    Examples:
    - Playwright browser launch died,
    - ``storage_state.json`` missing for an authenticated source,
    - the page's expected selector never appeared within ``timeout_ms``.

    The discover stage catches this per-scraper so one source's failure
    doesn't kill the whole hourly run; Phase 11's daily digest surfaces
    the failure for the operator to act on.
    """


def _normalise_domain(value: str) -> str:
    """Reduce a URL or hostname to a bare lowercase domain."""
    cleaned = value.strip().lower()
    if "://" in cleaned:
        cleaned = urlparse(cleaned).netloc or cleaned
    cleaned = cleaned.split("/")[0]
    if cleaned.startswith("www."):
        cleaned = cleaned[4:]
    return cleaned


@dataclass(frozen=True, slots=True)
class ScrapedJob:
    """Normalised payload that every scraper yields.

    Salary fields are *best-effort* and may all be ``None`` — they are
    used as a soft scoring signal downstream, not a hard pre-filter gate.
    ``salary_raw`` retains the unparsed source substring so an auditor can
    sanity-check what the extractor matched against.
    """

    source: JobSource
    source_job_id: str
    company_name: str
    company_domain: str
    company_size_bucket: CompanySizeBucket
    title: str
    location: str
    apply_url: str
    description: str
    posted_at: datetime | None
    tags: list[str] = field(default_factory=list)
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None  # "USD" / "INR" / "EUR" / "GBP"
    salary_period: str | None = None  # "annual" / "monthly" / "hourly"
    salary_raw: str | None = None

    def __post_init__(self) -> None:
        if not self.apply_url:
            raise ValueError("apply_url is required")
        if not self.source_job_id:
            raise ValueError("source_job_id is required")
        object.__setattr__(self, "company_domain", _normalise_domain(self.company_domain))


@runtime_checkable
class Scraper(Protocol):
    """A source-specific scraper.

    ``scrape()`` (renamed from ``fetch`` in Phase 10) returns a sync
    iterator. Sources that do async work internally (Playwright)
    bridge to sync at this boundary via ``asyncio.run`` because the
    pipeline runner is sync.

    ``name`` is a short, lowercase, log-friendly identifier (e.g.
    ``"hn"``, ``"greenhouse"``). Use it in structured logs instead of
    ``source.value.lower()``.
    """

    source: JobSource
    name: str

    def scrape(self) -> Iterator[ScrapedJob]: ...
