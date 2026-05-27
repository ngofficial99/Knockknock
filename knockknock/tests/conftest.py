from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from knockknock.db.engine import make_sync_engine


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests insulated from a developer's exported DSN.

    Integration tests that need a real database read
    ``KNOCKKNOCK_TEST_DATABASE_URL`` (see ``db_engine``) — that name is
    deliberately distinct so it survives this fixture.
    """
    monkeypatch.delenv("KNOCKKNOCK_DATABASE_URL", raising=False)


@pytest.fixture(scope="session")
def db_engine() -> Engine:
    """Session-scoped sync engine for integration tests.

    Skips the test if ``KNOCKKNOCK_TEST_DATABASE_URL`` is not set so the
    suite stays green in environments without Postgres (CI without a DB).
    """
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
