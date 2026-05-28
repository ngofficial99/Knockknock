"""Discover stage: pull jobs from each enabled scraper and upsert into the DB."""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, select

from knockknock.db.enums import JobStatus
from knockknock.db.models import Company, JobApplication
from knockknock.pipeline.stage import StageContext, StageResult
from knockknock.scrapers.base import ScrapedJob, Scraper

log = structlog.get_logger(__name__)


@dataclass
class DiscoverStage:
    """Pulls jobs from scrapers and upserts Company + JobApplication idempotently.

    Transaction boundary is owned by the caller (typically ``session_scope``).
    The stage only ``flush``es so that inserts are visible to subsequent
    queries inside the same transaction; the caller decides when to commit.
    """

    session: Session
    scrapers: list[Scraper]
    hourly_cap: int
    name: str = field(default="discover", init=False)

    def run(self, ctx: StageContext) -> StageResult:
        processed = 0
        rejected = 0
        errors = 0
        for scraper in self.scrapers:
            try:
                for job in scraper.fetch():
                    if processed >= self.hourly_cap:
                        log.info("discover.cap_reached", cap=self.hourly_cap)
                        break
                    if self._upsert(job):
                        processed += 1
                    else:
                        rejected += 1
                if processed >= self.hourly_cap:
                    break
            except Exception as exc:
                log.exception(
                    "discover.scraper_failed",
                    source=scraper.source,
                    run_id=ctx.run_id,
                    error=str(exc),
                )
                errors += 1
        self.session.flush()
        return StageResult(
            stage=self.name,
            processed=processed,
            advanced=processed,
            rejected=rejected,
            errors=errors,
        )

    def _upsert(self, job: ScrapedJob) -> bool:
        """Return True if a new JobApplication row was inserted."""
        company_id = self._get_or_create_company(job)
        table = JobApplication.__table__  # type: ignore[attr-defined]
        stmt = (
            insert(table)
            .values(
                company_id=company_id,
                source=job.source,
                source_job_id=job.source_job_id,
                title=job.title,
                location=job.location,
                apply_url=job.apply_url,
                description=job.description,
                posted_at=job.posted_at,
                status=JobStatus.DISCOVERED,
            )
            .on_conflict_do_nothing(constraint="jobs_source_unique")
            .returning(table.c.id)
        )
        result = self.session.execute(stmt).first()
        return result is not None

    def _get_or_create_company(self, job: ScrapedJob) -> int:
        existing = self.session.exec(
            select(Company).where(Company.domain == job.company_domain)
        ).first()
        if existing is not None and existing.id is not None:
            return existing.id
        row = Company(
            name=job.company_name,
            domain=job.company_domain,
            size_bucket=job.company_size_bucket,
        )
        self.session.add(row)
        self.session.flush()
        assert row.id is not None
        return row.id
