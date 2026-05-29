"""Tests for ``build_service`` -- the wiring layer that combines the FastAPI
app from :mod:`knockknock.telegram_bot.app` with the background
:func:`knockknock.telegram_bot.poller.run_forever` task via a FastAPI
``lifespan``.

We don't test ``uvicorn.run`` itself (that's a third-party I/O wrapper);
we verify the lifespan contract:

* on startup, ``run_forever`` is scheduled as a background task with the
  configured session factory + telegram client;
* on shutdown, the task is cancelled and awaited cleanly.

Why a separate ``service.py`` (not folded into ``app.py``): keeping the
HTTP app pure (no asyncio task management) keeps ``app.py`` trivially
unit-testable via :class:`fastapi.testclient.TestClient`. The
service-level integration -- "app + poller in one process" -- is its own
concern with its own tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from knockknock.clients.telegram import DraftCardPayload
from knockknock.telegram_bot.app import AppConfig
from knockknock.telegram_bot.service import build_service


@dataclass
class _StubTg:
    """Test double for the Telegram client surface used by app + poller.

    Matches the structural Protocols both consumers expect:
    ``send_draft_card`` (for the poller), ``edit_card_to_status`` +
    ``answer_callback`` (for the webhook -- not exercised here but kept
    so structural typing holds).
    """

    sent_cards: list[DraftCardPayload] = field(default_factory=list)
    next_message_id: int = 7000

    async def send_draft_card(self, payload: DraftCardPayload) -> int:
        self.sent_cards.append(payload)
        self.next_message_id += 1
        return self.next_message_id

    async def edit_card_to_status(self, *, message_id: int, status_text: str) -> None:
        return None

    async def answer_callback(self, *, query_id: str, text: str) -> None:
        return None


@dataclass
class _SessionFactoryStub:
    """Records that the lifespan called us, so we can assert it did.

    The real ``SessionFactory`` yields a transactional ``Session``; here
    we just need a context-manager-shaped callable so the poller's
    ``with session_factory() as session`` doesn't blow up. The poller
    won't actually find any drafts in this stub session.
    """

    enter_count: int = 0
    sessions: list[Session] = field(default_factory=list)

    @contextmanager
    def __call__(self) -> Iterator[Session]:
        self.enter_count += 1
        # We need *something* shaped like a Session for the poller's
        # session.exec() call. Lazy import the db_session-equivalent
        # from conftest at the call site if needed; here we just yield a
        # sentinel and the poller won't get past the first session.exec
        # because we'll short-circuit via a tiny patch below. But the
        # simpler approach: pass `start_poller=False` so the lifespan
        # doesn't actually run the poller in this test.
        raise NotImplementedError("session factory should not be called in this test")
        yield  # pragma: no cover (unreachable but satisfies type checker)


def test_build_service_returns_fastapi_app() -> None:
    """``build_service`` returns a configured FastAPI app, not a raw
    function. Smoke-level check: the type is correct and ``/healthz``
    works through the TestClient.
    """
    tg = _StubTg()
    factory = _SessionFactoryStub()

    app = build_service(
        config=AppConfig(
            webhook_secret="wh",
            callback_secret="cb",
            admin_chat_id=1,
        ),
        session_factory=factory,
        telegram=tg,
        start_poller=False,  # we test lifespan separately
    )
    assert isinstance(app, FastAPI)
    # The HTTP routes from build_app are present.
    with TestClient(app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


def test_build_service_lifespan_starts_and_cancels_poller() -> None:
    """When ``start_poller=True``, the lifespan schedules ``run_forever``
    on startup and cancels it on shutdown. We don't run the real poller
    -- we inject a tiny coroutine via ``poller_runner`` that records
    start + cancel.
    """
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_poller(**_kwargs: Any) -> None:
        started.set()
        try:
            # Sleep "forever" so the lifespan cancellation actually
            # fires. asyncio.sleep is a cancellation point.
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    tg = _StubTg()
    factory = _SessionFactoryStub()

    app = build_service(
        config=AppConfig(
            webhook_secret="wh",
            callback_secret="cb",
            admin_chat_id=1,
        ),
        session_factory=factory,
        telegram=tg,
        start_poller=True,
        poller_runner=fake_poller,
        poll_interval_seconds=0.01,
    )

    with TestClient(app) as client:
        # TestClient's __enter__ drives the lifespan startup; by the
        # time we get here, the fake poller has been scheduled.
        # Give the event loop a tick to run it.
        # (TestClient runs an event loop internally; the fake poller's
        # first line runs synchronously inside that loop.)
        resp = client.get("/healthz")
        assert resp.status_code == 200
        # started should have been set by now
        assert started.is_set(), "poller should have started during lifespan startup"
    # On TestClient.__exit__, lifespan shutdown runs -> cancel the
    # poller task -> our fake catches CancelledError and sets the flag.
    assert cancelled.is_set(), "poller should have been cancelled during lifespan shutdown"


def test_build_service_no_poller_when_disabled() -> None:
    """``start_poller=False`` is the test-friendly path: app boots
    without scheduling any background task. The session_factory is not
    invoked.
    """
    poller_called = False

    async def fake_poller(**_kwargs: Any) -> None:
        nonlocal poller_called
        poller_called = True

    tg = _StubTg()
    factory = _SessionFactoryStub()

    app = build_service(
        config=AppConfig(
            webhook_secret="wh",
            callback_secret="cb",
            admin_chat_id=1,
        ),
        session_factory=factory,
        telegram=tg,
        start_poller=False,
        poller_runner=fake_poller,
    )
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
    assert not poller_called
    assert factory.enter_count == 0
