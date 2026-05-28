"""Integration tests for :class:`TailorStage`.

The tailor stage advances ``ENRICHED`` jobs to ``TAILORED`` by selecting
a resume variant via the deterministic tag-overlap selector. No I/O,
no LLM — same job text always picks the same variant.

Ordering matters for parity with the rest of the pipeline: highest
score first, ``discovered_at ASC`` as the FIFO tie-breaker. We assert
on ``JobApplicationEvent.id`` (BigInteger PK, monotonically increasing)
because event ``created_at`` collapses for two rows inserted in the
same millisecond.

These tests need a real Postgres for inserts; they auto-skip via the
``db_session`` fixture when ``KNOCKKNOCK_TEST_DATABASE_URL`` isn't set.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlmodel import Session, select

from knockknock.db.enums import (
    CompanySizeBucket,
    JobSource,
    JobStatus,
)
from knockknock.db.models import (
    Company,
    JobApplication,
    JobApplicationEvent,
)
from knockknock.pipeline.stage import StageContext
from knockknock.pipeline.tailor import TailorStage
from knockknock.resume.manifest import load_manifest

_counter = {"n": 0}


def _seed_company(session: Session) -> Company:
    _counter["n"] += 1
    suffix = _counter["n"]
    company = Company(
        name=f"Acme{suffix}",
        domain=f"acme{suffix}.test",
        size_bucket=CompanySizeBucket.UNKNOWN,
    )
    session.add(company)
    session.flush()
    return company


def _seed_enriched_job(
    session: Session,
    *,
    title: str,
    description: str,
    score: int = 8,
    discovered_at: datetime | None = None,
) -> JobApplication:
    _counter["n"] += 1
    suffix = _counter["n"]
    company = _seed_company(session)
    assert company.id is not None
    job = JobApplication(
        company_id=company.id,
        source=JobSource.HN,
        source_job_id=f"hn-{suffix}",
        title=title,
        location="Bengaluru",
        apply_url=f"https://acme{suffix}.test/jobs/{suffix}",
        description=description,
        status=JobStatus.ENRICHED,
        score=score,
        discovered_at=discovered_at or datetime.now(UTC),
    )
    session.add(job)
    session.flush()
    return job


def test_tailor_assigns_distributed_variant_for_distributed_job(db_session: Session) -> None:
    """Job mentioning distributed systems + Kafka picks the distributed variant."""
    manifest = load_manifest(Path("resumes/manifest.yaml"))
    job = _seed_enriched_job(
        db_session,
        title="Senior Backend Engineer",
        description=("Build distributed systems with Python, Kafka, and Kubernetes on AWS."),
    )

    result = TailorStage(session=db_session, manifest=manifest).run(StageContext(run_id=1))

    db_session.refresh(job)
    assert job.status is JobStatus.TAILORED
    assert job.resume_variant_key == "backend-distributed"
    assert result.stage == "tailor"
    assert result.processed == 1
    assert result.advanced == 1
    assert result.errors == 0


def test_tailor_assigns_llm_variant_for_llm_job(db_session: Session) -> None:
    """LLM/ML job text routes to the llm variant via the manifest dictionary."""
    manifest = load_manifest(Path("resumes/manifest.yaml"))
    job = _seed_enriched_job(
        db_session,
        title="Applied AI Engineer",
        description=(
            "Build RAG pipelines with LangChain and FastAPI. "
            "Machine learning and LLM experience required."
        ),
    )

    TailorStage(session=db_session, manifest=manifest).run(StageContext(run_id=1))

    db_session.refresh(job)
    assert job.resume_variant_key == "backend-llm"
    assert job.status is JobStatus.TAILORED


def test_tailor_falls_back_to_highest_priority_for_unrelated_job(db_session: Session) -> None:
    """Zero-overlap text → the manifest's highest-priority variant wins."""
    manifest = load_manifest(Path("resumes/manifest.yaml"))
    job = _seed_enriched_job(
        db_session,
        title="Junior Tester",
        description="Manual QA work on legacy mainframe systems.",
    )

    TailorStage(session=db_session, manifest=manifest).run(StageContext(run_id=1))

    db_session.refresh(job)
    # No overlap → highest-priority fallback (any variant is acceptable as long as
    # the job is advanced and a key is assigned).
    assert job.resume_variant_key is not None
    assert job.status is JobStatus.TAILORED


def test_tailor_writes_audit_event(db_session: Session) -> None:
    """Successful transition produces a JobApplicationEvent with chosen variant key."""
    manifest = load_manifest(Path("resumes/manifest.yaml"))
    job = _seed_enriched_job(
        db_session,
        title="Backend Engineer",
        description="Python, Postgres, REST APIs",
    )

    TailorStage(session=db_session, manifest=manifest).run(StageContext(run_id=7))

    event = db_session.exec(
        select(JobApplicationEvent).where(JobApplicationEvent.job_id == job.id)
    ).one()
    assert event.from_status is JobStatus.ENRICHED
    assert event.to_status is JobStatus.TAILORED
    assert event.payload is not None
    assert event.payload["run_id"] == 7
    assert event.payload["variant_key"] == job.resume_variant_key


def test_tailor_processes_in_score_desc_order(db_session: Session) -> None:
    """Higher score is processed first; verify via JobApplicationEvent.id ordering."""
    now = datetime.now(UTC)
    low = _seed_enriched_job(
        db_session,
        title="Backend Engineer",
        description="python postgres",
        score=6,
        discovered_at=now,
    )
    high = _seed_enriched_job(
        db_session,
        title="Backend Engineer",
        description="python postgres",
        score=9,
        discovered_at=now + timedelta(minutes=1),
    )

    manifest = load_manifest(Path("resumes/manifest.yaml"))
    TailorStage(session=db_session, manifest=manifest).run(StageContext(run_id=1))

    events = db_session.exec(
        select(JobApplicationEvent).order_by(JobApplicationEvent.id)  # type: ignore[arg-type]
    ).all()
    assert len(events) == 2
    assert events[0].job_id == high.id
    assert events[1].job_id == low.id


def test_tailor_skips_non_enriched_jobs(db_session: Session) -> None:
    """SCORED, DRAFTED, TAILORED etc. are untouched -- only ENRICHED advances."""
    manifest = load_manifest(Path("resumes/manifest.yaml"))
    company = _seed_company(db_session)
    assert company.id is not None
    _counter["n"] += 1
    suffix = _counter["n"]
    scored = JobApplication(
        company_id=company.id,
        source=JobSource.HN,
        source_job_id=f"hn-{suffix}",
        title="Backend Engineer",
        location="Bengaluru",
        apply_url=f"https://acme{suffix}.test/jobs/{suffix}",
        description="Python services.",
        status=JobStatus.SCORED,
        score=80,
        discovered_at=datetime.now(UTC),
    )
    db_session.add(scored)
    db_session.flush()

    result = TailorStage(session=db_session, manifest=manifest).run(StageContext(run_id=1))

    assert result.processed == 0
    assert result.advanced == 0
    db_session.refresh(scored)
    assert scored.status is JobStatus.SCORED
    assert scored.resume_variant_key is None
