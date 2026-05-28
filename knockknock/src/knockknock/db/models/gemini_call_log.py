"""``gemini_call_logs`` table — every Gemini API call with cost/latency."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
)
from sqlmodel import Field, SQLModel

from knockknock.db.enums import GeminiModel, GeminiPurpose
from knockknock.db.models._base import pg_enum


class GeminiCallLog(SQLModel, table=True):
    __tablename__ = "gemini_call_logs"
    __table_args__ = (Index("ix_gemini_called_at", "called_at"),)

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    job_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("job_applications.id", ondelete="SET NULL")),
    )
    model: GeminiModel = Field(
        sa_column=Column(pg_enum(GeminiModel, "gemini_model"), nullable=False)
    )
    purpose: GeminiPurpose = Field(
        sa_column=Column(pg_enum(GeminiPurpose, "gemini_purpose"), nullable=False)
    )
    prompt_tokens: int = Field(sa_column=Column(Integer, nullable=False))
    completion_tokens: int = Field(sa_column=Column(Integer, nullable=False))
    latency_ms: int = Field(sa_column=Column(Integer, nullable=False))
    success: bool = Field(sa_column=Column(Boolean, nullable=False))
    error_code: str | None = Field(default=None, sa_column=Column(Text))
    called_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )
