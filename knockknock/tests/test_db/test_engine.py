from __future__ import annotations

import pytest

from knockknock.db.engine import make_async_engine, make_sync_engine
from knockknock.db.session import SessionFactory, session_scope


def test_make_sync_engine_returns_engine_with_dsn() -> None:
    engine = make_sync_engine("postgresql+psycopg://u:p@h/db")
    assert "postgresql+psycopg" in str(engine.url)


def test_make_sync_engine_rejects_wrong_driver() -> None:
    with pytest.raises(ValueError, match="postgresql\\+psycopg"):
        make_sync_engine("postgresql://u:p@h/db")


def test_make_async_engine_returns_engine_with_dsn() -> None:
    engine = make_async_engine("postgresql+asyncpg://u:p@h/db")
    assert "postgresql+asyncpg" in str(engine.url)


def test_make_async_engine_rejects_wrong_driver() -> None:
    with pytest.raises(ValueError, match="postgresql\\+asyncpg"):
        make_async_engine("postgresql+psycopg://u:p@h/db")


def test_session_scope_is_context_manager() -> None:
    """session_scope is importable and is a context manager factory."""
    # Smoke-level: only verify the symbol exists and is callable.
    # Real transactional behavior is tested in the integration tests (Task 1.5).
    assert callable(session_scope)


def test_session_factory_is_dataclass_with_engine() -> None:
    """Errata E.3: SessionFactory is a dataclass holding an engine,
    callable as a context manager.
    """
    engine = make_sync_engine("postgresql+psycopg://u:p@h/db")
    factory = SessionFactory(engine=engine)
    assert factory.engine is engine
    # Callable -> returns a context manager
    cm = factory()
    assert hasattr(cm, "__enter__")
    assert hasattr(cm, "__exit__")
