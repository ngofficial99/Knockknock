from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from sqlmodel import Session, select

from knockknock.db.enums import CompanySizeBucket, JobSource, JobStatus
from knockknock.db.models import Company, JobApplication
from knockknock.pipeline.discover import DiscoverStage
from knockknock.pipeline.stage import StageContext
from knockknock.scrapers.base import ScrapedJob


class _StaticScraper:
    source = JobSource.HN

    def __init__(self, jobs: list[ScrapedJob]) -> None:
        self._jobs = jobs

    def fetch(self) -> Iterator[ScrapedJob]:
        yield from self._jobs


def _make_job(source_id: str) -> ScrapedJob:
    return ScrapedJob(
        source=JobSource.HN,
        source_job_id=source_id,
        company_name="Acme",
        company_domain="acme.test",
        company_size_bucket=CompanySizeBucket.UNKNOWN,
        title="Backend Engineer",
        location="Bengaluru",
        apply_url=f"https://acme.test/jobs/{source_id}",
        description="Build things",
        posted_at=datetime(2026, 5, 28, tzinfo=UTC),
    )


def test_discover_upserts_company_once(db_session: Session) -> None:
    scrapers = [_StaticScraper([_make_job("hn-a"), _make_job("hn-b")])]
    stage = DiscoverStage(session=db_session, scrapers=scrapers, hourly_cap=50)
    result = stage.run(StageContext(run_id=1))
    assert result.processed == 2
    companies = db_session.exec(select(Company).where(Company.domain == "acme.test")).all()
    assert len(companies) == 1
    jobs = db_session.exec(select(JobApplication)).all()
    assert {j.source_job_id for j in jobs} == {"hn-a", "hn-b"}
    assert {j.status for j in jobs} == {JobStatus.DISCOVERED}


def test_discover_is_idempotent(db_session: Session) -> None:
    scrapers = [_StaticScraper([_make_job("hn-x")])]
    DiscoverStage(session=db_session, scrapers=scrapers, hourly_cap=50).run(StageContext(run_id=1))
    # Second run inserts no duplicates
    DiscoverStage(session=db_session, scrapers=scrapers, hourly_cap=50).run(StageContext(run_id=2))
    jobs = db_session.exec(
        select(JobApplication).where(JobApplication.source_job_id == "hn-x")
    ).all()
    assert len(jobs) == 1


def test_discover_respects_hourly_cap(db_session: Session) -> None:
    jobs_in = [_make_job(f"hn-{i}") for i in range(10)]
    scrapers = [_StaticScraper(jobs_in)]
    stage = DiscoverStage(session=db_session, scrapers=scrapers, hourly_cap=3)
    result = stage.run(StageContext(run_id=1))
    assert result.processed == 3
