from __future__ import annotations

from datetime import UTC, datetime

import pytest

from knockknock.db.enums import CompanySizeBucket, JobSource
from knockknock.scrapers.base import ScrapedJob


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
