"""``companies`` table — canonical employer record, unique by ``domain``."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Text, UniqueConstraint, func
from sqlmodel import Field, SQLModel

from knockknock.db.enums import CompanySizeBucket
from knockknock.db.models._base import pg_enum


class Company(SQLModel, table=True):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("domain", name="companies_domain_unique"),)

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    name: str = Field(sa_column=Column(Text, nullable=False))
    domain: str = Field(sa_column=Column(Text, nullable=False))
    careers_url: str | None = Field(default=None, sa_column=Column(Text))
    size_bucket: CompanySizeBucket = Field(
        sa_column=Column(pg_enum(CompanySizeBucket, "company_size_bucket"), nullable=False)
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
