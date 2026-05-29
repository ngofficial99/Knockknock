"""Run the knockknock Telegram bot service.

Invocation::

    uv run python -m knockknock.telegram_bot

The bot is its own process; it does not share the pipeline CLI's
:mod:`knockknock.__main__`. Per the Phase 9 brainstorm the bot service
holds the Telegram surface only -- no Gmail, no Gemini -- so its
threat model is "the worst an attacker who breaks in can do is flip
job-status rows in the DB". The pipeline's SendStage owns Gmail send.

Required env vars (sourced via :class:`Settings` + :class:`SecretsClient`):

* ``KNOCKKNOCK_DATABASE_URL``: ``postgresql+psycopg://...`` (sync DSN)
* ``KNOCKKNOCK_SECRET_TELEGRAM_BOT_TOKEN``: bot token from BotFather
* ``KNOCKKNOCK_SECRET_TELEGRAM_ADMIN_CHAT_ID``: numeric Telegram chat id
* ``KNOCKKNOCK_SECRET_TELEGRAM_CALLBACK_SECRET``: HMAC key for
  ``callback_data`` integrity (Phase 9.1)
* ``KNOCKKNOCK_SECRET_TELEGRAM_WEBHOOK_SECRET``: the value Telegram
  echoes in ``X-Telegram-Bot-Api-Secret-Token``

Optional env vars:

* ``PORT`` (default ``8080``): HTTP port for the webhook server.
* ``KNOCKKNOCK_BOT_POLL_INTERVAL_S`` (default ``60.0``): how often the
  background poller wakes to check for new ``DRAFT_CREATED`` rows.

This module is intentionally thin: all the wiring shape lives in
:mod:`knockknock.telegram_bot.service`. ``main`` here just resolves
secrets, builds the dependencies, and hands the FastAPI app to uvicorn.
"""

from __future__ import annotations

import os

import structlog
import uvicorn

from knockknock.clients.telegram import TelegramClient, build_bot
from knockknock.config.secrets import build_secrets_client
from knockknock.config.settings import Settings
from knockknock.db.engine import make_sync_engine
from knockknock.db.session import SessionFactory
from knockknock.logging import configure_logging
from knockknock.telegram_bot.app import AppConfig
from knockknock.telegram_bot.service import build_service

log = structlog.get_logger(__name__)


def main() -> None:
    """Resolve config + secrets, build the service, run uvicorn.

    Each `secrets.get(...)` is fail-loud: a missing secret raises
    :class:`ConfigError` at startup so we never run a half-configured
    bot. This is the right trade -- the alternative ("fall back to
    defaults") would silently swap our HMAC key for an empty string
    and accept arbitrary attacker-signed callback_data.
    """
    settings = Settings()
    configure_logging(level=settings.log_level)

    secrets = build_secrets_client(settings)
    engine = make_sync_engine(settings.database_url)

    # Resolve all secrets up-front so the bot fails fast if any are
    # missing. Don't lazy-resolve inside request handlers -- a webhook
    # that fails with ConfigError mid-request looks like a transient
    # bug rather than a configuration problem.
    bot_token = secrets.get("telegram-bot-token")
    admin_chat_id = int(secrets.get("telegram-admin-chat-id"))
    callback_secret = secrets.get("telegram-callback-secret")
    webhook_secret = secrets.get("telegram-webhook-secret")

    bot = build_bot(bot_token)
    telegram = TelegramClient(
        bot=bot,
        admin_chat_id=admin_chat_id,
        callback_secret=callback_secret,
    )

    session_factory = SessionFactory(engine=engine)

    poll_interval_s = float(os.environ.get("KNOCKKNOCK_BOT_POLL_INTERVAL_S", "60.0"))
    port = int(os.environ.get("PORT", "8080"))

    app = build_service(
        config=AppConfig(
            webhook_secret=webhook_secret,
            callback_secret=callback_secret,
            admin_chat_id=admin_chat_id,
        ),
        session_factory=session_factory,
        telegram=telegram,
        start_poller=True,
        poll_interval_seconds=poll_interval_s,
    )

    log.info(
        "telegram_bot.start",
        port=port,
        admin_chat_id=admin_chat_id,
        poll_interval_s=poll_interval_s,
    )
    # ``log_config=None`` so uvicorn doesn't override our structlog
    # setup with its own dict-config logger.
    uvicorn.run(app, host="0.0.0.0", port=port, log_config=None)  # noqa: S104


if __name__ == "__main__":
    main()
