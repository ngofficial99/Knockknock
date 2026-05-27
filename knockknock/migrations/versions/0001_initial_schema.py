"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-05-28 00:00:00.000000

Enum tuples below are the authoritative source-of-truth and must stay in lock-step
with src/knockknock/db/enums.py. They apply errata E.2 from
docs/superpowers/plans/2026-05-28-knockknock/00-index.md:

* CompanySizeBucket: SERIES_C + LATE_STAGE (not SERIES_C_PLUS)
* PhonebookSource: + SEED, CACHE
* JobStatus: no APPROVED
* RejectionReason: SCORE_LOW (not SCORE_BELOW_THRESHOLD), NO_EMAIL_FOUND
  (not NO_CONTACT_FOUND), + MAX_RETRIES
* PipelineStage: + NOTIFY
* DraftState -> EmailDraftState with members
  GENERATED/DRAFT_CREATED/SENT/FAILED/SUPERSEDED; type renamed to
  ``email_draft_state``; server_default = ``GENERATED``; partial unique
  active-draft index uses ``state IN ('GENERATED','DRAFT_CREATED')``.
* GeminiPurpose: DRAFT_EMAIL (not DRAFT) + REGENERATE
* TelegramDirection: OUTGOING/INCOMING
* TelegramKind: DRAFT_PREVIEW/APPROVAL/REJECTION/COMMAND/DIGEST/ALERT

ScraperState additionally carries ``consecutive_failure_count`` and
``disabled`` columns to support the Phase 11 auto-disable feature.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


JOB_SOURCE = ("HN", "WELLFOUND", "YC_WAAS", "GREENHOUSE", "LEVER", "ASHBY")
COMPANY_SIZE = ("SEED", "SERIES_A", "SERIES_B", "SERIES_C", "LATE_STAGE", "UNKNOWN")
PHONEBOOK_SOURCE = ("APOLLO", "HUNTER", "PATTERN_GUESS", "MANUAL", "SEED", "CACHE")
JOB_STATUS = (
    "DISCOVERED",
    "PRE_FILTERED",
    "PRE_FILTER_REJECTED",
    "SCORED",
    "SCORE_REJECTED",
    "ENRICHED",
    "ENRICH_FAILED",
    "TAILORED",
    "DRAFTED",
    "AWAITING_APPROVAL",
    "SENT",
    "USER_REJECTED",
    "ERROR",
)
REJECTION_REASON = (
    "BLACKLISTED",
    "LOCATION_MISMATCH",
    "ROLE_MISMATCH",
    "SENIORITY_MISMATCH",
    "SCORE_LOW",
    "NO_EMAIL_FOUND",
    "USER_REJECTED",
    "MAX_RETRIES",
    "OTHER",
)
PIPELINE_STAGE = (
    "DISCOVER",
    "PRE_FILTER",
    "SCORE",
    "ENRICH",
    "TAILOR",
    "DRAFT",
    "NOTIFY",
)
PIPELINE_RUN_STATUS = ("RUNNING", "SUCCESS", "PARTIAL", "FAILED")
EMAIL_DRAFT_STATE = ("GENERATED", "DRAFT_CREATED", "SENT", "FAILED", "SUPERSEDED")
GEMINI_MODEL = ("gemini-2.5-flash", "gemini-2.5-pro")
GEMINI_PURPOSE = ("SCORE", "DRAFT_EMAIL", "REGENERATE")
TELEGRAM_DIRECTION = ("OUTGOING", "INCOMING")
TELEGRAM_KIND = (
    "DRAFT_PREVIEW",
    "APPROVAL",
    "REJECTION",
    "COMMAND",
    "DIGEST",
    "ALERT",
)


def _create_enum(name: str, values: tuple[str, ...]) -> postgresql.ENUM:
    enum = postgresql.ENUM(*values, name=name)
    enum.create(op.get_bind(), checkfirst=False)
    return enum


