← [Index](00-index.md) · [Prev: phase-08-email-draft.md](phase-08-email-draft.md) · [Next: phase-10-scrapers.md](phase-10-scrapers.md)

## Phase 9: Telegram Bot Service — Approval Flow

**Outcome:** A FastAPI service receives Telegram webhooks at `/telegram/webhook`, verifies the secret header + admin chat id + HMAC-signed `callback_data`, and handles three actions per draft: ✅ Approve (Gmail `drafts.send` → SENT), ❌ Reject (draft → SUPERSEDED, job → USER_REJECTED), ↻ Regenerate (new Pro call, supersede old draft, post fresh card). Outbound notifications (the draft-preview card with inline buttons) are sent from the pipeline after `DraftStage` advances a job to AWAITING_APPROVAL. Every Telegram message in or out is persisted in `telegram_messages`.

The bot is a **separate Cloud Run service** in Phase 12, but in this phase we build it as a standalone FastAPI app testable end-to-end against a fake Telegram API.

### Task 9.1: Callback-data codec (HMAC sign + verify)

**Files:**
- Create: `src/knockknock/telegram_bot/__init__.py`
- Create: `src/knockknock/telegram_bot/codec.py`
- Create: `tests/test_telegram_bot/__init__.py`
- Create: `tests/test_telegram_bot/test_codec.py`

Telegram caps `callback_data` to 64 bytes. We encode `(action, draft_id)` plus an HMAC tag so an attacker who somehow reaches the webhook can't forge an "approve" for an arbitrary draft id. Format:

```
<action>:<draft_id>:<hmac_hex16>
```

`hmac_hex16` is the first 16 hex chars (64 bits) of `HMAC-SHA256(secret, f"{action}:{draft_id}")`. 64 bits is plenty for a single-user, single-bot system.

- [ ] **Step 1: Write failing codec tests**

Create `tests/test_telegram_bot/__init__.py` as empty file.

Create `tests/test_telegram_bot/test_codec.py`:

```python
from __future__ import annotations

import pytest

from knockknock.telegram_bot.codec import (
    CallbackAction,
    decode_callback_data,
    encode_callback_data,
)


def test_encode_decode_roundtrip() -> None:
    payload = encode_callback_data(CallbackAction.APPROVE, draft_id=42, secret="s3cret")
    action, draft_id = decode_callback_data(payload, secret="s3cret")
    assert action is CallbackAction.APPROVE
    assert draft_id == 42


def test_encode_fits_in_64_bytes() -> None:
    payload = encode_callback_data(
        CallbackAction.REGENERATE, draft_id=9_999_999_999, secret="s3cret"
    )
    assert len(payload.encode("utf-8")) <= 64


def test_decode_rejects_wrong_secret() -> None:
    payload = encode_callback_data(CallbackAction.REJECT, draft_id=1, secret="real")
    with pytest.raises(ValueError, match="signature"):
        decode_callback_data(payload, secret="fake")


def test_decode_rejects_tampered_payload() -> None:
    payload = encode_callback_data(CallbackAction.APPROVE, draft_id=1, secret="s")
    # Flip approve → reject; signature is over the original action.
    tampered = payload.replace("approve:", "reject:", 1)
    with pytest.raises(ValueError, match="signature"):
        decode_callback_data(tampered, secret="s")


def test_decode_rejects_malformed_input() -> None:
    with pytest.raises(ValueError):
        decode_callback_data("garbage", secret="s")


def test_decode_rejects_unknown_action() -> None:
    with pytest.raises(ValueError, match="action"):
        decode_callback_data("delete:1:abcd1234abcd1234", secret="s")
```

- [ ] **Step 2: Confirm failure**

```bash
uv run pytest tests/test_telegram_bot/test_codec.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement the codec**

Create `src/knockknock/telegram_bot/__init__.py` as empty file.

Create `src/knockknock/telegram_bot/codec.py`:

```python
"""HMAC-signed callback_data codec for Telegram inline-button actions."""

from __future__ import annotations

import hmac
from enum import StrEnum
from hashlib import sha256

_SIGNATURE_HEX_LEN = 16  # 64 bits — sufficient for single-user, single-bot threat model.


class CallbackAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REGENERATE = "regenerate"


def _sign(action: CallbackAction, draft_id: int, secret: str) -> str:
    message = f"{action.value}:{draft_id}".encode("utf-8")
    mac = hmac.new(secret.encode("utf-8"), message, sha256).hexdigest()
    return mac[:_SIGNATURE_HEX_LEN]


def encode_callback_data(action: CallbackAction, *, draft_id: int, secret: str) -> str:
    """Pack `(action, draft_id, hmac)` into a colon-delimited string ≤64 bytes."""
    signature = _sign(action, draft_id, secret)
    return f"{action.value}:{draft_id}:{signature}"


def decode_callback_data(raw: str, *, secret: str) -> tuple[CallbackAction, int]:
    """Verify HMAC and return `(action, draft_id)`. Raise ValueError on any tamper."""
    if not raw or raw.count(":") != 2:
        raise ValueError(f"Malformed callback_data: {raw!r}")
    action_str, draft_id_str, signature = raw.split(":", 2)
    try:
        action = CallbackAction(action_str)
    except ValueError as exc:
        raise ValueError(f"Unknown callback action: {action_str!r}") from exc
    try:
        draft_id = int(draft_id_str)
    except ValueError as exc:
        raise ValueError(f"Non-integer draft id in callback_data: {draft_id_str!r}") from exc
    expected = _sign(action, draft_id, secret)
    if not hmac.compare_digest(expected, signature):
        raise ValueError("callback_data signature mismatch")
    return action, draft_id
```

- [ ] **Step 4: Run tests until green; commit**

```bash
uv run pytest tests/test_telegram_bot/test_codec.py -v
uv run ruff check src tests
uv run mypy
git add src/knockknock/telegram_bot/__init__.py src/knockknock/telegram_bot/codec.py tests/test_telegram_bot
git commit -m "feat(telegram): hmac-signed callback_data codec"
```

### Task 9.2: Telegram client wrapper

**Files:**
- Create: `src/knockknock/clients/telegram.py`
- Create: `tests/test_clients/test_telegram.py`

We use `python-telegram-bot` v21 for the typed Bot API client. For tests we stub the bot at the method level (`send_message`, `answer_callback_query`, `edit_message_reply_markup`).

The client exposes:
- `send_draft_card(draft, prefs, secret) → message_id`: posts the inline-button card.
- `edit_card_to_status(chat_id, message_id, text)`: replaces the buttons with a status line after an action lands.
- `answer_callback(query_id, text)`: dismisses the loading spinner with a toast.
- `send_text(chat_id, text)`: plain text fallback for alerts / digest.

- [ ] **Step 1: Write failing tests**

Create `tests/test_clients/test_telegram.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from knockknock.clients.telegram import (
    DraftCardPayload,
    TelegramClient,
    TelegramError,
)
from knockknock.telegram_bot.codec import CallbackAction


