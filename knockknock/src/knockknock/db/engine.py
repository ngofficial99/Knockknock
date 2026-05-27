"""SQLAlchemy engine factories.

Sync engine uses psycopg (v3) driver; async engine uses asyncpg.
Errata E.3 in docs/superpowers/plans/2026-05-28-knockknock/00-index.md notes
``SessionFactory`` lives in db.session and wraps the sync engine.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def make_sync_engine(dsn: str, *, echo: bool = False) -> Engine:
    """Build a sync engine. DSN must start with ``postgresql+psycopg``."""
    if not dsn.startswith("postgresql+psycopg"):
        raise ValueError(
            f"sync DSN must start with postgresql+psycopg (got: {dsn.split('://', 1)[0]!r})"
        )
    return create_engine(
        dsn,
        echo=echo,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
    )


def make_async_engine(dsn: str, *, echo: bool = False) -> AsyncEngine:
    """Build an async engine. DSN must start with ``postgresql+asyncpg``."""
    if not dsn.startswith("postgresql+asyncpg"):
        raise ValueError(
            f"async DSN must start with postgresql+asyncpg (got: {dsn.split('://', 1)[0]!r})"
        )
    return create_async_engine(dsn, echo=echo, pool_pre_ping=True)
