from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from knockknock.db.enums import CompanySizeBucket, JobSource
from knockknock.scrapers.base import ScrapedJob, Scraper


def test_scraped_job_normalises_domain() -> None:
    job = ScrapedJob(
        source=JobSource.HN,
        source_job_id="hn-1",
        company_name="Acme Labs",
        company_domain="HTTPS://Acme.Test/path",
        company_size_bucket=CompanySizeBucket.SERIES_A,
        title="Backend Engineer",
        location="Bengaluru",
        apply_url="https://acme.test/jobs/1",
        description="Build things",
        posted_at=datetime(2026, 5, 28, tzinfo=UTC),
    )
    assert job.company_domain == "acme.test"


def test_scraper_protocol_requires_scrape_and_name() -> None:
    """The Scraper Protocol exposes ``source``, ``name`` and ``scrape()``.

    Phase 10 renamed the method from ``fetch`` to ``scrape`` (more accurate
    for sources that don't strictly 'fetch' over HTTP -- e.g. Playwright)
    and added a ``name`` string attribute used in structured logs (e.g.
    ``log.warn("greenhouse.skip_company", ...)``) so we don't have to
    keep calling ``source.value.lower()`` everywhere.

    This test is structural: any object that satisfies the new shape
    must pass an ``isinstance(..., Scraper)`` check (runtime-checkable
    Protocols support this for attribute presence; method *signatures*
    aren't enforced at runtime but mypy --strict catches drift).
    """

    class _Concrete:
        source = JobSource.HN
        name = "hn"

        def scrape(self) -> Iterator[ScrapedJob]:
            return iter(())

    instance: Scraper = _Concrete()
    # ``Scraper`` is ``@runtime_checkable`` so this isinstance check
    # actually exercises structural attribute presence. If the Protocol
    # still declares ``fetch`` instead of ``scrape``, this fails.
    assert isinstance(instance, Scraper)
    assert instance.name == "hn"
    assert instance.source is JobSource.HN
    assert list(instance.scrape()) == []


def test_scraper_protocol_rejects_old_fetch_only_shape() -> None:
    """An object that only has ``fetch`` (the pre-Phase-10 shape) must
    NOT pass the structural check. Pins the rename: if someone reverts
    ``base.py`` to declare ``fetch`` again, both this test and the one
    above fail loudly.
    """

    class _OldShape:
        source = JobSource.HN

        def fetch(self) -> Iterator[ScrapedJob]:
            return iter(())

    assert not isinstance(_OldShape(), Scraper)


def test_scraped_job_requires_apply_url() -> None:
    with pytest.raises(ValueError):
        ScrapedJob(
            source=JobSource.HN,
            source_job_id="x",
            company_name="x",
            company_domain="x.test",
            company_size_bucket=CompanySizeBucket.UNKNOWN,
            title="x",
            location="x",
            apply_url="",
            description="x",
            posted_at=None,
        )
