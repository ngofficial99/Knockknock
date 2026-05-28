"""``companies_blacklist`` table — patterns of companies we never apply to."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Text, func
from sqlmodel import Field, SQLModel


class CompanyBlacklist(SQLModel, table=True):
    __tablename__ = "companies_blacklist"

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    pattern: str = Field(sa_column=Column(Text, nullable=False, unique=True))
    reason: str | None = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )
