"""``scraper_states`` table — per-source cursor + auto-disable bookkeeping."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    SmallInteger,
    Text,
    func,
)
from sqlmodel import Field, SQLModel

from knockknock.db.enums import JobSource
from knockknock.db.models._base import pg_enum


class ScraperState(SQLModel, table=True):
    __tablename__ = "scraper_states"

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    source: JobSource = Field(
        sa_column=Column(pg_enum(JobSource, "job_source"), nullable=False, unique=True)
    )
    cursor: str | None = Field(default=None, sa_column=Column(Text))
    last_run_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    consecutive_failure_count: int = Field(
        default=0,
        sa_column=Column(SmallInteger, nullable=False, server_default="0"),
    )
    disabled: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="false"),
    )
    updated_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        )
    )