def upgrade() -> None:
    _create_enum("job_source", JOB_SOURCE)
    _create_enum("company_size_bucket", COMPANY_SIZE)
    _create_enum("phonebook_source", PHONEBOOK_SOURCE)
    _create_enum("job_status", JOB_STATUS)
    _create_enum("rejection_reason", REJECTION_REASON)
    _create_enum("pipeline_stage", PIPELINE_STAGE)
    _create_enum("pipeline_run_status", PIPELINE_RUN_STATUS)
    _create_enum("email_draft_state", EMAIL_DRAFT_STATE)
    _create_enum("gemini_model", GEMINI_MODEL)
    _create_enum("gemini_purpose", GEMINI_PURPOSE)
    _create_enum("telegram_direction", TELEGRAM_DIRECTION)
    _create_enum("telegram_kind", TELEGRAM_KIND)

    op.create_table(
        "companies",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("domain", sa.Text, nullable=False),
        sa.Column("careers_url", sa.Text),
        sa.Column(
            "size_bucket",
            postgresql.ENUM(name="company_size_bucket", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("domain", name="companies_domain_unique"),
    )

    op.create_table(
        "companies_blacklist",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("pattern", sa.Text, nullable=False, unique=True),
        sa.Column("reason", sa.Text),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "phonebook",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "company_id",
            sa.BigInteger,
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("founder_email", sa.Text),
        sa.Column("founder_name", sa.Text),
        sa.Column("careers_email", sa.Text),
        sa.Column(
            "source",
            postgresql.ENUM(name="phonebook_source", create_type=False),
            nullable=False,
        ),
        sa.Column("confidence", sa.SmallInteger, nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("company_id", name="phonebook_company_unique"),
    )

    op.create_table(
        "job_applications",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "company_id",
            sa.BigInteger,
            sa.ForeignKey("companies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "source",
            postgresql.ENUM(name="job_source", create_type=False),
            nullable=False,
        ),
        sa.Column("source_job_id", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("location", sa.Text, nullable=False),
        sa.Column("apply_url", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="job_status", create_type=False),
            nullable=False,
            server_default="DISCOVERED",
        ),
        sa.Column("score", sa.SmallInteger),
        sa.Column(
            "rejection_reason",
            postgresql.ENUM(name="rejection_reason", create_type=False),
        ),
        sa.Column("rejection_detail", sa.Text),
        sa.Column("resume_variant_key", sa.Text),
        sa.Column("last_error", sa.Text),
        sa.Column("retry_count", sa.SmallInteger, server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("source", "source_job_id", name="jobs_source_unique"),
    )
    op.create_index("ix_jobs_status", "job_applications", ["status"])
    op.create_index("ix_jobs_company", "job_applications", ["company_id"])

    op.create_table(
        "job_application_events",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "job_id",
            sa.BigInteger,
            sa.ForeignKey("job_applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "from_status",
            postgresql.ENUM(name="job_status", create_type=False),
        ),
        sa.Column(
            "to_status",
            postgresql.ENUM(name="job_status", create_type=False),
            nullable=False,
        ),
        sa.Column("note", sa.Text),
        sa.Column("payload", postgresql.JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_events_job", "job_application_events", ["job_id"])

    op.create_table(
        "email_drafts",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "job_id",
            sa.BigInteger,
            sa.ForeignKey("job_applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("gmail_draft_id", sa.Text),
        sa.Column("subject", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("to_recipients", postgresql.JSONB, nullable=False),
        sa.Column("cc_recipients", postgresql.JSONB),
        sa.Column(
            "state",
            postgresql.ENUM(name="email_draft_state", create_type=False),
            nullable=False,
            server_default="GENERATED",
        ),
        sa.Column(
            "regeneration_of",
            sa.BigInteger,
            sa.ForeignKey("email_drafts.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_email_drafts_active_per_job",
        "email_drafts",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('GENERATED', 'DRAFT_CREATED')"),
    )

    op.create_table(
        "gemini_call_logs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "job_id",
            sa.BigInteger,
            sa.ForeignKey("job_applications.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "model",
            postgresql.ENUM(name="gemini_model", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "purpose",
            postgresql.ENUM(name="gemini_purpose", create_type=False),
            nullable=False,
        ),
        sa.Column("prompt_tokens", sa.Integer, nullable=False),
        sa.Column("completion_tokens", sa.Integer, nullable=False),
        sa.Column("latency_ms", sa.Integer, nullable=False),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("error_code", sa.Text),
        sa.Column(
            "called_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_gemini_called_at", "gemini_call_logs", ["called_at"])

    op.create_table(
        "telegram_messages",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "job_id",
            sa.BigInteger,
            sa.ForeignKey("job_applications.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "direction",
            postgresql.ENUM(name="telegram_direction", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "kind",
            postgresql.ENUM(name="telegram_kind", create_type=False),
            nullable=False,
        ),
        sa.Column("chat_id", sa.BigInteger, nullable=False),
        sa.Column("message_id", sa.BigInteger),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_tg_job", "telegram_messages", ["job_id"])

    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column(
            "status",
            postgresql.ENUM(name="pipeline_run_status", create_type=False),
            nullable=False,
        ),
        sa.Column("discovered_count", sa.Integer, server_default="0", nullable=False),
        sa.Column("pre_filtered_count", sa.Integer, server_default="0", nullable=False),
        sa.Column("scored_count", sa.Integer, server_default="0", nullable=False),
        sa.Column("drafted_count", sa.Integer, server_default="0", nullable=False),
        sa.Column("error_count", sa.Integer, server_default="0", nullable=False),
        sa.Column("summary", postgresql.JSONB),
    )
    op.create_index("ix_pipeline_started_at", "pipeline_runs", ["started_at"])

    op.create_table(
        "scraper_states",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "source",
            postgresql.ENUM(name="job_source", create_type=False),
            nullable=False,
            unique=True,
        ),
        sa.Column("cursor", sa.Text),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column(
            "consecutive_failure_count",
            sa.SmallInteger,
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "disabled",
            sa.Boolean,
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("scraper_states")
    op.drop_index("ix_pipeline_started_at", table_name="pipeline_runs")
    op.drop_table("pipeline_runs")
    op.drop_index("ix_tg_job", table_name="telegram_messages")
    op.drop_table("telegram_messages")
    op.drop_index("ix_gemini_called_at", table_name="gemini_call_logs")
    op.drop_table("gemini_call_logs")
    op.drop_index("ix_email_drafts_active_per_job", table_name="email_drafts")
    op.drop_table("email_drafts")
    op.drop_index("ix_events_job", table_name="job_application_events")
    op.drop_table("job_application_events")
    op.drop_index("ix_jobs_company", table_name="job_applications")
    op.drop_index("ix_jobs_status", table_name="job_applications")
    op.drop_table("job_applications")
    op.drop_table("phonebook")
    op.drop_table("companies_blacklist")
    op.drop_table("companies")

    for enum_name in [
        "telegram_kind",
        "telegram_direction",
        "gemini_purpose",
        "gemini_model",
        "email_draft_state",
        "pipeline_run_status",
        "pipeline_stage",
        "rejection_reason",
        "job_status",
        "phonebook_source",
        "company_size_bucket",
        "job_source",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
