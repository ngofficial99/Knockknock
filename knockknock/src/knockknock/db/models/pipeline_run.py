"""``pipeline_runs`` table — one row per ``knockknock pipeline run`` invocation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from knockknock.db.enums import PipelineRunStatus
from knockknock.db.models._base import pg_enum


class PipelineRun(SQLModel, table=True):
    __tablename__ = "pipeline_runs"
    __table_args__ = (Index("ix_pipeline_started_at", "started_at"),)

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    started_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    status: PipelineRunStatus = Field(
        sa_column=Column(pg_enum(PipelineRunStatus, "pipeline_run_status"), nullable=False)
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
