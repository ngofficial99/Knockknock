"""SQLModel ORM definitions mirroring the Phase-1 schema.

Errata E.2 applies: EmailDraftState (not DraftState); the active-draft
partial index uses GENERATED, not PENDING; server defaults updated
accordingly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ENUM as PgEnum  # noqa: N811
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from knockknock.db.enums import (
    CompanySizeBucket,
    EmailDraftState,
    GeminiModel,
    GeminiPurpose,
    JobSource,
    JobStatus,
    PhonebookSource,
    PipelineRunStatus,
    RejectionReason,
    TelegramDirection,
    TelegramKind,
)


def _pg_enum(enum_cls: type, name: str) -> PgEnum:
    """Build a Postgres ENUM column type.

    `create_type=False` tells SQLAlchemy not to emit CREATE TYPE on metadata
    create_all -- the Alembic migration is responsible for creating the
    enum types up front. `values_callable` ensures member.value (not name)
    is used as the Postgres label.
    """
    return PgEnum(
        enum_cls,
        name=name,
        create_type=False,
        values_callable=lambda e: [m.value for m in e],
    )


class Company(SQLModel, table=True):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("domain", name="companies_domain_unique"),)

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    name: str = Field(sa_column=Column(Text, nullable=False))
    domain: str = Field(sa_column=Column(Text, nullable=False))
    careers_url: str | None = Field(default=None, sa_column=Column(Text))
    size_bucket: CompanySizeBucket = Field(
        sa_column=Column(_pg_enum(CompanySizeBucket, "company_size_bucket"), nullable=False)
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


class CompanyBlacklist(SQLModel, table=True):
    __tablename__ = "companies_blacklist"

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    pattern: str = Field(sa_column=Column(Text, nullable=False, unique=True))
    reason: str | None = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )


class PhonebookEntry(SQLModel, table=True):
    __tablename__ = "phonebook"
    __table_args__ = (UniqueConstraint("company_id", name="phonebook_company_unique"),)

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    company_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    founder_email: str | None = Field(default=None, sa_column=Column(Text))
    founder_name: str | None = Field(default=None, sa_column=Column(Text))
    careers_email: str | None = Field(default=None, sa_column=Column(Text))
    source: PhonebookSource = Field(
        sa_column=Column(_pg_enum(PhonebookSource, "phonebook_source"), nullable=False)
    )
    confidence: int = Field(sa_column=Column(SmallInteger, nullable=False))
    last_verified_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
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
    source: JobSource = Field(sa_column=Column(_pg_enum(JobSource, "job_source"), nullable=False))
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
            _pg_enum(JobStatus, "job_status"),
            nullable=False,
            server_default="DISCOVERED",
        )
    )
    score: int | None = Field(default=None, sa_column=Column(SmallInteger))
    rejection_reason: RejectionReason | None = Field(
        default=None, sa_column=Column(_pg_enum(RejectionReason, "rejection_reason"))
    )
    rejection_detail: str | None = Field(default=None, sa_column=Column(Text))
    resume_variant_key: str | None = Field(default=None, sa_column=Column(Text))
    last_error: str | None = Field(default=None, sa_column=Column(Text))
    retry_count: int = Field(
        default=0, sa_column=Column(SmallInteger, nullable=False, server_default="0")
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


class JobApplicationEvent(SQLModel, table=True):
    __tablename__ = "job_application_events"
    __table_args__ = (Index("ix_events_job", "job_id"),)

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    job_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("job_applications.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    from_status: JobStatus | None = Field(
        default=None,
        sa_column=Column(_pg_enum(JobStatus, "job_status")),
    )
    to_status: JobStatus = Field(
        sa_column=Column(_pg_enum(JobStatus, "job_status"), nullable=False)
    )
    note: str | None = Field(default=None, sa_column=Column(Text))
    payload: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )


class EmailDraft(SQLModel, table=True):
    __tablename__ = "email_drafts"
    __table_args__ = (
        # At most one ACTIVE draft per job. "Active" = not yet decided/sent/superseded.
        # Per errata E.2 the initial state is GENERATED (was PENDING in the
        # earlier draft); DRAFT_CREATED is its Gmail-side sibling. Both are
        # active states, so the partial index covers both.
        Index(
            "ix_email_drafts_active_per_job",
            "job_id",
            unique=True,
            postgresql_where="state IN ('GENERATED', 'DRAFT_CREATED')",
        ),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    job_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("job_applications.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    gmail_draft_id: str | None = Field(default=None, sa_column=Column(Text))
    subject: str = Field(sa_column=Column(Text, nullable=False))
    body: str = Field(sa_column=Column(Text, nullable=False))
    to_recipients: list[str] = Field(sa_column=Column(JSONB, nullable=False))
    cc_recipients: list[str] | None = Field(default=None, sa_column=Column(JSONB))
    state: EmailDraftState = Field(
        sa_column=Column(
            _pg_enum(EmailDraftState, "email_draft_state"),
            nullable=False,
            server_default="GENERATED",
        )
    )
    regeneration_of: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("email_drafts.id", ondelete="SET NULL")),
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )
    decided_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    sent_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))


class GeminiCallLog(SQLModel, table=True):
    __tablename__ = "gemini_call_logs"
    __table_args__ = (Index("ix_gemini_called_at", "called_at"),)

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    job_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("job_applications.id", ondelete="SET NULL")),
    )
    model: GeminiModel = Field(
        sa_column=Column(_pg_enum(GeminiModel, "gemini_model"), nullable=False)
    )
    purpose: GeminiPurpose = Field(
        sa_column=Column(_pg_enum(GeminiPurpose, "gemini_purpose"), nullable=False)
    )
    prompt_tokens: int = Field(sa_column=Column(Integer, nullable=False))
    completion_tokens: int = Field(sa_column=Column(Integer, nullable=False))
    latency_ms: int = Field(sa_column=Column(Integer, nullable=False))
    success: bool = Field(sa_column=Column(Boolean, nullable=False))
    error_code: str | None = Field(default=None, sa_column=Column(Text))
    called_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )


class TelegramMessage(SQLModel, table=True):
    __tablename__ = "telegram_messages"
    __table_args__ = (Index("ix_tg_job", "job_id"),)

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    job_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("job_applications.id", ondelete="SET NULL")),
    )
    direction: TelegramDirection = Field(
        sa_column=Column(_pg_enum(TelegramDirection, "telegram_direction"), nullable=False)
    )
    kind: TelegramKind = Field(
        sa_column=Column(_pg_enum(TelegramKind, "telegram_kind"), nullable=False)
    )
    chat_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    message_id: int | None = Field(default=None, sa_column=Column(BigInteger))
    payload: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )


class PipelineRun(SQLModel, table=True):
    __tablename__ = "pipeline_runs"
    __table_args__ = (Index("ix_pipeline_started_at", "started_at"),)

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    started_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    status: PipelineRunStatus = Field(
        sa_column=Column(_pg_enum(PipelineRunStatus, "pipeline_run_status"), nullable=False)
    )
    discovered_count: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )
    pre_filtered_count: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )
    scored_count: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )
    drafted_count: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )
    error_count: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )
    summary: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))


class ScraperState(SQLModel, table=True):
    __tablename__ = "scraper_states"

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    source: JobSource = Field(
        sa_column=Column(_pg_enum(JobSource, "job_source"), nullable=False, unique=True)
    )
    cursor: str | None = Field(default=None, sa_column=Column(Text))
    last_run_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    consecutive_failure_count: int = Field(
        default=0, sa_column=Column(SmallInteger, nullable=False, server_default="0")
    )
    disabled: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, server_default="false")
    )
    updated_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        )
    )
