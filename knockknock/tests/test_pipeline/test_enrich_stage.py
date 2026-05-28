"""Integration tests for :class:`EnrichStage`.

The enrich stage advances ``SCORED`` jobs to ``ENRICHED`` by resolving a
:class:`PhonebookEntry` for the job's company via :class:`PhonebookLookup`.
Jobs whose company has no usable domain (no fallback possible) fail
gracefully to ``ENRICH_FAILED`` with ``NO_EMAIL_FOUND``.

Order matters: highest-score jobs are enriched first so that API credits
(Apollo/Hunter quota) are spent on the most promising leads if a run
exits early. Within the same score, ties break on ``discovered_at ASC``
(FIFO).

These tests need a real Postgres for inserts; they auto-skip via the
``db_session`` fixture when ``KNOCKKNOCK_TEST_DATABASE_URL`` isn't set.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from knockknock.db.enums import (
    CompanySizeBucket,
    JobSource,
    JobStatus,
    PhonebookSource,
    RejectionReason,
)
from knockknock.db.models import (
    Company,
    JobApplication,
    JobApplicationEvent,
    PhonebookEntry,
)
from knockknock.phonebook.lookup import PhonebookLookup
from knockknock.phonebook.models import FounderHit
from knockknock.pipeline.enrich import EnrichStage
from knockknock.pipeline.stage import StageContext


@dataclass
class _StubProvider:
    """Returns the same canned founder hits for every domain."""

    hits: list[FounderHit]

    def find_founders(self, *, domain: str) -> list[FounderHit]:
        return list(self.hits)


_counter = {"n": 0}


def _seed_company(session: Session, *, domain: str | None = None) -> Company:
    _counter["n"] += 1
    suffix = _counter["n"]
    company = Company(
        name=f"Acme{suffix}",
        domain=domain if domain is not None else f"acme{suffix}.test",
        size_bucket=CompanySizeBucket.UNKNOWN,
    )
    session.add(company)
    session.flush()
    return company


def _seed_job(
    session: Session,
    *,
    company: Company,
    score: int,
    discovered_at: datetime | None = None,
) -> JobApplication:
    _counter["n"] += 1
    suffix = _counter["n"]
    assert company.id is not None
    job = JobApplication(
        company_id=company.id,
        source=JobSource.HN,
        source_job_id=f"hn-{suffix}",
        title="Senior Backend Engineer",
        location="Bengaluru",
        apply_url=f"https://acme{suffix}.test/jobs/{suffix}",
        description="Python services.",
        status=JobStatus.SCORED,
        score=score,
        discovered_at=discovered_at or datetime.now(UTC),
    )
    session.add(job)
    session.flush()
    return job


def _chain(apollo_hits: list[FounderHit] | None = None) -> PhonebookLookup:
    apollo = _StubProvider(hits=apollo_hits or [])
    hunter = _StubProvider(hits=[])
    return PhonebookLookup(apollo=apollo, hunter=hunter)


def test_enrich_advances_scored_jobs(db_session: Session) -> None:
    """SCORED job with a domain gets ENRICHED and a phonebook row written."""
    company = _seed_company(db_session, domain="acme.io")
    job = _seed_job(db_session, company=company, score=85)
    chain = _chain([FounderHit("Aarav Singh", "aarav@acme.io", PhonebookSource.APOLLO)])

    result = EnrichStage(session=db_session, chain=chain).run(StageContext(run_id=1))

    assert result.stage == "enrich"
    assert result.processed == 1
    assert result.advanced == 1
    assert result.rejected == 0
    assert result.errors == 0

    db_session.refresh(job)
    assert job.status is JobStatus.ENRICHED
    entry = db_session.exec(
        select(PhonebookEntry).where(PhonebookEntry.company_id == company.id)
    ).one()
    assert entry.founder_email == "aarav@acme.io"
    # hr@ change: comma-list of careers + hr aliases.
    assert entry.careers_email == "careers@acme.io, hr@acme.io"
    assert entry.source is PhonebookSource.APOLLO


def test_enrich_processes_jobs_in_score_desc_order(db_session: Session) -> None:
    """Higher score processed first; verify via JobApplicationEvent.created_at."""
    company_lo = _seed_company(db_session, domain="lo.io")
    company_hi = _seed_company(db_session, domain="hi.io")
    now = datetime.now(UTC)
    low = _seed_job(db_session, company=company_lo, score=50, discovered_at=now)
    high = _seed_job(
        db_session, company=company_hi, score=90, discovered_at=now + timedelta(minutes=1)
    )
    chain = _chain([FounderHit("Aarav Singh", "aarav@hi.io", PhonebookSource.APOLLO)])

    EnrichStage(session=db_session, chain=chain).run(StageContext(run_id=1))

    # Event for the high-score job must exist with an id smaller than the low-score event
    # (events are inserted in processing order; BigInteger PK is monotonic).
    hi_event = db_session.exec(
        select(JobApplicationEvent).where(JobApplicationEvent.job_id == high.id)
    ).one()
    lo_event = db_session.exec(
        select(JobApplicationEvent).where(JobApplicationEvent.job_id == low.id)
    ).one()
    assert hi_event.id is not None and lo_event.id is not None
    assert hi_event.id < lo_event.id


def test_enrich_handles_no_domain_company(db_session: Session) -> None:
    """Company with empty domain → ENRICH_FAILED + NO_EMAIL_FOUND."""
    company = _seed_company(db_session, domain="")
    job = _seed_job(db_session, company=company, score=85)
    chain = _chain([FounderHit("Aarav Singh", "aarav@acme.io", PhonebookSource.APOLLO)])

    result = EnrichStage(session=db_session, chain=chain).run(StageContext(run_id=1))

    assert result.processed == 1
    assert result.advanced == 0
    assert result.rejected == 1
    assert result.errors == 0

    db_session.refresh(job)
    assert job.status is JobStatus.ENRICH_FAILED
    assert job.rejection_reason is RejectionReason.NO_EMAIL_FOUND
    assert job.rejection_detail and "domain" in job.rejection_detail.lower()
    # No phonebook row written when we can't even derive a fallback.
    assert (
        db_session.exec(
            select(PhonebookEntry).where(PhonebookEntry.company_id == company.id)
        ).first()
        is None
    )


def test_enrich_records_audit_event(db_session: Session) -> None:
    """Every status transition writes a JobApplicationEvent with run_id."""
    company = _seed_company(db_session, domain="acme.io")
    job = _seed_job(db_session, company=company, score=85)
    chain = _chain([FounderHit("Aarav Singh", "aarav@acme.io", PhonebookSource.APOLLO)])

    EnrichStage(session=db_session, chain=chain).run(StageContext(run_id=42))

    events = db_session.exec(
        select(JobApplicationEvent).where(JobApplicationEvent.job_id == job.id)
    ).all()
    assert len(events) == 1
    event = events[0]
    assert event.from_status is JobStatus.SCORED
    assert event.to_status is JobStatus.ENRICHED
    assert event.payload is not None
    assert event.payload.get("run_id") == 42
    assert event.payload.get("source") == PhonebookSource.APOLLO.value


def test_enrich_skips_non_scored_jobs(db_session: Session) -> None:
    """Jobs not in SCORED status are ignored."""
    company = _seed_company(db_session, domain="acme.io")
    job = _seed_job(db_session, company=company, score=85)
    job.status = JobStatus.PRE_FILTERED  # not eligible
    db_session.add(job)
    db_session.flush()
    chain = _chain([FounderHit("Aarav Singh", "aarav@acme.io", PhonebookSource.APOLLO)])

    result = EnrichStage(session=db_session, chain=chain).run(StageContext(run_id=1))

    assert result.processed == 0
    db_session.refresh(job)
    assert job.status is JobStatus.PRE_FILTERED
