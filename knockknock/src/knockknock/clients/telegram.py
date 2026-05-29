"""Telegram Bot API wrapper. Handles draft cards + plain alerts.

The client exposes a deliberately small surface for the bot service:

- :meth:`TelegramClient.send_draft_card` -- post the approval card with
  three inline buttons (Approve / Regenerate / Reject). Returns the
  Telegram message_id so callers can persist it in ``telegram_messages``
  and later edit the card to strike out buttons after an action lands.
- :meth:`TelegramClient.edit_card_to_status` -- replace the card's
  buttons with a status line ("Sent to ...", "Rejected", etc). Failure
  here is non-fatal -- the user already saw the toast.
- :meth:`TelegramClient.answer_callback` -- dismiss the loading spinner
  on the user's tap with a short toast. Also non-fatal on failure.
- :meth:`TelegramClient.send_text` -- plain text fallback for alerts /
  digest messages.

Design choices, project-specific:

- The underlying ``telegram.Bot`` is injected via the ``bot`` field
  rather than constructed in ``__init__``. This makes unit testing
  trivial (stub the four async methods we use) and avoids pulling
  ``python-telegram-bot`` into the cron pipeline's import graph -- it's
  an optional dependency installed only for the bot service.
- ``send_message`` failures are wrapped in :class:`TelegramError`
  because the bot service treats a failed card-send as a hard failure
  (the user never sees the draft, so the poller will retry on the next
  cycle). Edits and callback-answers fail soft -- they're UX polish
  and re-doing them on retry would be noisy.
- HTML parse mode + ``disable_web_page_preview=True`` so the company
  name in the card title doesn't trigger an unfurled link preview
  card.
- The inline-keyboard ``callback_data`` is HMAC-signed via
  :mod:`knockknock.telegram_bot.codec` so a leaked webhook URL can't
  be used to forge an approve for an arbitrary draft id.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from knockknock.exceptions import ExternalServiceError
from knockknock.telegram_bot.codec import CallbackAction, encode_callback_data

log = structlog.get_logger(__name__)

# Preview cap keeps the card under Telegram's 4096-char message limit
# with room to spare for the headers + HTML tags. The body's full text
# lives in the Gmail draft; the card is a glance preview.
_PREVIEW_MAX_CHARS = 400


class TelegramError(ExternalServiceError):
    """Raised when a Telegram API call fails."""


@dataclass(frozen=True, slots=True)
class DraftCardPayload:
    """All the fields needed to render an approval card.

    Built by the poller from a ``EmailDraft`` joined with its
    ``JobApplication`` + ``Company``. Kept as a frozen dataclass (not a
    Pydantic model) because there's no validation logic worth running
    -- the data comes straight from the DB, which is the source of
    truth.
    """

    draft_id: int
    company: str
    role_title: str
    score: int
    resume_variant: str
    to_email: str
    cc_email: str | None
    preview: str  # First ~400 chars of body_text, raw.


class _BotProto(Protocol):
    """Minimal ``telegram.Bot`` surface this wrapper depends on.

    Keeps the type checker honest in tests where ``bot`` is a stub
    dataclass with the same async methods.
    """

    async def send_message(self, **kwargs: Any) -> Any: ...
    async def edit_message_text(self, **kwargs: Any) -> Any: ...
    async def answer_callback_query(self, **kwargs: Any) -> bool: ...


@dataclass(slots=True)
class TelegramClient:
    bot: _BotProto
    admin_chat_id: int
    callback_secret: str

    async def send_draft_card(self, payload: DraftCardPayload) -> int:
        """Post the approval card with three inline buttons.

        Returns the Telegram message_id so the caller can persist it in
        ``telegram_messages`` and later edit the same card.
        """
        text = _format_card_text(payload)
        markup = _build_keyboard(payload.draft_id, self.callback_secret)
        try:
            message = await self.bot.send_message(
                chat_id=self.admin_chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=markup,
                disable_web_page_preview=True,
            )
        except Exception as exc:
            raise TelegramError(f"Telegram send_message failed: {exc}") from exc
        return int(message.message_id)

    async def edit_card_to_status(self, *, message_id: int, status_text: str) -> None:
        """Replace card buttons with a status line after an action lands.

        Failure is logged at WARNING but not raised -- the user already
        saw the toast from ``answer_callback`` and the audit row in
        ``telegram_messages`` already records the action.
        """
        try:
            await self.bot.edit_message_text(
                chat_id=self.admin_chat_id,
                message_id=message_id,
                text=status_text,
                reply_markup=None,
            )
        except Exception as exc:
            log.warning("telegram.edit_failed", message_id=message_id, error=str(exc))

    async def answer_callback(self, *, query_id: str, text: str) -> None:
        """Dismiss the loading spinner on the user's tap with a toast.

        Failure is logged but not raised -- the action has already been
        applied to the DB by the time we get here; the toast is UX.
        """
        try:
            await self.bot.answer_callback_query(callback_query_id=query_id, text=text)
        except Exception as exc:
            log.warning("telegram.answer_failed", query_id=query_id, error=str(exc))

    async def send_text(self, text: str) -> None:
        """Plain-text message to the admin chat. Used for alerts/digest."""
        try:
            await self.bot.send_message(chat_id=self.admin_chat_id, text=text)
        except Exception as exc:
            raise TelegramError(f"Telegram send_text failed: {exc}") from exc


def _format_card_text(p: DraftCardPayload) -> str:
    """Render the card body as HTML for Telegram's HTML parse_mode."""
    preview = p.preview.strip()
    if len(preview) > _PREVIEW_MAX_CHARS:
        # Reserve 3 chars for the trailing ellipsis.
        preview = preview[: _PREVIEW_MAX_CHARS - 3] + "\u2026"
    cc_line = f"<b>Cc:</b> {p.cc_email}\n" if p.cc_email else ""
    return (
        f"<b>{p.company}</b> \u2014 {p.role_title}\n"
        f"<b>Score:</b> {p.score}/10  <b>Resume:</b> {p.resume_variant}\n"
        f"<b>To:</b> {p.to_email}\n"
        f"{cc_line}"
        f"\n<i>{preview}</i>"
    )


def _build_keyboard(draft_id: int, secret: str) -> InlineKeyboardMarkup:
    """Build the 3-button inline keyboard with HMAC-signed callback_data."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "\u2705 Approve",
                    callback_data=encode_callback_data(
                        CallbackAction.APPROVE, draft_id=draft_id, secret=secret
                    ),
                ),
                InlineKeyboardButton(
                    "\u21bb Regenerate",
                    callback_data=encode_callback_data(
                        CallbackAction.REGENERATE, draft_id=draft_id, secret=secret
                    ),
                ),
                InlineKeyboardButton(
                    "\u274c Reject",
                    callback_data=encode_callback_data(
                        CallbackAction.REJECT, draft_id=draft_id, secret=secret
                    ),
                ),
            ]
        ]
    )


def build_bot(token: str) -> Any:
    """Build a real ``telegram.Bot``.

    Imported lazily so unit tests that stub the bot don't need
    ``python-telegram-bot`` resolution at import time. Return type is
    ``Any`` rather than :class:`_BotProto` because the real ``Bot``
    has more-specific (positional-named) signatures than our minimal
    ``**kwargs: Any`` Protocol -- mypy correctly flags the mismatch on
    return, even though the call sites in :class:`TelegramClient`
    only use the keyword forms which both signatures accept.
    """
    from telegram import Bot

    return Bot(token=token)