@dataclass
class _StubBot:
    sent: list[dict] = field(default_factory=list)
    edited: list[dict] = field(default_factory=list)
    answered: list[dict] = field(default_factory=list)
    fail_send: bool = False

    async def send_message(self, **kwargs):  # type: ignore[no-untyped-def]
        if self.fail_send:
            raise RuntimeError("tg send broke")
        self.sent.append(kwargs)
        return _StubMessage(message_id=11111)

    async def edit_message_text(self, **kwargs):  # type: ignore[no-untyped-def]
        self.edited.append(kwargs)
        return None

    async def answer_callback_query(self, **kwargs):  # type: ignore[no-untyped-def]
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
    await client.edit_card_to_status(message_id=11, status_text="✅ Sent to aarav@acme.io")
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
```

- [ ] **Step 2: Confirm failure**

```bash
uv run pytest tests/test_clients/test_telegram.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement the client**

Create `src/knockknock/clients/telegram.py`:

```python
"""Telegram Bot API wrapper. Handles draft cards + plain alerts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from knockknock.exceptions import ExternalServiceError
from knockknock.telegram_bot.codec import CallbackAction, encode_callback_data

log = structlog.get_logger(__name__)


class TelegramError(ExternalServiceError):
    """Raised when a Telegram API call fails."""


@dataclass(frozen=True, slots=True)
class DraftCardPayload:
    draft_id: int
    company: str
    role_title: str
    score: int
    resume_variant: str
    to_email: str
    cc_email: str | None
    preview: str  # first ~400 chars of body_text


class _BotProto(Protocol):
    async def send_message(self, **kwargs: Any) -> Any: ...
    async def edit_message_text(self, **kwargs: Any) -> Any: ...
    async def answer_callback_query(self, **kwargs: Any) -> bool: ...


@dataclass(slots=True)
class TelegramClient:
    bot: _BotProto
    admin_chat_id: int
    callback_secret: str

    async def send_draft_card(self, payload: DraftCardPayload) -> int:
        """Post the approval card with three inline buttons. Return tg message id."""
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
        except Exception as exc:  # noqa: BLE001 - telegram raises many types.
            raise TelegramError(f"Telegram send_message failed: {exc}") from exc
        return int(message.message_id)

    async def edit_card_to_status(self, *, message_id: int, status_text: str) -> None:
        """Replace card buttons with a status line after an action lands."""
        try:
            await self.bot.edit_message_text(
                chat_id=self.admin_chat_id,
                message_id=message_id,
                text=status_text,
                reply_markup=None,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("telegram.edit_failed", message_id=message_id, error=str(exc))
            # Edits failing is non-fatal — user already saw the toast.

    async def answer_callback(self, *, query_id: str, text: str) -> None:
        try:
            await self.bot.answer_callback_query(callback_query_id=query_id, text=text)
        except Exception as exc:  # noqa: BLE001
            log.warning("telegram.answer_failed", query_id=query_id, error=str(exc))

    async def send_text(self, text: str) -> None:
        try:
            await self.bot.send_message(chat_id=self.admin_chat_id, text=text)
        except Exception as exc:  # noqa: BLE001
            raise TelegramError(f"Telegram send_text failed: {exc}") from exc


def _format_card_text(p: DraftCardPayload) -> str:
    preview = p.preview.strip()
    if len(preview) > 400:
        preview = preview[:397] + "…"
    cc_line = f"<b>Cc:</b> {p.cc_email}\n" if p.cc_email else ""
    return (
        f"<b>{p.company}</b> — {p.role_title}\n"
        f"<b>Score:</b> {p.score}/10  <b>Resume:</b> {p.resume_variant}\n"
        f"<b>To:</b> {p.to_email}\n"
        f"{cc_line}"
        f"\n<i>{preview}</i>"
    )


def _build_keyboard(draft_id: int, secret: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Approve",
                    callback_data=encode_callback_data(
                        CallbackAction.APPROVE, draft_id=draft_id, secret=secret
                    ),
                ),
                InlineKeyboardButton(
                    "↻ Regenerate",
                    callback_data=encode_callback_data(
                        CallbackAction.REGENERATE, draft_id=draft_id, secret=secret
                    ),
                ),
                InlineKeyboardButton(
                    "❌ Reject",
                    callback_data=encode_callback_data(
                        CallbackAction.REJECT, draft_id=draft_id, secret=secret
                    ),
                ),
            ]
        ]
    )


def build_bot(token: str) -> _BotProto:
    """Build a real `telegram.Bot`. Imported here so unit tests don't need it."""
    from telegram import Bot

    return Bot(token=token)
```

- [ ] **Step 4: Run tests until green; commit**

```bash
uv run pytest tests/test_clients/test_telegram.py -v
uv run ruff check src tests
uv run mypy
git add src/knockknock/clients/telegram.py tests/test_clients/test_telegram.py
git commit -m "feat(clients): telegram bot wrapper with draft card builder"
```

### Task 9.3: Notify stage — post card after Draft stage advances a job

**Files:**
- Create: `src/knockknock/pipeline/notify.py`
- Create: `tests/test_pipeline/test_notify_stage.py`

This is a small pipeline stage that runs after `DraftStage`. It queries `email_drafts` where `state=DRAFT_CREATED` AND no outgoing Telegram `DRAFT_PREVIEW` row exists yet, posts the card via `TelegramClient.send_draft_card`, and records the resulting Telegram message in `telegram_messages` so we don't double-post on a re-run.

- [ ] **Step 1: Write failing tests**

Create `tests/test_pipeline/test_notify_stage.py`:

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest
from sqlmodel import Session

from knockknock.clients.telegram import DraftCardPayload
from knockknock.db.enums import (
    DraftState,
    JobStatus,
    PhonebookSource,
    TelegramDirection,
    TelegramKind,
)
from knockknock.db.models import (
    Company,
    EmailDraft,
    JobApplication,
    PhonebookEntry,
    TelegramMessage,
)
from knockknock.pipeline.notify import NotifyStage


@dataclass
class _StubTg:
    calls: list[DraftCardPayload] = field(default_factory=list)
    next_message_id: int = 1000

    async def send_draft_card(self, payload: DraftCardPayload) -> int:
        self.calls.append(payload)
        self.next_message_id += 1
        return self.next_message_id


