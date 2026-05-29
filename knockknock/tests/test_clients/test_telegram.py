"""Tests for the TelegramClient wrapper around python-telegram-bot.

The real ``telegram.Bot`` is stubbed at the method level (``send_message``,
``edit_message_text``, ``answer_callback_query``) so these tests can run
without network or a real bot token. Inline-keyboard markup objects are
the real classes from ``python-telegram-bot`` -- we want to assert on
their structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from knockknock.clients.telegram import (
    DraftCardPayload,
    TelegramClient,
    TelegramError,
)
from knockknock.telegram_bot.codec import CallbackAction


@dataclass
class _StubBot:
    sent: list[dict[str, Any]] = field(default_factory=list)
    edited: list[dict[str, Any]] = field(default_factory=list)
    answered: list[dict[str, Any]] = field(default_factory=list)
    fail_send: bool = False

    async def send_message(self, **kwargs: Any) -> Any:
        if self.fail_send:
            raise RuntimeError("tg send broke")
        self.sent.append(kwargs)
        return _StubMessage(message_id=11111)

    async def edit_message_text(self, **kwargs: Any) -> Any:
        self.edited.append(kwargs)
        return None

    async def answer_callback_query(self, **kwargs: Any) -> bool:
        self.answered.append(kwargs)
        return True


@dataclass
class _StubMessage:
    message_id: int


@pytest.mark.asyncio
async def test_send_draft_card_includes_three_buttons() -> None:
    bot = _StubBot()
    client = TelegramClient(bot=bot, admin_chat_id=42, callback_secret="s")
    payload = DraftCardPayload(
        draft_id=7,
        company="Acme",
        role_title="Senior Backend Engineer",
        score=8,
        resume_variant="backend-distributed",
        to_email="aarav@acme.io",
        cc_email=None,
        preview="Hi Aarav, ...",
    )
    message_id = await client.send_draft_card(payload)
    assert message_id == 11111
    call = bot.sent[0]
    assert call["chat_id"] == 42
    text = call["text"]
    assert "Acme" in text
    assert "8/10" in text
    assert "backend-distributed" in text
    # 3 buttons in one row: approve, regenerate, reject.
    markup = call["reply_markup"]
    rows = markup.inline_keyboard
    actions_in_payload = []
    for row in rows:
        for button in row:
            actions_in_payload.append(button.callback_data.split(":", 1)[0])
    assert set(actions_in_payload) == {
        CallbackAction.APPROVE.value,
        CallbackAction.REGENERATE.value,
        CallbackAction.REJECT.value,
    }


@pytest.mark.asyncio
async def test_send_draft_card_wraps_send_failure() -> None:
    bot = _StubBot(fail_send=True)
    client = TelegramClient(bot=bot, admin_chat_id=42, callback_secret="s")
    payload = DraftCardPayload(
        draft_id=1,
        company="A",
        role_title="r",
        score=5,
        resume_variant="v",
        to_email="t@t.com",
        cc_email=None,
        preview="p",
    )
    with pytest.raises(TelegramError):
        await client.send_draft_card(payload)


@pytest.mark.asyncio
async def test_edit_card_to_status_removes_buttons() -> None:
    bot = _StubBot()
    client = TelegramClient(bot=bot, admin_chat_id=42, callback_secret="s")
    await client.edit_card_to_status(message_id=11, status_text="Sent to aarav@acme.io")
    call = bot.edited[0]
    assert call["chat_id"] == 42
    assert call["message_id"] == 11
    assert "Sent" in call["text"]
    assert call.get("reply_markup") is None


@pytest.mark.asyncio
async def test_send_text_uses_admin_chat_id() -> None:
    bot = _StubBot()
    client = TelegramClient(bot=bot, admin_chat_id=42, callback_secret="s")
    await client.send_text("alert: things on fire")
    assert bot.sent[0]["chat_id"] == 42
    assert bot.sent[0]["text"] == "alert: things on fire"


@pytest.mark.asyncio
async def test_send_draft_card_includes_cc_when_present() -> None:
    """When the payload has a cc_email, the card text must surface it
    -- otherwise the user can't tell at a glance who's being CC'd.
    """
    bot = _StubBot()
    client = TelegramClient(bot=bot, admin_chat_id=42, callback_secret="s")
    payload = DraftCardPayload(
        draft_id=2,
        company="Globex",
        role_title="Staff Eng",
        score=9,
        resume_variant="platform",
        to_email="founder@globex.io",
        cc_email="careers@globex.io",
        preview="...",
    )
    await client.send_draft_card(payload)
    text = bot.sent[0]["text"]
    assert "careers@globex.io" in text


@pytest.mark.asyncio
async def test_send_draft_card_truncates_long_preview() -> None:
    """Preview is capped at 400 chars to keep the card under Telegram's
    4096-char message limit even with long body text.
    """
    bot = _StubBot()
    client = TelegramClient(bot=bot, admin_chat_id=42, callback_secret="s")
    long_preview = "x" * 1000
    payload = DraftCardPayload(
        draft_id=3,
        company="C",
        role_title="r",
        score=5,
        resume_variant="v",
        to_email="t@t.com",
        cc_email=None,
        preview=long_preview,
    )
    await client.send_draft_card(payload)
    text = bot.sent[0]["text"]
    # Preview is wrapped in <i>...</i>; the rendered preview substring
    # should be <= 400 chars and end with an ellipsis.
    assert "x" * 401 not in text
    assert "\u2026" in text  # horizontal ellipsis
