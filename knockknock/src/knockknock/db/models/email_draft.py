"""``email_drafts`` table — generated email per job.

Errata E.2: initial state is ``GENERATED`` (was ``PENDING``); the partial
unique index covers both ``GENERATED`` and ``DRAFT_CREATED`` so at most one
active draft per job exists at any time.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from knockknock.db.enums import EmailDraftState
from knockknock.db.models._base import pg_enum


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
            pg_enum(EmailDraftState, "email_draft_state"),
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
