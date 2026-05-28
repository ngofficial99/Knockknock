"""``telegram_messages`` table — every outgoing/incoming Telegram message."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from knockknock.db.enums import TelegramDirection, TelegramKind
from knockknock.db.models._base import pg_enum


class TelegramMessage(SQLModel, table=True):
    __tablename__ = "telegram_messages"
    __table_args__ = (Index("ix_tg_job", "job_id"),)

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    job_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("job_applications.id", ondelete="SET NULL")),
    )
    direction: TelegramDirection = Field(
        sa_column=Column(pg_enum(TelegramDirection, "telegram_direction"), nullable=False)
    )
    kind: TelegramKind = Field(
        sa_column=Column(pg_enum(TelegramKind, "telegram_kind"), nullable=False)
    )
    chat_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    message_id: int | None = Field(default=None, sa_column=Column(BigInteger))
    payload: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )
