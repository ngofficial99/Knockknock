← [Index](00-index.md) · [Prev: phase-00-bootstrap.md](phase-00-bootstrap.md) · [Next: phase-02-config.md](phase-02-config.md)

## Phase 1: Database Schema, Alembic, SQLModel Models

**Outcome:** All 10 tables and 12 enums from the spec exist in Neon via Alembic migration `0001_initial_schema`. SQLModel classes mirror the schema and are importable. A round-trip integration test inserts and reads a `Company` row against a real Postgres (uses a transactional rollback per test).

### Task 1.1: Postgres enums module

**Files:**
- Create: `src/knockknock/db/__init__.py`
- Create: `src/knockknock/db/enums.py`
- Create: `tests/test_db/__init__.py`
- Create: `tests/test_db/test_enums.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_db/test_enums.py`:

```python
from __future__ import annotations

from knockknock.db.enums import (
    CompanySizeBucket,
    DraftState,
    GeminiModel,
    GeminiPurpose,
    JobSource,
    JobStatus,
    PhonebookSource,
    PipelineRunStatus,
    PipelineStage,
    RejectionReason,
    TelegramDirection,
    TelegramKind,
)


def test_all_enums_have_string_values() -> None:
    enums = [
        JobSource,
        CompanySizeBucket,
        PhonebookSource,
        JobStatus,
        RejectionReason,
        PipelineStage,
        PipelineRunStatus,
        DraftState,
        GeminiModel,
        GeminiPurpose,
        TelegramDirection,
        TelegramKind,
    ]
    for enum_cls in enums:
        for member in enum_cls:
            assert isinstance(member.value, str)
            assert member.value == member.value.upper() or member.value.islower()


def test_job_status_has_terminal_states() -> None:
    assert JobStatus.SENT in JobStatus
    assert JobStatus.USER_REJECTED in JobStatus
    assert JobStatus.ERROR in JobStatus
```

Create empty `tests/test_db/__init__.py`.

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_db/test_enums.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'knockknock.db'`.

- [ ] **Step 3: Implement enums**

Create `src/knockknock/db/__init__.py` (empty).

Create `src/knockknock/db/enums.py`:

```python
"""Postgres enum values mirrored as Python StrEnums."""

from __future__ import annotations

from enum import StrEnum


class JobSource(StrEnum):
    HN = "HN"
    WELLFOUND = "WELLFOUND"
    YC_WAAS = "YC_WAAS"
    GREENHOUSE = "GREENHOUSE"
    LEVER = "LEVER"
    ASHBY = "ASHBY"


class CompanySizeBucket(StrEnum):
    SEED = "SEED"
    SERIES_A = "SERIES_A"
    SERIES_B = "SERIES_B"
    SERIES_C_PLUS = "SERIES_C_PLUS"
    UNKNOWN = "UNKNOWN"


class PhonebookSource(StrEnum):
    APOLLO = "APOLLO"
    HUNTER = "HUNTER"
    PATTERN_GUESS = "PATTERN_GUESS"
    MANUAL = "MANUAL"


class JobStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    PRE_FILTERED = "PRE_FILTERED"
    PRE_FILTER_REJECTED = "PRE_FILTER_REJECTED"
    SCORED = "SCORED"
    SCORE_REJECTED = "SCORE_REJECTED"
    ENRICHED = "ENRICHED"
    ENRICH_FAILED = "ENRICH_FAILED"
    TAILORED = "TAILORED"
    DRAFTED = "DRAFTED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    SENT = "SENT"
    USER_REJECTED = "USER_REJECTED"
    ERROR = "ERROR"


class RejectionReason(StrEnum):
    BLACKLISTED = "BLACKLISTED"
    LOCATION_MISMATCH = "LOCATION_MISMATCH"
    ROLE_MISMATCH = "ROLE_MISMATCH"
    SENIORITY_MISMATCH = "SENIORITY_MISMATCH"
    SCORE_BELOW_THRESHOLD = "SCORE_BELOW_THRESHOLD"
    NO_CONTACT_FOUND = "NO_CONTACT_FOUND"
    USER_REJECTED = "USER_REJECTED"
    OTHER = "OTHER"


class PipelineStage(StrEnum):
    DISCOVER = "DISCOVER"
    PRE_FILTER = "PRE_FILTER"
    SCORE = "SCORE"
    ENRICH = "ENRICH"
    TAILOR = "TAILOR"
    DRAFT = "DRAFT"


class PipelineRunStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class DraftState(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    SENT = "SENT"


class GeminiModel(StrEnum):
    FLASH_2_5 = "gemini-2.5-flash"
    PRO_2_5 = "gemini-2.5-pro"


class GeminiPurpose(StrEnum):
    SCORE = "SCORE"
    DRAFT = "DRAFT"


class TelegramDirection(StrEnum):
    OUTBOUND = "OUTBOUND"
    INBOUND = "INBOUND"


class TelegramKind(StrEnum):
    APPROVAL_REQUEST = "APPROVAL_REQUEST"
    CALLBACK = "CALLBACK"
    ALERT = "ALERT"
    DIGEST = "DIGEST"
```

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/test_db/test_enums.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/knockknock/db tests/test_db
git commit -m "feat(db): add Postgres enums as StrEnums"
```

### Task 1.2: SQLModel table definitions

**Files:**
- Create: `src/knockknock/db/models.py`
- Create: `tests/test_db/test_models.py`

- [ ] **Step 1: Write failing test for model importability and FK shape**

Create `tests/test_db/test_models.py`:

```python
from __future__ import annotations

from sqlmodel import SQLModel

from knockknock.db import models


def test_all_tables_registered() -> None:
    expected = {
        "companies",
        "companies_blacklist",
        "phonebook",
        "job_applications",
        "job_application_events",
        "email_drafts",
        "gemini_call_logs",
        "telegram_messages",
        "pipeline_runs",
        "scraper_states",
    }
    actual = set(SQLModel.metadata.tables.keys())
    assert expected.issubset(actual), f"missing tables: {expected - actual}"


def test_job_application_has_company_fk() -> None:
    table = SQLModel.metadata.tables["job_applications"]
    fks = {fk.column.table.name for fk in table.foreign_keys}
    assert "companies" in fks


def test_email_draft_has_job_fk() -> None:
    table = SQLModel.metadata.tables["email_drafts"]
    fks = {fk.column.table.name for fk in table.foreign_keys}
    assert "job_applications" in fks


def test_company_models_imported() -> None:
    assert models.Company.__tablename__ == "companies"
    assert models.JobApplication.__tablename__ == "job_applications"
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_db/test_models.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement SQLModel tables**

Create `src/knockknock/db/models.py`:

```python
"""SQLModel ORM definitions mirroring the Phase-1 schema."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from knockknock.db.enums import (
    CompanySizeBucket,
    DraftState,
    GeminiModel,
    GeminiPurpose,
    JobSource,
    JobStatus,
    PhonebookSource,
    PipelineRunStatus,
    PipelineStage,
    RejectionReason,
    TelegramDirection,
    TelegramKind,
)


def _pg_enum(enum_cls: type, name: str) -> PgEnum:
    return PgEnum(
        enum_cls,
        name=name,
        create_type=False,
        values_callable=lambda e: [m.value for m in e],
    )


class Company(SQLModel, table=True):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("domain", name="companies_domain_unique"),)

    id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    name: str = Field(sa_column=Column(Text, nullable=False))
    domain: str = Field(sa_column=Column(Text, nullable=False))
    careers_url: Optional[str] = Field(default=None, sa_column=Column(Text))
    size_bucket: CompanySizeBucket = Field(
        sa_column=Column(_pg_enum(CompanySizeBucket, "company_size_bucket"), nullable=False)
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


class CompanyBlacklist(SQLModel, table=True):
    __tablename__ = "companies_blacklist"

    id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    pattern: str = Field(sa_column=Column(Text, nullable=False, unique=True))
    reason: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )


class PhonebookEntry(SQLModel, table=True):
    __tablename__ = "phonebook"
    __table_args__ = (UniqueConstraint("company_id", name="phonebook_company_unique"),)

    id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    company_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    founder_email: Optional[str] = Field(default=None, sa_column=Column(Text))
    founder_name: Optional[str] = Field(default=None, sa_column=Column(Text))
    careers_email: Optional[str] = Field(default=None, sa_column=Column(Text))
    source: PhonebookSource = Field(
        sa_column=Column(_pg_enum(PhonebookSource, "phonebook_source"), nullable=False)
    )
    confidence: int = Field(sa_column=Column(SmallInteger, nullable=False))
    last_verified_at: Optional[datetime] = Field(
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


class JobApplication(SQLModel, table=True):
    __tablename__ = "job_applications"
    __table_args__ = (
        UniqueConstraint("source", "source_job_id", name="jobs_source_unique"),
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_company", "company_id"),
    )

    id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    company_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("companies.id", ondelete="RESTRICT"),
            nullable=False,
        )
    )
    source: JobSource = Field(sa_column=Column(_pg_enum(JobSource, "job_source"), nullable=False))
    source_job_id: str = Field(sa_column=Column(Text, nullable=False))
    title: str = Field(sa_column=Column(Text, nullable=False))
    location: str = Field(sa_column=Column(Text, nullable=False))
    apply_url: str = Field(sa_column=Column(Text, nullable=False))
    description: str = Field(sa_column=Column(Text, nullable=False))
    posted_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    discovered_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )
    status: JobStatus = Field(
        sa_column=Column(_pg_enum(JobStatus, "job_status"), nullable=False, server_default="DISCOVERED")
    )
    score: Optional[int] = Field(default=None, sa_column=Column(SmallInteger))
    rejection_reason: Optional[RejectionReason] = Field(
        default=None, sa_column=Column(_pg_enum(RejectionReason, "rejection_reason"))
    )
    rejection_detail: Optional[str] = Field(default=None, sa_column=Column(Text))
    resume_variant_key: Optional[str] = Field(default=None, sa_column=Column(Text))
    last_error: Optional[str] = Field(default=None, sa_column=Column(Text))
    retry_count: int = Field(
        sa_column=Column(SmallInteger, nullable=False, server_default="0")
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


class JobApplicationEvent(SQLModel, table=True):
    __tablename__ = "job_application_events"
    __table_args__ = (Index("ix_events_job", "job_id"),)

    id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    job_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("job_applications.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    from_status: Optional[JobStatus] = Field(
        default=None, sa_column=Column(_pg_enum(JobStatus, "job_status"))
    )
    to_status: JobStatus = Field(
        sa_column=Column(_pg_enum(JobStatus, "job_status"), nullable=False)
    )
    note: Optional[str] = Field(default=None, sa_column=Column(Text))
    payload: Optional[dict] = Field(default=None, sa_column=Column(JSONB))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )


class EmailDraft(SQLModel, table=True):
    __tablename__ = "email_drafts"
    __table_args__ = (
        Index(
            "ix_email_drafts_active_per_job",
            "job_id",
            unique=True,
            postgresql_where="state = 'PENDING'",
        ),
    )

    id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    job_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("job_applications.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    gmail_draft_id: Optional[str] = Field(default=None, sa_column=Column(Text))
    subject: str = Field(sa_column=Column(Text, nullable=False))
    body: str = Field(sa_column=Column(Text, nullable=False))
    to_recipients: list[str] = Field(sa_column=Column(JSONB, nullable=False))
    cc_recipients: Optional[list[str]] = Field(default=None, sa_column=Column(JSONB))
    state: DraftState = Field(
        sa_column=Column(_pg_enum(DraftState, "draft_state"), nullable=False, server_default="PENDING")
    )
    regeneration_of: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("email_drafts.id", ondelete="SET NULL")),
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )
    decided_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    sent_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))


class GeminiCallLog(SQLModel, table=True):
    __tablename__ = "gemini_call_logs"
    __table_args__ = (Index("ix_gemini_called_at", "called_at"),)

    id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    job_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger, ForeignKey("job_applications.id", ondelete="SET NULL")
        ),
    )
    model: GeminiModel = Field(
        sa_column=Column(_pg_enum(GeminiModel, "gemini_model"), nullable=False)
    )
    purpose: GeminiPurpose = Field(
        sa_column=Column(_pg_enum(GeminiPurpose, "gemini_purpose"), nullable=False)
    )
    prompt_tokens: int = Field(sa_column=Column(Integer, nullable=False))
    completion_tokens: int = Field(sa_column=Column(Integer, nullable=False))
    latency_ms: int = Field(sa_column=Column(Integer, nullable=False))
    success: bool = Field(sa_column=Column(nullable=False))
    error_code: Optional[str] = Field(default=None, sa_column=Column(Text))
    called_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )


class TelegramMessage(SQLModel, table=True):
    __tablename__ = "telegram_messages"
    __table_args__ = (Index("ix_tg_job", "job_id"),)

    id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    job_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger, ForeignKey("job_applications.id", ondelete="SET NULL")
        ),
    )
    direction: TelegramDirection = Field(
        sa_column=Column(_pg_enum(TelegramDirection, "telegram_direction"), nullable=False)
    )
    kind: TelegramKind = Field(
        sa_column=Column(_pg_enum(TelegramKind, "telegram_kind"), nullable=False)
    )
    chat_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    message_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger))
    payload: dict = Field(sa_column=Column(JSONB, nullable=False))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )


class PipelineRun(SQLModel, table=True):
    __tablename__ = "pipeline_runs"
    __table_args__ = (Index("ix_pipeline_started_at", "started_at"),)

    id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    started_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    )
    finished_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    status: PipelineRunStatus = Field(
        sa_column=Column(_pg_enum(PipelineRunStatus, "pipeline_run_status"), nullable=False)
    )
    discovered_count: int = Field(sa_column=Column(Integer, nullable=False, server_default="0"))
    pre_filtered_count: int = Field(sa_column=Column(Integer, nullable=False, server_default="0"))
    scored_count: int = Field(sa_column=Column(Integer, nullable=False, server_default="0"))
    drafted_count: int = Field(sa_column=Column(Integer, nullable=False, server_default="0"))
    error_count: int = Field(sa_column=Column(Integer, nullable=False, server_default="0"))
    summary: Optional[dict] = Field(default=None, sa_column=Column(JSONB))


class ScraperState(SQLModel, table=True):
    __tablename__ = "scraper_states"

    id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    source: JobSource = Field(
        sa_column=Column(_pg_enum(JobSource, "job_source"), nullable=False, unique=True)
    )
    cursor: Optional[str] = Field(default=None, sa_column=Column(Text))
    last_run_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    updated_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        )
    )
```

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/test_db/test_models.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/knockknock/db/models.py tests/test_db/test_models.py
git commit -m "feat(db): add SQLModel definitions for all 10 tables"
```

### Task 1.3: Engine + session helpers

**Files:**
- Create: `src/knockknock/db/engine.py`
- Create: `src/knockknock/db/session.py`
- Create: `tests/test_db/test_engine.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_db/test_engine.py`:

```python
from __future__ import annotations

from knockknock.db.engine import make_sync_engine


def test_make_sync_engine_returns_engine_with_dsn() -> None:
    engine = make_sync_engine("postgresql+psycopg://u:p@h/db")
    assert "postgresql+psycopg" in str(engine.url)
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_db/test_engine.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement engine and session**

Create `src/knockknock/db/engine.py`:

```python
"""SQLAlchemy engine factories."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def make_sync_engine(dsn: str, *, echo: bool = False) -> Engine:
    """Build a sync engine. DSN must be `postgresql+psycopg://...`."""
    if not dsn.startswith("postgresql+psycopg"):
        raise ValueError("sync DSN must start with postgresql+psycopg")
    return create_engine(dsn, echo=echo, pool_pre_ping=True, pool_size=5, max_overflow=5)


def make_async_engine(dsn: str, *, echo: bool = False) -> AsyncEngine:
    """Build an async engine. DSN must be `postgresql+asyncpg://...`."""
    if not dsn.startswith("postgresql+asyncpg"):
        raise ValueError("async DSN must start with postgresql+asyncpg")
    return create_async_engine(dsn, echo=echo, pool_pre_ping=True)
```

Create `src/knockknock/db/session.py`:

```python
"""Session context managers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine
from sqlmodel import Session


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Provide a transactional scope around a series of operations."""
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/test_db/test_engine.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/knockknock/db/engine.py src/knockknock/db/session.py tests/test_db/test_engine.py
git commit -m "feat(db): add engine + session_scope helpers"
```

### Task 1.4: Alembic init + initial migration

**Files:**
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/script.py.mako`
- Create: `migrations/versions/0001_initial_schema.py`

- [ ] **Step 1: Initialise Alembic**

```bash
uv run alembic init -t generic migrations
```

This creates `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, and `migrations/versions/`. We will overwrite the first two.

- [ ] **Step 2: Overwrite `alembic.ini` (keep only relevant sections)**

```ini
[alembic]
script_location = migrations
prepend_sys_path = .
version_path_separator = os
sqlalchemy.url =

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

- [ ] **Step 3: Overwrite `migrations/env.py`**

```python
"""Alembic environment using KNOCKKNOCK_DATABASE_URL from env."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from knockknock.db import models  # noqa: F401  (register tables on metadata)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def _dsn() -> str:
    dsn = os.environ.get("KNOCKKNOCK_DATABASE_URL")
    if not dsn:
        raise RuntimeError("KNOCKKNOCK_DATABASE_URL not set")
    return dsn


def run_migrations_offline() -> None:
    context.configure(
        url=_dsn(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section) or {}
    cfg["sqlalchemy.url"] = _dsn()
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, compare_type=True
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Write the initial-schema migration manually**

Create `migrations/versions/0001_initial_schema.py`:

```python
"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-05-28 00:00:00.000000
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
COMPANY_SIZE = ("SEED", "SERIES_A", "SERIES_B", "SERIES_C_PLUS", "UNKNOWN")
PHONEBOOK_SOURCE = ("APOLLO", "HUNTER", "PATTERN_GUESS", "MANUAL")
JOB_STATUS = (
    "DISCOVERED", "PRE_FILTERED", "PRE_FILTER_REJECTED", "SCORED", "SCORE_REJECTED",
    "ENRICHED", "ENRICH_FAILED", "TAILORED", "DRAFTED", "AWAITING_APPROVAL",
    "APPROVED", "SENT", "USER_REJECTED", "ERROR",
)
REJECTION_REASON = (
    "BLACKLISTED", "LOCATION_MISMATCH", "ROLE_MISMATCH", "SENIORITY_MISMATCH",
    "SCORE_BELOW_THRESHOLD", "NO_CONTACT_FOUND", "USER_REJECTED", "OTHER",
)
PIPELINE_STAGE = ("DISCOVER", "PRE_FILTER", "SCORE", "ENRICH", "TAILOR", "DRAFT")
PIPELINE_RUN_STATUS = ("RUNNING", "SUCCESS", "PARTIAL", "FAILED")
DRAFT_STATE = ("PENDING", "APPROVED", "REJECTED", "SUPERSEDED", "SENT")
GEMINI_MODEL = ("gemini-2.5-flash", "gemini-2.5-pro")
GEMINI_PURPOSE = ("SCORE", "DRAFT")
TELEGRAM_DIRECTION = ("OUTBOUND", "INBOUND")
TELEGRAM_KIND = ("APPROVAL_REQUEST", "CALLBACK", "ALERT", "DIGEST")


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
    _create_enum("draft_state", DRAFT_STATE)
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
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("domain", name="companies_domain_unique"),
    )

    op.create_table(
        "companies_blacklist",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("pattern", sa.Text, nullable=False, unique=True),
        sa.Column("reason", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
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
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
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
        sa.Column("source", postgresql.ENUM(name="job_source", create_type=False), nullable=False),
        sa.Column("source_job_id", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("location", sa.Text, nullable=False),
        sa.Column("apply_url", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.Column("discovered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="job_status", create_type=False),
            nullable=False,
            server_default="DISCOVERED",
        ),
        sa.Column("score", sa.SmallInteger),
        sa.Column("rejection_reason", postgresql.ENUM(name="rejection_reason", create_type=False)),
        sa.Column("rejection_detail", sa.Text),
        sa.Column("resume_variant_key", sa.Text),
        sa.Column("last_error", sa.Text),
        sa.Column("retry_count", sa.SmallInteger, server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
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
        sa.Column("from_status", postgresql.ENUM(name="job_status", create_type=False)),
        sa.Column("to_status", postgresql.ENUM(name="job_status", create_type=False), nullable=False),
        sa.Column("note", sa.Text),
        sa.Column("payload", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
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
            postgresql.ENUM(name="draft_state", create_type=False),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column(
            "regeneration_of",
            sa.BigInteger,
            sa.ForeignKey("email_drafts.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_email_drafts_active_per_job",
        "email_drafts",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text("state = 'PENDING'"),
    )

    op.create_table(
        "gemini_call_logs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "job_id",
            sa.BigInteger,
            sa.ForeignKey("job_applications.id", ondelete="SET NULL"),
        ),
        sa.Column("model", postgresql.ENUM(name="gemini_model", create_type=False), nullable=False),
        sa.Column("purpose", postgresql.ENUM(name="gemini_purpose", create_type=False), nullable=False),
        sa.Column("prompt_tokens", sa.Integer, nullable=False),
        sa.Column("completion_tokens", sa.Integer, nullable=False),
        sa.Column("latency_ms", sa.Integer, nullable=False),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("error_code", sa.Text),
        sa.Column("called_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
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
        sa.Column("direction", postgresql.ENUM(name="telegram_direction", create_type=False), nullable=False),
        sa.Column("kind", postgresql.ENUM(name="telegram_kind", create_type=False), nullable=False),
        sa.Column("chat_id", sa.BigInteger, nullable=False),
        sa.Column("message_id", sa.BigInteger),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tg_job", "telegram_messages", ["job_id"])

    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
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
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
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
        "telegram_kind", "telegram_direction", "gemini_purpose", "gemini_model",
        "draft_state", "pipeline_run_status", "pipeline_stage", "rejection_reason",
        "job_status", "phonebook_source", "company_size_bucket", "job_source",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
```

- [ ] **Step 5: Apply migration to Neon**

Ensure `.env` is loaded (or export inline):

```bash
export $(grep -v '^#' .env | xargs)
uv run alembic upgrade head
```

Expected: `INFO  [alembic.runtime.migration] Running upgrade  -> 0001_initial_schema`.

- [ ] **Step 6: Verify schema in Neon SQL editor**

Run in Neon SQL console:

```sql
SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name;
```

Expected: 10 tables + `alembic_version`.

- [ ] **Step 7: Commit**

```bash
git add alembic.ini migrations/
git commit -m "feat(db): add Alembic and initial schema migration"
```

### Task 1.5: Integration test against real Postgres

**Files:**
- Modify: `tests/conftest.py` — add `db_engine` and `db_session` fixtures
- Create: `tests/test_db/test_roundtrip.py`

- [ ] **Step 1: Extend `tests/conftest.py`**

Replace `tests/conftest.py` with:

```python
from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from knockknock.db.engine import make_sync_engine


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KNOCKKNOCK_DATABASE_URL", raising=False)


@pytest.fixture(scope="session")
def db_engine() -> Engine:
    dsn = os.environ.get("KNOCKKNOCK_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("KNOCKKNOCK_TEST_DATABASE_URL not set; skipping DB integration tests")
    return make_sync_engine(dsn)


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    """Open a session inside a transaction that rolls back at teardown."""
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
```

- [ ] **Step 2: Write integration test**

Create `tests/test_db/test_roundtrip.py`:

```python
from __future__ import annotations

from sqlmodel import Session, select

from knockknock.db.enums import CompanySizeBucket, JobSource, JobStatus
from knockknock.db.models import Company, JobApplication


def test_company_roundtrip(db_session: Session) -> None:
    company = Company(
        name="Acme Labs",
        domain="acme.test",
        size_bucket=CompanySizeBucket.SERIES_A,
    )
    db_session.add(company)
    db_session.flush()

    fetched = db_session.exec(select(Company).where(Company.domain == "acme.test")).one()
    assert fetched.name == "Acme Labs"
    assert fetched.size_bucket == CompanySizeBucket.SERIES_A


def test_job_application_requires_company(db_session: Session) -> None:
    company = Company(name="Beta", domain="beta.test", size_bucket=CompanySizeBucket.SEED)
    db_session.add(company)
    db_session.flush()

    job = JobApplication(
        company_id=company.id,
        source=JobSource.HN,
        source_job_id="hn-1",
        title="Backend Engineer",
        location="Bengaluru",
        apply_url="https://example.test/apply",
        description="Build things",
        status=JobStatus.DISCOVERED,
    )
    db_session.add(job)
    db_session.flush()

    fetched = db_session.exec(
        select(JobApplication).where(JobApplication.source_job_id == "hn-1")
    ).one()
    assert fetched.title == "Backend Engineer"
    assert fetched.status == JobStatus.DISCOVERED
```

- [ ] **Step 3: Run integration test against Neon**

```bash
export KNOCKKNOCK_TEST_DATABASE_URL="$KNOCKKNOCK_DATABASE_URL"
uv run pytest tests/test_db/test_roundtrip.py -v
```

Expected: both tests PASS. Unset variable: tests are skipped (also OK).

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py tests/test_db/test_roundtrip.py
git commit -m "test(db): add roundtrip integration tests against Neon"
```

---

← [Index](00-index.md) · [Prev: phase-00-bootstrap.md](phase-00-bootstrap.md) · [Next: phase-02-config.md](phase-02-config.md)
