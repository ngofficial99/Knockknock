"""``phonebook`` table — per-company contact (founder/careers email)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlmodel import Field, SQLModel

from knockknock.db.enums import PhonebookSource
from knockknock.db.models._base import pg_enum


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
        sa_column=Column(pg_enum(PhonebookSource, "phonebook_source"), nullable=False)
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
