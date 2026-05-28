"""``job_applications`` table — one row per scraped job, unique on (source, source_job_id)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlmodel import Field, SQLModel

from knockknock.db.enums import JobSource, JobStatus, RejectionReason
from knockknock.db.models._base import pg_enum


class JobApplication(SQLModel, table=True):
    __tablename__ = "job_applications"
    __table_args__ = (
        UniqueConstraint("source", "source_job_id", name="jobs_source_unique"),
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_company", "company_id"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    company_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("companies.id", ondelete="RESTRICT"),
            nullable=False,
        )
    )
    source: JobSource = Field(sa_column=Column(pg_enum(JobSource, "job_source"), nullable=False))
    source_job_id: str = Field(sa_column=Column(Text, nullable=False))
    title: str = Field(sa_column=Column(Text, nullable=False))
    location: str = Field(sa_column=Column(Text, nullable=False))
    apply_url: str = Field(sa_column=Column(Text, nullable=False))
    description: str = Field(sa_column=Column(Text, nullable=False))
    posted_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    discovered_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )
    status: JobStatus = Field(
        sa_column=Column(
            pg_enum(JobStatus, "job_status"),
            nullable=False,
            server_default="DISCOVERED",
        )
    )
    score: int | None = Field(default=None, sa_column=Column(SmallInteger))
    # Salary capture (best-effort). All nullable; populated when a scraper
    # can extract structured comp from the source. Soft signal for scoring,
    # NOT a hard pre-filter gate. See ``scrapers/_salary.py`` and migration
    # ``0002_add_salary_fields.py``.
    salary_min: int | None = Field(default=None, sa_column=Column(BigInteger))
    salary_max: int | None = Field(default=None, sa_column=Column(BigInteger))
    salary_currency: str | None = Field(default=None, sa_column=Column(Text))
    salary_period: str | None = Field(default=None, sa_column=Column(Text))
    salary_raw: str | None = Field(default=None, sa_column=Column(Text))
    rejection_reason: RejectionReason | None = Field(
        default=None,
        sa_column=Column(pg_enum(RejectionReason, "rejection_reason")),
    )
    rejection_detail: str | None = Field(default=None, sa_column=Column(Text))
    resume_variant_key: str | None = Field(default=None, sa_column=Column(Text))
    last_error: str | None = Field(default=None, sa_column=Column(Text))
    retry_count: int = Field(
        default=0,
        sa_column=Column(SmallInteger, nullable=False, server_default="0"),
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )
    updated_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        )
    )
