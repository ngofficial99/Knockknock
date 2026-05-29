"""Service-level wiring: FastAPI app + background NotifyPoller, glued
by a FastAPI ``lifespan``.

The HTTP app (in :mod:`knockknock.telegram_bot.app`) is intentionally
pure -- routes only, no asyncio task management -- so it's
trivially-testable via :class:`fastapi.testclient.TestClient`. The
background poller (in :mod:`knockknock.telegram_bot.poller`) is its own
async function that runs forever and shuts down cleanly on cancel.

This module is the place those two halves meet:

* On lifespan startup, schedule ``run_forever`` as a background task.
* On lifespan shutdown (SIGTERM / uvicorn reload / Ctrl-C), ``cancel``
  the task and ``await`` it so the poller has a chance to log
  ``poller.cancelled`` and close its DB session.

Tests inject ``poller_runner`` to swap in a fake coroutine; production
uses the real ``poller.run_forever``. ``start_poller=False`` is the
escape hatch for tests that want the app without the background task.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI

from knockknock.db.session import SessionFactory
from knockknock.telegram_bot.app import AppConfig, _TgLike, build_app
from knockknock.telegram_bot.poller import run_forever as real_run_forever

log = structlog.get_logger(__name__)

# A poller runner is anything callable as ``await runner(session_factory=...,
# telegram=..., poll_interval_seconds=...)``. We default to the real
# ``run_forever`` but tests inject a stub that records start + cancel.
PollerRunner = Callable[..., Coroutine[Any, Any, None]]


def build_service(
    *,
    config: AppConfig,
    session_factory: SessionFactory | Any,
    telegram: _TgLike,
    start_poller: bool = True,
    poller_runner: PollerRunner | None = None,
    poll_interval_seconds: float = 60.0,
) -> FastAPI:
    """Construct the bot service: FastAPI app + (optional) poller task.

    ``session_factory`` is typed as ``SessionFactory | Any`` because
    tests pass a structural stub. At runtime we only need a callable
    that yields a context manager; the type checker doesn't gain much
    by being strict here.

    ``start_poller``:
        - ``True`` (production): the FastAPI lifespan spawns
          ``poller_runner`` and cancels it on shutdown.
        - ``False`` (tests + ad-hoc HTTP-only runs): no background task.

    ``poller_runner``:
        - ``None`` -> use the production poller (``run_forever``).
        - callable -> use that instead. Test-only seam.
    """
    # ``real_run_forever`` has a stricter signature than ``PollerRunner``
    # (its ``session_factory`` parameter expects the poller-local
    # ``_SessionFactory`` Protocol, and ``telegram`` expects the
    # poller-local ``_TgLike``). At runtime our SessionFactory + the
    # production TelegramClient satisfy both poller-local Protocols
    # structurally, but each module declares its own narrow Protocol so
    # mypy sees them as distinct types. Cast through the PollerRunner
    # alias which intentionally uses ``Callable[..., Coroutine[...]]``
    # so test stubs and the production fn both fit.
    runner: PollerRunner = poller_runner if poller_runner is not None else real_run_forever

    # The FastAPI lifespan: a single async context manager that runs at
    # startup (before requests are accepted) and shutdown (after the
    # server has stopped accepting requests).
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        task: asyncio.Task[None] | None = None
        if start_poller:
            log.info("service.poller_starting", interval_s=poll_interval_seconds)
            task = asyncio.create_task(
                runner(
                    session_factory=session_factory,
                    telegram=telegram,
                    poll_interval_seconds=poll_interval_seconds,
                ),
                name="knockknock-poller",
            )
        try:
            yield
        finally:
            if task is not None:
                log.info("service.poller_stopping")
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    # Expected: the poller catches CancelledError and
                    # re-raises so the supervisor sees clean shutdown.
                    pass
                except Exception as exc:
                    # Defensive: if the poller died for some other
                    # reason during cancellation, log it but don't
                    # block the FastAPI shutdown.
                    log.warning("service.poller_shutdown_error", error=str(exc))

    # Build the inner HTTP app (routes + handlers) and attach the
    # lifespan to it. ``router.lifespan_context`` is the FastAPI 0.110+
    # way to do this *after* construction without rebuilding the app.
    app = build_app(
        config=config,
        session_scope=session_factory,
        telegram=telegram,
    )
    app.router.lifespan_context = lifespan
    return app