def _make_awaiting_with_draft(db_session: Session, *, source_job_id: str = "j-1") -> EmailDraft:
    company = Company(name="Acme", domain="acme.io")
    db_session.add(company)
    db_session.flush()
    db_session.add(
        PhonebookEntry(
            company_id=company.id,
            careers_email="careers@acme.io",
            source=PhonebookSource.SEED,
        )
    )
    job = JobApplication(
        company_id=company.id,
        title="Backend Engineer",
        location="Bangalore",
        description="x",
        apply_url="https://acme.io/jobs/1",
        source="HN_WHO_IS_HIRING",
        source_job_id=source_job_id,
        status=JobStatus.AWAITING_APPROVAL,
        score=8,
        resume_variant_key="backend-distributed",
        discovered_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.flush()
    phonebook = db_session.query(PhonebookEntry).filter_by(company_id=company.id).one()
    draft = EmailDraft(
        job_application_id=job.id,
        phonebook_id=phonebook.id,
        to_email="careers@acme.io",
        subject="Hi",
        body_text="Hi team,\n\nI'd love to talk.\n\nBest,\nNishant\nme@example.com",
        body_html="<p>Hi team,</p>",
        resume_variant_key="backend-distributed",
        gmail_draft_id="draft_x",
        state=DraftState.DRAFT_CREATED,
    )
    db_session.add(draft)
    db_session.flush()
    return draft


def test_notify_sends_card_once_per_draft(db_session: Session) -> None:
    draft = _make_awaiting_with_draft(db_session)
    tg = _StubTg()
    stage = NotifyStage(telegram=tg)
    result = asyncio.run(stage.run_async(session=db_session, run_id=1))
    assert result.advanced == 1
    assert len(tg.calls) == 1
    msg = db_session.query(TelegramMessage).filter_by(email_draft_id=draft.id).one()
    assert msg.direction == TelegramDirection.OUTGOING
    assert msg.kind == TelegramKind.DRAFT_PREVIEW
    assert msg.telegram_message_id is not None


def test_notify_is_idempotent(db_session: Session) -> None:
    draft = _make_awaiting_with_draft(db_session)
    tg = _StubTg()
    stage = NotifyStage(telegram=tg)
    asyncio.run(stage.run_async(session=db_session, run_id=1))
    asyncio.run(stage.run_async(session=db_session, run_id=2))
    # Second run posts nothing new.
    assert len(tg.calls) == 1
    msgs = db_session.query(TelegramMessage).filter_by(email_draft_id=draft.id).all()
    assert len(msgs) == 1


def test_notify_truncates_preview() -> None:
    # Pure-function check — exercised in test_clients/test_telegram via _format_card_text.
    # Re-asserted here to make the contract explicit.
    long = "x" * 1000
    short = long[:397] + "…"
    assert len(short) == 398
```

- [ ] **Step 2: Confirm failure**

```bash
uv run pytest tests/test_pipeline/test_notify_stage.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement the stage**

Create `src/knockknock/pipeline/notify.py`:

```python
"""Notify stage: post Telegram draft card for each AWAITING_APPROVAL job."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import structlog
from sqlmodel import Session, select

from knockknock.clients.telegram import DraftCardPayload, TelegramError
from knockknock.db.enums import (
    DraftState,
    PipelineStage,
    TelegramDirection,
    TelegramKind,
)
from knockknock.db.models import (
    Company,
    EmailDraft,
    JobApplication,
    TelegramMessage,
)
from knockknock.pipeline.stage import StageResult

log = structlog.get_logger(__name__)


class _TgLike(Protocol):
    async def send_draft_card(self, payload: DraftCardPayload) -> int: ...


@dataclass(slots=True)
class NotifyStage:
    """Post a Telegram approval card for any DRAFT_CREATED with no preview yet."""

    telegram: _TgLike
    name: str = "notify"

    async def run_async(self, *, session: Session, run_id: int) -> StageResult:
        stmt = (
            select(EmailDraft, JobApplication, Company)
            .join(JobApplication, JobApplication.id == EmailDraft.job_application_id)
            .join(Company, Company.id == JobApplication.company_id)
            .where(EmailDraft.state == DraftState.DRAFT_CREATED)
            .order_by(JobApplication.score.desc(), JobApplication.discovered_at.asc())
        )
        advanced = 0
        errors = 0
        for draft, job, company in session.exec(stmt).all():
            already = session.exec(
                select(TelegramMessage)
                .where(TelegramMessage.email_draft_id == draft.id)
                .where(TelegramMessage.direction == TelegramDirection.OUTGOING)
                .where(TelegramMessage.kind == TelegramKind.DRAFT_PREVIEW)
            ).first()
            if already is not None:
                continue
            payload = DraftCardPayload(
                draft_id=draft.id,
                company=company.name,
                role_title=job.title,
                score=job.score or 0,
                resume_variant=draft.resume_variant_key,
                to_email=draft.to_email,
                cc_email=draft.cc_email,
                preview=draft.body_text,
            )
            try:
                tg_message_id = await self.telegram.send_draft_card(payload)
            except TelegramError as exc:
                errors += 1
                log.warning("notify.send_failed", draft_id=draft.id, error=str(exc))
                continue

            session.add(
                TelegramMessage(
                    job_application_id=job.id,
                    email_draft_id=draft.id,
                    direction=TelegramDirection.OUTGOING,
                    kind=TelegramKind.DRAFT_PREVIEW,
                    telegram_message_id=tg_message_id,
                    body=payload.preview[:1000],
                )
            )
            session.flush()
            advanced += 1
        log.info("notify.summary", advanced=advanced, errors=errors)
        return StageResult(
            stage=PipelineStage.DRAFT,  # uses DRAFT stage for audit; no dedicated enum.
            advanced=advanced,
            rejected=0,
            errors=errors,
        )
```

> **Schema note on `TelegramMessage.body`:** spec table includes a generic text column for the message body. If your Phase 1 column is named differently, adjust the field reference. Otherwise the rest of the columns (`job_application_id`, `email_draft_id`, `direction`, `kind`, `telegram_message_id`) are exactly the spec.

> **Enum reuse note:** we reuse `PipelineStage.DRAFT` for the notify summary (it's the same logical step from a pipeline-run-counts perspective). The spec's `pipeline_stage` enum has a `TELEGRAM_RESPONSE` variant — that one is used by the webhook handler in 9.4 for the *user's* approve/reject events, not by this stage.

- [ ] **Step 4: Run tests until green; commit**

```bash
uv run pytest tests/test_pipeline/test_notify_stage.py -v
uv run ruff check src tests
uv run mypy
git add src/knockknock/pipeline/notify.py tests/test_pipeline/test_notify_stage.py
git commit -m "feat(pipeline): notify stage posts telegram approval cards"
```

### Task 9.4: Webhook handler — verify, dispatch, persist

**Files:**
- Create: `src/knockknock/telegram_bot/handlers.py`
- Create: `tests/test_telegram_bot/test_handlers.py`

The handler accepts a parsed Telegram `Update` (just a dict in our world; we don't need PTB's Dispatcher) and performs the action. Verification responsibility split:

- The FastAPI app (task 9.5) verifies the `X-Telegram-Bot-Api-Secret-Token` header — that's transport security.
- `handle_callback` here verifies the **admin chat id** and the **HMAC signature** of `callback_data`.

Actions:

- **APPROVE** → load draft + job → call `GmailClient.send_draft(gmail_draft_id)` → update `email_drafts.state=SENT`, `email_drafts.gmail_message_id`, `email_drafts.sent_at`, `job_applications.status=APPROVED → SENT`. Write a `TELEGRAM_RESPONSE` audit event.
- **REJECT** → set `email_drafts.state=SUPERSEDED`, `job_applications.status=USER_REJECTED`, `rejection_reason=USER_REJECTED`. Audit event.
- **REGENERATE** → set old `email_drafts.state=SUPERSEDED`, job back to TAILORED. The next pipeline run will produce a fresh draft via the existing `DraftStage` (now with `is_regeneration_of=<old draft id>` once we pass that hint — see note below). Audit event.

- [ ] **Step 1: Write failing tests**

Create `tests/test_telegram_bot/test_handlers.py`:

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest
from sqlmodel import Session

from knockknock.db.enums import (
    DraftState,
    JobStatus,
    PhonebookSource,
    RejectionReason,
    TelegramDirection,
    TelegramKind,
)
from knockknock.db.models import (
    Company,
    EmailDraft,
    JobApplication,
    PhonebookEntry,
    TelegramMessage,
)
from knockknock.exceptions import ExternalServiceError
from knockknock.telegram_bot.codec import CallbackAction, encode_callback_data
from knockknock.telegram_bot.handlers import (
    HandlerContext,
    HandlerResult,
    UnauthorizedError,
    handle_callback,
)


SECRET = "callback-secret"


@dataclass
class _StubGmail:
    send_calls: list[str] = field(default_factory=list)
    fail: bool = False

    def send_draft(self, draft_id: str) -> str:
        if self.fail:
            raise ExternalServiceError("gmail send down")
        self.send_calls.append(draft_id)
        return f"msg_{draft_id}"


@dataclass
class _StubTg:
    edits: list[dict] = field(default_factory=list)
    answers: list[dict] = field(default_factory=list)

    async def edit_card_to_status(self, *, message_id: int, status_text: str) -> None:
        self.edits.append({"message_id": message_id, "status_text": status_text})

    async def answer_callback(self, *, query_id: str, text: str) -> None:
        self.answers.append({"query_id": query_id, "text": text})


def _seed_draft(db_session: Session, *, source_job_id: str = "j-1") -> EmailDraft:
    company = Company(name="Acme", domain="acme.io")
    db_session.add(company)
    db_session.flush()
    db_session.add(
        PhonebookEntry(
            company_id=company.id,
            careers_email="careers@acme.io",
            source=PhonebookSource.SEED,
        )
    )
    job = JobApplication(
        company_id=company.id,
        title="Backend",
        location="Bangalore",
        description="x",
        apply_url="https://acme.io/jobs/1",
        source="HN_WHO_IS_HIRING",
        source_job_id=source_job_id,
        status=JobStatus.AWAITING_APPROVAL,
        score=8,
        resume_variant_key="backend-distributed",
        discovered_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.flush()
    phonebook = db_session.query(PhonebookEntry).filter_by(company_id=company.id).one()
    draft = EmailDraft(
        job_application_id=job.id,
        phonebook_id=phonebook.id,
        to_email="careers@acme.io",
        subject="Hi",
        body_text="Hi.",
        body_html="<p>Hi.</p>",
        resume_variant_key="backend-distributed",
        gmail_draft_id="gd_1",
        state=DraftState.DRAFT_CREATED,
    )
    db_session.add(draft)
    db_session.flush()
    return draft


def _make_update(action: CallbackAction, draft_id: int, *, chat_id: int = 42) -> dict:
    data = encode_callback_data(action, draft_id=draft_id, secret=SECRET)
    return {
        "update_id": 100,
        "callback_query": {
            "id": "cb-1",
            "from": {"id": chat_id, "is_bot": False, "first_name": "User"},
            "message": {"message_id": 555, "chat": {"id": chat_id}},
            "data": data,
        },
    }


def _context(db_session: Session, gmail: _StubGmail, tg: _StubTg) -> HandlerContext:
    return HandlerContext(
        session=db_session,
        gmail=gmail,
        telegram=tg,
        admin_chat_id=42,
        callback_secret=SECRET,
    )


def test_approve_sends_and_marks_sent(db_session: Session) -> None:
    draft = _seed_draft(db_session)
    gmail = _StubGmail()
    tg = _StubTg()
    ctx = _context(db_session, gmail, tg)
    update = _make_update(CallbackAction.APPROVE, draft.id)
    result = asyncio.run(handle_callback(update, ctx))
    db_session.refresh(draft)
    assert result.action == CallbackAction.APPROVE
    assert draft.state == DraftState.SENT
    assert draft.gmail_message_id == "msg_gd_1"
    assert draft.sent_at is not None
    job = db_session.query(JobApplication).filter_by(id=draft.job_application_id).one()
    assert job.status == JobStatus.SENT
    assert gmail.send_calls == ["gd_1"]
    assert tg.edits[0]["status_text"].startswith("✅")


def test_reject_marks_superseded_and_user_rejected(db_session: Session) -> None:
    draft = _seed_draft(db_session, source_job_id="j-reject")
    gmail = _StubGmail()
    tg = _StubTg()
    ctx = _context(db_session, gmail, tg)
    update = _make_update(CallbackAction.REJECT, draft.id)
    asyncio.run(handle_callback(update, ctx))
    db_session.refresh(draft)
    assert draft.state == DraftState.SUPERSEDED
    job = db_session.query(JobApplication).filter_by(id=draft.job_application_id).one()
    assert job.status == JobStatus.USER_REJECTED
    assert job.rejection_reason == RejectionReason.USER_REJECTED
    assert gmail.send_calls == []


def test_regenerate_moves_job_back_to_tailored(db_session: Session) -> None:
    draft = _seed_draft(db_session, source_job_id="j-regen")
    gmail = _StubGmail()
    tg = _StubTg()
    ctx = _context(db_session, gmail, tg)
    update = _make_update(CallbackAction.REGENERATE, draft.id)
    asyncio.run(handle_callback(update, ctx))
    db_session.refresh(draft)
    assert draft.state == DraftState.SUPERSEDED
    job = db_session.query(JobApplication).filter_by(id=draft.job_application_id).one()
    assert job.status == JobStatus.TAILORED  # next run will redraft
    assert gmail.send_calls == []


def test_handler_rejects_wrong_chat_id(db_session: Session) -> None:
    draft = _seed_draft(db_session, source_job_id="j-wrong")
    gmail = _StubGmail()
    tg = _StubTg()
    ctx = _context(db_session, gmail, tg)
    update = _make_update(CallbackAction.APPROVE, draft.id, chat_id=999)
    with pytest.raises(UnauthorizedError, match="chat"):
        asyncio.run(handle_callback(update, ctx))
    db_session.refresh(draft)
    assert draft.state == DraftState.DRAFT_CREATED  # untouched


def test_handler_rejects_bad_hmac(db_session: Session) -> None:
    draft = _seed_draft(db_session, source_job_id="j-hmac")
    gmail = _StubGmail()
    tg = _StubTg()
    ctx = _context(db_session, gmail, tg)
    update = _make_update(CallbackAction.APPROVE, draft.id)
    # Tamper with signature.
    update["callback_query"]["data"] = update["callback_query"]["data"][:-1] + "0"
    with pytest.raises(UnauthorizedError, match="signature"):
        asyncio.run(handle_callback(update, ctx))


def test_handler_records_incoming_telegram_message(db_session: Session) -> None:
    draft = _seed_draft(db_session, source_job_id="j-tm")
    gmail = _StubGmail()
    tg = _StubTg()
    ctx = _context(db_session, gmail, tg)
    asyncio.run(handle_callback(_make_update(CallbackAction.APPROVE, draft.id), ctx))
    incoming = (
        db_session.query(TelegramMessage)
        .filter_by(email_draft_id=draft.id, direction=TelegramDirection.INCOMING)
        .one()
    )
    assert incoming.kind == TelegramKind.APPROVAL


def test_handler_idempotent_for_already_sent_draft(db_session: Session) -> None:
    draft = _seed_draft(db_session, source_job_id="j-idem")
    draft.state = DraftState.SENT
    db_session.flush()
    gmail = _StubGmail()
    tg = _StubTg()
    ctx = _context(db_session, gmail, tg)
    result = asyncio.run(handle_callback(_make_update(CallbackAction.APPROVE, draft.id), ctx))
    assert result.idempotent_skip is True
    assert gmail.send_calls == []  # no re-send
```

- [ ] **Step 2: Confirm failure**

```bash
uv run pytest tests/test_telegram_bot/test_handlers.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement the handler**

Create `src/knockknock/telegram_bot/handlers.py`:

```python
"""Webhook dispatch logic for approve / reject / regenerate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

import structlog
from sqlmodel import Session

from knockknock.db.enums import (
    DraftState,
    JobStatus,
    PipelineStage,
    RejectionReason,
    TelegramDirection,
    TelegramKind,
)
from knockknock.db.models import (
    EmailDraft,
    JobApplication,
    JobApplicationEvent,
    TelegramMessage,
)
from knockknock.exceptions import ExternalServiceError
from knockknock.telegram_bot.codec import CallbackAction, decode_callback_data

log = structlog.get_logger(__name__)


class UnauthorizedError(Exception):
    """Raised when chat id mismatches or HMAC verification fails."""


class _GmailLike(Protocol):
    def send_draft(self, draft_id: str) -> str: ...


class _TgLike(Protocol):
    async def edit_card_to_status(self, *, message_id: int, status_text: str) -> None: ...
    async def answer_callback(self, *, query_id: str, text: str) -> None: ...


@dataclass(slots=True)
class HandlerContext:
    session: Session
    gmail: _GmailLike
    telegram: _TgLike
    admin_chat_id: int
    callback_secret: str


@dataclass(frozen=True, slots=True)
class HandlerResult:
    action: CallbackAction
    draft_id: int
    idempotent_skip: bool = False


async def handle_callback(update: dict, ctx: HandlerContext) -> HandlerResult:
    """Validate, dispatch, persist. Caller commits the session."""
    cq = update.get("callback_query") or {}
    chat = ((cq.get("message") or {}).get("chat") or {})
    chat_id = int(chat.get("id") or 0)
    if chat_id != ctx.admin_chat_id:
        raise UnauthorizedError(f"chat id {chat_id} not admin {ctx.admin_chat_id}")

    data = cq.get("data") or ""
    try:
        action, draft_id = decode_callback_data(data, secret=ctx.callback_secret)
    except ValueError as exc:
        raise UnauthorizedError(f"callback signature invalid: {exc}") from exc

    query_id = str(cq.get("id") or "")
    tg_message_id = int((cq.get("message") or {}).get("message_id") or 0)

    draft = ctx.session.get(EmailDraft, draft_id)
    if draft is None:
        # Record the incoming attempt and bail.
        _persist_incoming(ctx.session, draft_id=None, action=action, raw=data)
        await ctx.telegram.answer_callback(query_id=query_id, text="Draft no longer exists.")
        return HandlerResult(action=action, draft_id=draft_id, idempotent_skip=True)

    # Idempotency: if the draft is already terminal for this action, no-op.
    if action is CallbackAction.APPROVE and draft.state == DraftState.SENT:
        await ctx.telegram.answer_callback(query_id=query_id, text="Already sent.")
        _persist_incoming(ctx.session, draft_id=draft.id, action=action, raw=data)
        return HandlerResult(action=action, draft_id=draft_id, idempotent_skip=True)
    if action in (CallbackAction.REJECT, CallbackAction.REGENERATE) and draft.state == DraftState.SUPERSEDED:
        await ctx.telegram.answer_callback(query_id=query_id, text="Already handled.")
        _persist_incoming(ctx.session, draft_id=draft.id, action=action, raw=data)
        return HandlerResult(action=action, draft_id=draft_id, idempotent_skip=True)

    job = ctx.session.get(JobApplication, draft.job_application_id)
    if job is None:
        raise RuntimeError(f"Draft {draft.id} references missing job {draft.job_application_id}")

    if action is CallbackAction.APPROVE:
        await _do_approve(ctx, draft=draft, job=job, query_id=query_id, tg_message_id=tg_message_id, raw=data)
    elif action is CallbackAction.REJECT:
        await _do_reject(ctx, draft=draft, job=job, query_id=query_id, tg_message_id=tg_message_id, raw=data)
    else:
        await _do_regenerate(ctx, draft=draft, job=job, query_id=query_id, tg_message_id=tg_message_id, raw=data)

    return HandlerResult(action=action, draft_id=draft.id)


async def _do_approve(
    ctx: HandlerContext,
    *,
    draft: EmailDraft,
    job: JobApplication,
    query_id: str,
    tg_message_id: int,
    raw: str,
) -> None:
    try:
        message_id = ctx.gmail.send_draft(draft.gmail_draft_id or "")
    except ExternalServiceError as exc:
        log.warning("approve.gmail_send_failed", draft_id=draft.id, error=str(exc))
        draft.state = DraftState.FAILED
        draft.failure_reason = f"gmail send failed: {exc}"
        ctx.session.flush()
        await ctx.telegram.answer_callback(query_id=query_id, text=f"Send failed: {exc}")
        _persist_incoming(ctx.session, draft_id=draft.id, action=CallbackAction.APPROVE, raw=raw)
        return

    draft.state = DraftState.SENT
    draft.gmail_message_id = message_id
    draft.sent_at = datetime.now(timezone.utc)
    from_status = job.status
    job.status = JobStatus.SENT
    ctx.session.add(
        JobApplicationEvent(
            job_application_id=job.id,
            stage=PipelineStage.TELEGRAM_RESPONSE,
            from_status=from_status,
            to_status=JobStatus.SENT,
            detail=f"approved via telegram, msg_id={message_id}",
        )
    )
    ctx.session.flush()
    await ctx.telegram.edit_card_to_status(
        message_id=tg_message_id,
        status_text=f"✅ Sent to {draft.to_email}",
    )
    await ctx.telegram.answer_callback(query_id=query_id, text="Sent.")
    _persist_incoming(ctx.session, draft_id=draft.id, action=CallbackAction.APPROVE, raw=raw)


async def _do_reject(
    ctx: HandlerContext,
    *,
    draft: EmailDraft,
    job: JobApplication,
    query_id: str,
    tg_message_id: int,
    raw: str,
) -> None:
    draft.state = DraftState.SUPERSEDED
    from_status = job.status
    job.status = JobStatus.USER_REJECTED
    job.rejection_reason = RejectionReason.USER_REJECTED
    ctx.session.add(
        JobApplicationEvent(
            job_application_id=job.id,
            stage=PipelineStage.TELEGRAM_RESPONSE,
            from_status=from_status,
            to_status=JobStatus.USER_REJECTED,
            rejection_reason=RejectionReason.USER_REJECTED,
            detail="rejected via telegram",
        )
    )
    ctx.session.flush()
    await ctx.telegram.edit_card_to_status(
        message_id=tg_message_id,
        status_text="❌ Rejected.",
    )
    await ctx.telegram.answer_callback(query_id=query_id, text="Rejected.")
    _persist_incoming(ctx.session, draft_id=draft.id, action=CallbackAction.REJECT, raw=raw)


async def _do_regenerate(
    ctx: HandlerContext,
    *,
    draft: EmailDraft,
    job: JobApplication,
    query_id: str,
    tg_message_id: int,
    raw: str,
) -> None:
    draft.state = DraftState.SUPERSEDED
    from_status = job.status
    job.status = JobStatus.TAILORED  # next pipeline run will redraft.
    job.last_error = None
    ctx.session.add(
        JobApplicationEvent(
            job_application_id=job.id,
            stage=PipelineStage.TELEGRAM_RESPONSE,
            from_status=from_status,
            to_status=JobStatus.TAILORED,
            detail=f"regenerate requested via telegram (superseded draft {draft.id})",
        )
    )
    ctx.session.flush()
    await ctx.telegram.edit_card_to_status(
        message_id=tg_message_id,
        status_text="↻ Regenerating on next run…",
    )
    await ctx.telegram.answer_callback(query_id=query_id, text="Will regenerate.")
    _persist_incoming(ctx.session, draft_id=draft.id, action=CallbackAction.REGENERATE, raw=raw)


def _persist_incoming(
    session: Session,
    *,
    draft_id: int | None,
    action: CallbackAction,
    raw: str,
) -> None:
    kind_map = {
        CallbackAction.APPROVE: TelegramKind.APPROVAL,
        CallbackAction.REJECT: TelegramKind.REJECTION,
        CallbackAction.REGENERATE: TelegramKind.COMMAND,
    }
    session.add(
        TelegramMessage(
            email_draft_id=draft_id,
            direction=TelegramDirection.INCOMING,
            kind=kind_map[action],
            body=raw[:200],
        )
    )
    session.flush()
```

> **Job-status note for regenerate:** we move the job back to `TAILORED` so the existing `DraftStage` re-processes it on the next pipeline run. We do NOT inline-call Pro here because the bot is a separate service with its own quota visibility — the spec mentions inline regeneration but for v1 we deliberately defer to the batch pipeline. Mark this as a tracked future enhancement.

- [ ] **Step 4: Run tests until green; commit**

```bash
uv run pytest tests/test_telegram_bot/test_handlers.py -v
uv run ruff check src tests
uv run mypy
git add src/knockknock/telegram_bot/handlers.py tests/test_telegram_bot/test_handlers.py
git commit -m "feat(telegram): callback handler with hmac + idempotency"
```

### Task 9.5: FastAPI webhook app

**Files:**
- Create: `src/knockknock/telegram_bot/app.py`
- Create: `tests/test_telegram_bot/test_app.py`

The app exposes one endpoint: `POST /telegram/webhook`. It:

1. Verifies `X-Telegram-Bot-Api-Secret-Token` header matches the configured webhook secret. 401 on mismatch.
2. Parses the JSON body as an Update.
3. Opens a DB session, builds a `HandlerContext`, calls `handle_callback`.
4. Returns 200 with `{"ok": true}` always (even on UnauthorizedError, so Telegram doesn't retry forever).
5. Logs every request structurally — `update_id`, `chat_id`, `action`, `duration_ms`.

Health check at `GET /healthz` returns 200 always.

- [ ] **Step 1: Write failing tests using FastAPI's `TestClient`**

Create `tests/test_telegram_bot/test_app.py`:

```python
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from knockknock.db.enums import (
    DraftState,
    JobStatus,
    PhonebookSource,
)
from knockknock.db.models import (
    Company,
    EmailDraft,
    JobApplication,
    PhonebookEntry,
)
from knockknock.telegram_bot.app import AppConfig, build_app
from knockknock.telegram_bot.codec import CallbackAction, encode_callback_data


SECRET = "callback-secret"
WEBHOOK_SECRET = "tg-header-secret"
ADMIN_CHAT_ID = 42


@dataclass
class _StubGmail:
    send_calls: list[str] = field(default_factory=list)

    def send_draft(self, draft_id: str) -> str:
        self.send_calls.append(draft_id)
        return f"msg_{draft_id}"


@dataclass
class _StubTg:
    edits: list[dict] = field(default_factory=list)
    answers: list[dict] = field(default_factory=list)

    async def edit_card_to_status(self, *, message_id: int, status_text: str) -> None:
        self.edits.append({"message_id": message_id, "status_text": status_text})

    async def answer_callback(self, *, query_id: str, text: str) -> None:
        self.answers.append({"query_id": query_id, "text": text})


@pytest.fixture
def seeded_draft(db_session: Session) -> EmailDraft:
    company = Company(name="Acme", domain="acme.io")
    db_session.add(company)
    db_session.flush()
    db_session.add(
        PhonebookEntry(
            company_id=company.id,
            careers_email="careers@acme.io",
            source=PhonebookSource.SEED,
        )
    )
    job = JobApplication(
        company_id=company.id,
        title="Backend",
        location="Bangalore",
        description="x",
        apply_url="https://acme.io/jobs/1",
        source="HN_WHO_IS_HIRING",
        source_job_id="j-app-1",
        status=JobStatus.AWAITING_APPROVAL,
        score=8,
        resume_variant_key="backend-distributed",
        discovered_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.flush()
    phonebook = db_session.query(PhonebookEntry).filter_by(company_id=company.id).one()
    draft = EmailDraft(
        job_application_id=job.id,
        phonebook_id=phonebook.id,
        to_email="careers@acme.io",
        subject="Hi",
        body_text="Hi",
        body_html="<p>Hi</p>",
        resume_variant_key="backend-distributed",
        gmail_draft_id="gd_1",
        state=DraftState.DRAFT_CREATED,
    )
    db_session.add(draft)
    db_session.flush()
    return draft


def _app_with(db_session: Session, gmail: _StubGmail, tg: _StubTg) -> TestClient:
    @contextmanager
    def fake_session_scope() -> Iterator[Session]:
        # Reuse the per-test transactional session so commits inside the handler
        # are visible to assertions but rolled back at teardown.
        yield db_session

    config = AppConfig(
        webhook_secret=WEBHOOK_SECRET,
        callback_secret=SECRET,
        admin_chat_id=ADMIN_CHAT_ID,
    )
    app = build_app(
        config=config,
        session_scope=fake_session_scope,
        gmail=gmail,
        telegram=tg,
    )
    return TestClient(app)


def test_healthz_returns_ok(db_session: Session) -> None:
    client = _app_with(db_session, _StubGmail(), _StubTg())
    r = client.get("/healthz")
    assert r.status_code == 200


def test_webhook_requires_header(db_session: Session, seeded_draft: EmailDraft) -> None:
    client = _app_with(db_session, _StubGmail(), _StubTg())
    body = {"update_id": 1}
    r = client.post("/telegram/webhook", json=body)
    assert r.status_code == 401


def test_webhook_rejects_wrong_header(db_session: Session, seeded_draft: EmailDraft) -> None:
    client = _app_with(db_session, _StubGmail(), _StubTg())
    r = client.post(
        "/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert r.status_code == 401


def test_webhook_handles_approve(db_session: Session, seeded_draft: EmailDraft) -> None:
    gmail = _StubGmail()
    tg = _StubTg()
    client = _app_with(db_session, gmail, tg)
    data = encode_callback_data(CallbackAction.APPROVE, draft_id=seeded_draft.id, secret=SECRET)
    update = {
        "update_id": 1,
        "callback_query": {
            "id": "cb-1",
            "from": {"id": ADMIN_CHAT_ID, "is_bot": False, "first_name": "u"},
            "message": {"message_id": 99, "chat": {"id": ADMIN_CHAT_ID}},
            "data": data,
        },
    }
    r = client.post(
        "/telegram/webhook",
        json=update,
        headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
    )
    assert r.status_code == 200
    assert gmail.send_calls == ["gd_1"]
    db_session.refresh(seeded_draft)
    assert seeded_draft.state == DraftState.SENT


def test_webhook_returns_200_on_unauthorized_callback(
    db_session: Session, seeded_draft: EmailDraft
) -> None:
    """Telegram retries on non-200; never echo 4xx back to it for app-layer auth failures."""
    gmail = _StubGmail()
    tg = _StubTg()
    client = _app_with(db_session, gmail, tg)
    data = encode_callback_data(CallbackAction.APPROVE, draft_id=seeded_draft.id, secret=SECRET)
    update = {
        "update_id": 1,
        "callback_query": {
            "id": "cb-1",
            "from": {"id": 999, "is_bot": False, "first_name": "evil"},
            "message": {"message_id": 99, "chat": {"id": 999}},  # wrong chat
            "data": data,
        },
    }
    r = client.post(
        "/telegram/webhook",
        json=update,
        headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
    )
    assert r.status_code == 200
    assert gmail.send_calls == []
```

- [ ] **Step 2: Confirm failure**

```bash
uv run pytest tests/test_telegram_bot/test_app.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement the app**

Create `src/knockknock/telegram_bot/app.py`:

```python
"""FastAPI app for the Telegram webhook."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

import structlog
from fastapi import FastAPI, Header, HTTPException, Request
from sqlmodel import Session

from knockknock.telegram_bot.handlers import (
    HandlerContext,
    UnauthorizedError,
    handle_callback,
)

log = structlog.get_logger(__name__)


class _GmailLike(Protocol):
    def send_draft(self, draft_id: str) -> str: ...


class _TgLike(Protocol):
    async def edit_card_to_status(self, *, message_id: int, status_text: str) -> None: ...
    async def answer_callback(self, *, query_id: str, text: str) -> None: ...


@dataclass(frozen=True, slots=True)
class AppConfig:
    webhook_secret: str  # Telegram's `X-Telegram-Bot-Api-Secret-Token` header value.
    callback_secret: str  # HMAC secret for callback_data.
    admin_chat_id: int


SessionScope = Callable[[], AbstractContextManager[Session]]


def build_app(
    *,
    config: AppConfig,
    session_scope: SessionScope,
    gmail: _GmailLike,
    telegram: _TgLike,
) -> FastAPI:
    app = FastAPI(title="knockknock-telegram-bot")

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    @app.post("/telegram/webhook")
    async def webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> dict:
        if (
            x_telegram_bot_api_secret_token is None
            or x_telegram_bot_api_secret_token != config.webhook_secret
        ):
            log.warning(
                "telegram.webhook_secret_mismatch",
                provided=bool(x_telegram_bot_api_secret_token),
            )
            raise HTTPException(status_code=401, detail="invalid webhook secret")

        update = await request.json()
        started = time.monotonic()
        try:
            with session_scope() as session:
                ctx = HandlerContext(
                    session=session,
                    gmail=gmail,
                    telegram=telegram,
                    admin_chat_id=config.admin_chat_id,
                    callback_secret=config.callback_secret,
                )
                if "callback_query" in update:
                    try:
                        await handle_callback(update, ctx)
                    except UnauthorizedError as exc:
                        # Don't echo 4xx — Telegram will retry forever. Log loud and ack.
                        log.warning(
                            "telegram.callback_unauthorized",
                            update_id=update.get("update_id"),
                            error=str(exc),
                        )
        finally:
            duration_ms = int((time.monotonic() - started) * 1000)
            log.info(
                "telegram.webhook",
                update_id=update.get("update_id"),
                duration_ms=duration_ms,
            )
        return {"ok": True}

    return app
```

- [ ] **Step 4: Run tests until green**

```bash
uv run pytest tests/test_telegram_bot -v
```

Expected: PASS for all 5 webhook tests + codec + handler tests.

- [ ] **Step 5: Full suite + lint + commit**

```bash
uv run pytest -v
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
git add src/knockknock/telegram_bot/app.py tests/test_telegram_bot/test_app.py
git commit -m "feat(telegram): fastapi webhook app with header verification"
```

### Task 9.6: Wire notify stage into pipeline CLI

**Files:**
- Modify: `src/knockknock/__main__.py`

- [ ] **Step 1: Build Telegram client + NotifyStage**

Edit `src/knockknock/__main__.py`. After `DraftStage` is appended, add:

```python
import asyncio

from knockknock.clients.telegram import TelegramClient, build_bot
from knockknock.pipeline.notify import NotifyStage


# ...inside the pipeline command, after DraftStage is appended...
bot = build_bot(secrets.get("telegram-bot-token"))
admin_chat_id = int(secrets.get("telegram-admin-chat-id"))
callback_secret = secrets.get("telegram-callback-secret")
telegram_client = TelegramClient(
    bot=bot, admin_chat_id=admin_chat_id, callback_secret=callback_secret
)
notify_stage = NotifyStage(telegram=telegram_client)
```

The runner's existing `Stage` protocol is sync (`run`). `NotifyStage.run_async` is async. Add a thin sync adapter at the end of the CLI command after the runner finishes the sync stages:

```python
with session_scope(engine) as notify_session:
    asyncio.run(notify_stage.run_async(session=notify_session, run_id=run.id))
```

> **Refactor note:** in Phase 11 we'll change `Stage` protocol to support optional async `run_async`; for now keep notify separate.

- [ ] **Step 2: Smoke-run CLI**

```bash
KNOCKKNOCK_SECRET_TELEGRAM_BOT_TOKEN=fake \
KNOCKKNOCK_SECRET_TELEGRAM_ADMIN_CHAT_ID=42 \
KNOCKKNOCK_SECRET_TELEGRAM_CALLBACK_SECRET=cb-secret \
... other secrets ... \
uv run knockknock pipeline run --once
```

Expected: bot construction succeeds with a fake token (it's lazy); notify stage reports `advanced=0` (no draft rows yet).

- [ ] **Step 3: Commit**

```bash
git add src/knockknock/__main__.py
git commit -m "feat(cli): wire telegram notify stage into pipeline"
```

### Task 9.7: Bot service entrypoint + uvicorn runner

**Files:**
- Create: `src/knockknock/telegram_bot/__main__.py`

The bot is its own process; it does not share the CLI's `__main__.py`.

- [ ] **Step 1: Write the entrypoint**

Create `src/knockknock/telegram_bot/__main__.py`:

```python
"""Run the telegram-bot FastAPI service via uvicorn.

Invocation:
    uv run python -m knockknock.telegram_bot
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from collections.abc import Iterator

import structlog
import uvicorn
from sqlmodel import Session

from knockknock.clients.gmail import GmailClient, build_gmail_service
from knockknock.clients.telegram import TelegramClient, build_bot
from knockknock.config.secrets import build_secrets_client
from knockknock.config.settings import Settings
from knockknock.db.engine import make_sync_engine
from knockknock.db.session import session_scope as real_session_scope
from knockknock.logging import configure_logging
from knockknock.telegram_bot.app import AppConfig, build_app


def main() -> None:
    settings = Settings()
    configure_logging(settings)
    log = structlog.get_logger(__name__)

    secrets = build_secrets_client(settings)
    engine = make_sync_engine(settings.database_url)

    bot = build_bot(secrets.get("telegram-bot-token"))
    admin_chat_id = int(secrets.get("telegram-admin-chat-id"))
    callback_secret = secrets.get("telegram-callback-secret")
    webhook_secret = secrets.get("telegram-webhook-secret")
    sender_email = secrets.get("gmail-sender-email")

    gmail_service = build_gmail_service(
        client_id=secrets.get("gmail-oauth-client-id"),
        client_secret=secrets.get("gmail-oauth-client-secret"),
        refresh_token=secrets.get("gmail-oauth-refresh-token"),
    )
    gmail = GmailClient(service=gmail_service, sender_email=sender_email)
    telegram = TelegramClient(
        bot=bot, admin_chat_id=admin_chat_id, callback_secret=callback_secret
    )

    @contextmanager
    def session_scope_factory() -> Iterator[Session]:
        with real_session_scope(engine) as session:
            yield session

    app = build_app(
        config=AppConfig(
            webhook_secret=webhook_secret,
            callback_secret=callback_secret,
            admin_chat_id=admin_chat_id,
        ),
        session_scope=session_scope_factory,
        gmail=gmail,
        telegram=telegram,
    )

    port = int(os.environ.get("PORT", "8080"))
    log.info("telegram_bot.start", port=port)
    uvicorn.run(app, host="0.0.0.0", port=port, log_config=None)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-run locally with fake secrets**

```bash
KNOCKKNOCK_SECRET_TELEGRAM_BOT_TOKEN=fake \
KNOCKKNOCK_SECRET_TELEGRAM_ADMIN_CHAT_ID=42 \
KNOCKKNOCK_SECRET_TELEGRAM_CALLBACK_SECRET=cb \
KNOCKKNOCK_SECRET_TELEGRAM_WEBHOOK_SECRET=wh \
... gmail secrets ... \
uv run python -m knockknock.telegram_bot
```

Expected: server logs `telegram_bot.start port=8080` then accepts requests at `http://localhost:8080/healthz`. Stop with Ctrl-C.

- [ ] **Step 3: Commit**

```bash
git add src/knockknock/telegram_bot/__main__.py
git commit -m "feat(telegram): bot service entrypoint via uvicorn"
```

---

← [Index](00-index.md) · [Prev: phase-08-email-draft.md](phase-08-email-draft.md) · [Next: phase-10-scrapers.md](phase-10-scrapers.md)
