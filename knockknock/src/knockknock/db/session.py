"""Session context managers.

Two entry points:

* ``session_scope(engine)`` — a free-function context manager used inside
  Phase 1 integration tests and ad-hoc scripts.
* ``SessionFactory(engine=...)`` — a dataclass that bundles an engine with a
  ``__call__`` returning a context manager. Per errata E.3 in
  docs/superpowers/plans/2026-05-28-knockknock/00-index.md, downstream phases
  (pipeline factory, CLI, Telegram bot, NotifyStage) inject this into
  long-lived components instead of an engine.

Both helpers wrap a SQLModel ``Session`` in a transaction: on clean exit
the session is committed, on any exception it is rolled back. The session
is always closed.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

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


@dataclass(slots=True)
class SessionFactory:
    """Callable that yields a transactional Session.

    Pass to stages/services as ``session_factory: SessionFactory``, then use
    ``with session_factory() as session: ...``.
    """

    engine: Engine

    @contextmanager
    def __call__(self) -> Iterator[Session]:
        with session_scope(self.engine) as session:
            yield session
