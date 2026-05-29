"""FastAPI app for the Telegram webhook.

One endpoint that matters: ``POST /telegram/webhook``. Telegram delivers
``Update`` payloads here; we verify the shared-secret header, build a
:class:`knockknock.telegram_bot.handlers.HandlerContext`, and hand the
update to :func:`knockknock.telegram_bot.handlers.handle_callback`.

Design notes:

- **Header verification first.** Telegram sets
  ``X-Telegram-Bot-Api-Secret-Token`` to the value passed at
  ``setWebhook`` time. We compare with :func:`hmac.compare_digest` (via
  the cheap string equality on a constant-length token, but explicit
  compare_digest avoids a timing leak on the prefix). A mismatch returns
  401 *before* parsing JSON so a flood of bad requests stays cheap.
- **Handler errors -> 200.** Telegram retries non-2xx forever, so we
  trap :class:`UnauthorizedError` from the handler (wrong chat id, bad
  HMAC, unknown draft) and ack 200. The handler has already logged the
  detail; the attacker gets no feedback about which axis of authz
  failed.
- **Session lifetime.** ``session_scope`` is injected so tests can
  reuse the per-test transactional Session (commits become SAVEPOINT
  releases inside the outer transaction). In production it's a fresh
  ``Session(bind=engine)`` per request -- the handler does its own
  ``commit()`` so there's nothing for a context manager to do at exit.
- **No Gmail.** Per Phase 9 brainstorm, the bot service is DB-mediated:
  it flips ``job.status -> APPROVED`` and the pipeline's SendStage owns
  the Gmail send. The app therefore only injects the Telegram client.
- **Health check.** ``GET /healthz`` returns 200 unconditionally so a
  process supervisor (uvicorn under systemd, k8s, ...) can liveness-probe
  without hitting the DB.
"""

from __future__ import annotations

import hmac
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol

import structlog
from fastapi import FastAPI, Header, HTTPException, Request
from sqlmodel import Session

from knockknock.telegram_bot.handlers import (
    HandlerContext,
    UnauthorizedError,
    handle_callback,
)

log = structlog.get_logger(__name__)


class _TgLike(Protocol):
    """Subset of :class:`knockknock.clients.telegram.TelegramClient` the
    handler calls. Kept here so the app's dependency injection point is
    structurally typed -- tests inject a tiny dataclass stub.
    """

    async def edit_card_to_status(self, *, message_id: int, status_text: str) -> None: ...
    async def answer_callback(self, *, query_id: str, text: str) -> None: ...


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Runtime knobs. Built once at boot from env vars (Task 9.7).

    - ``webhook_secret``: the value Telegram echoes back in the
      ``X-Telegram-Bot-Api-Secret-Token`` header (configured at
      ``setWebhook`` time).
    - ``callback_secret``: HMAC key for ``callback_data``. Different
      from ``webhook_secret`` because they have different threat models
      (header secret protects the endpoint; callback secret protects
      individual button presses).
    - ``admin_chat_id``: only chat the handler will act on. Anything
      else gets dropped as :class:`UnauthorizedError`.
    """

    webhook_secret: str
    callback_secret: str
    admin_chat_id: int


SessionScope = Callable[[], AbstractContextManager[Session]]


def build_app(
    *,
    config: AppConfig,
    session_scope: SessionScope,
    telegram: _TgLike,
) -> FastAPI:
    """Construct the FastAPI app with its dependencies bound in.

    Returning a fresh app from a builder (rather than declaring a
    module-level ``app``) makes the wiring explicit and keeps tests
    hermetic -- each test gets its own app with its own stubs.
    """
    app = FastAPI(title="knockknock-telegram-bot")

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/telegram/webhook")
    async def webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> dict[str, bool]:
        # --- header verification (before we even parse JSON) -----------
        if x_telegram_bot_api_secret_token is None or not hmac.compare_digest(
            x_telegram_bot_api_secret_token, config.webhook_secret
        ):
            log.warning(
                "telegram.webhook_secret_mismatch",
                provided=bool(x_telegram_bot_api_secret_token),
            )
            # 401 here is fine -- this is the *transport*-level secret;
            # a leaked-secret bot will fail this every time and Telegram
            # never sees the 401 anyway (it's an attacker hitting our
            # endpoint directly).
            raise HTTPException(status_code=401, detail="invalid webhook secret")

        update: dict[str, Any] = await request.json()
        update_id = update.get("update_id")
        started = time.monotonic()
        action: str | None = None
        try:
            with session_scope() as session:
                ctx = HandlerContext(
                    session=session,
                    telegram=telegram,
                    admin_chat_id=config.admin_chat_id,
                    callback_secret=config.callback_secret,
                )
                try:
                    result = await handle_callback(update, ctx)
                except UnauthorizedError as exc:
                    # Don't echo a 4xx -- Telegram would retry forever
                    # and an attacker gets no info about which axis
                    # failed. Log loudly so we can spot abuse in metrics.
                    log.warning(
                        "telegram.callback_unauthorized",
                        update_id=update_id,
                        error=str(exc),
                    )
                else:
                    if result is not None:
                        action = result.action.value
        finally:
            duration_ms = int((time.monotonic() - started) * 1000)
            log.info(
                "telegram.webhook",
                update_id=update_id,
                action=action,
                duration_ms=duration_ms,
            )
        return {"ok": True}

    return app
