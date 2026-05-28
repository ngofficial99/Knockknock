"""``job_application_events`` table — audit log of state transitions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from knockknock.db.enums import JobStatus
from knockknock.db.models._base import pg_enum


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
        sa_column=Column(pg_enum(JobStatus, "job_status")),
    )
    to_status: JobStatus = Field(sa_column=Column(pg_enum(JobStatus, "job_status"), nullable=False))
    note: str | None = Field(default=None, sa_column=Column(Text))
    payload: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )
