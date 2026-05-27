"""Integration tests against a real Postgres database.

These tests require ``KNOCKKNOCK_TEST_DATABASE_URL`` to point at a database
that has the 0001_initial_schema migration applied (see
docs/superpowers/plans/2026-05-28-knockknock/phase-01-db-schema.md Task 1.5).
The conftest's ``db_session`` fixture wraps each test in a SAVEPOINT-style
transaction that rolls back at teardown so tests don't pollute the DB.
"""

from __future__ import annotations

from sqlmodel import Session, select

from knockknock.db.enums import (
    CompanySizeBucket,
    EmailDraftState,
    JobSource,
    JobStatus,
)
from knockknock.db.models import Company, EmailDraft, JobApplication


def test_company_roundtrip(db_session: Session) -> None:
    company = Company(
        name="Acme Labs",
        domain="acme.test",
        size_bucket=CompanySizeBucket.SERIES_A,
    )
    db_session.add(company)
    db_session.flush()

    fetched = db_session.exec(select(Company).where(Company.domain == "acme.test")).one()
    assert fetched.name == "Acme Labs"
    assert fetched.size_bucket == CompanySizeBucket.SERIES_A


def test_job_application_requires_company(db_session: Session) -> None:
    company = Company(name="Beta", domain="beta.test", size_bucket=CompanySizeBucket.SEED)
    db_session.add(company)
    db_session.flush()

    job = JobApplication(
        company_id=company.id,
        source=JobSource.HN,
        source_job_id="hn-1",
        title="Backend Engineer",
        location="Bengaluru",
        apply_url="https://example.test/apply",
        description="Build things",
        status=JobStatus.DISCOVERED,
    )
    db_session.add(job)
    db_session.flush()

    fetched = db_session.exec(
        select(JobApplication).where(JobApplication.source_job_id == "hn-1")
    ).one()
    assert fetched.title == "Backend Engineer"
    assert fetched.status == JobStatus.DISCOVERED


def test_email_draft_state_default_is_generated(db_session: Session) -> None:
    """Errata E.2: the server_default for email_drafts.state is GENERATED."""
    company = Company(name="Gamma", domain="gamma.test", size_bucket=CompanySizeBucket.SEED)
    db_session.add(company)
    db_session.flush()

    job = JobApplication(
        company_id=company.id,
        source=JobSource.HN,
        source_job_id="hn-default",
        title="SDE",
        location="Bengaluru",
        apply_url="https://example.test/apply",
        description="...",
    )
    db_session.add(job)
    db_session.flush()

    # Construct without specifying state -> server_default should apply.
    draft = EmailDraft(
        job_id=job.id,
        subject="Hello",
        body="Body",
        to_recipients=["a@b.test"],
    )
    db_session.add(draft)
    db_session.flush()
    db_session.refresh(draft)
    assert draft.state == EmailDraftState.GENERATED
