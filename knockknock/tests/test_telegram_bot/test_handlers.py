"""Tests for the Telegram webhook callback handler.

These are integration tests -- they go through a real Postgres session
(via the ``db_session`` fixture from ``conftest.py``) because the
handler's job is exactly to flip DB rows and write audit/audit-trail
rows. Stubbing the ORM would invert the test pyramid.

The handler is DB-mediated per the Phase 9 brainstorming decision:
APPROVE moves ``job.status`` to ``APPROVED`` (a new enum value added
by migration 0004); it does NOT call Gmail. The pipeline's SendStage
(Task 9.5) polls for APPROVED rows and performs the Gmail send. This
keeps the bot service "dumb" -- no Gemini, no Gmail, no external API
calls. Smaller blast radius, single source of truth for what gets
sent.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlmodel import Session, select

from knockknock.db.enums import (
    CompanySizeBucket,
    EmailDraftState,
    JobSource,
    JobStatus,
    RejectionReason,
    TelegramDirection,
    TelegramKind,
)
from knockknock.db.models import (
    Company,
    EmailDraft,
    JobApplication,
    JobApplicationEvent,
    TelegramMessage,
)
from knockknock.telegram_bot.codec import CallbackAction, encode_callback_data
from knockknock.telegram_bot.handlers import (
    HandlerContext,
    UnauthorizedError,
    handle_callback,
)

SECRET = "callback-secret"
ADMIN_CHAT_ID = 42


@dataclass
class _StubTg:
    """Minimal TelegramClient stub: records the two methods the
    handler calls. The real client lives in ``clients/telegram.py``;
    here we only care that the handler emits the right calls.
    """

    edits: list[dict[str, Any]] = field(default_factory=list)
    answers: list[dict[str, Any]] = field(default_factory=list)

    async def edit_card_to_status(self, *, message_id: int, status_text: str) -> None:
        self.edits.append({"message_id": message_id, "status_text": status_text})

    async def answer_callback(self, *, query_id: str, text: str) -> None:
        self.answers.append({"query_id": query_id, "text": text})


def _seed_draft(
    db_session: Session,
    *,
    source_job_id: str = "j-1",
    status: JobStatus = JobStatus.AWAITING_APPROVAL,
    draft_state: EmailDraftState = EmailDraftState.DRAFT_CREATED,
) -> EmailDraft:
    """Seed a company + job + email_draft in DRAFT_CREATED state.

    Mirrors what DraftStage produces at the end of Phase 8: job is
    AWAITING_APPROVAL, draft has a gmail_draft_id and is DRAFT_CREATED.
    """
    company = Company(
        name=f"Acme-{source_job_id}",
        domain=f"acme-{source_job_id}.test",
        size_bucket=CompanySizeBucket.SERIES_A,
    )
    db_session.add(company)
    db_session.flush()
    job = JobApplication(
        company_id=company.id,
        source=JobSource.HN,
        source_job_id=source_job_id,
        title="Backend Engineer",
        location="Bengaluru",
        apply_url="https://acme.test/jobs/1",
        description="Build distributed things.",
        status=status,
        score=8,
        resume_variant_key="backend-distributed",
    )
    db_session.add(job)
    db_session.flush()
    draft = EmailDraft(
        job_id=job.id,
        subject="Senior Backend Engineer -- Nishant Gupta",
        body="Hi team,\n\nI'd love to chat about the role.\n\nBest,\nNishant",
        to_recipients=["careers@acme.test"],
        gmail_draft_id="gd_1",
        state=draft_state,
    )
    db_session.add(draft)
    db_session.flush()
    return draft


def _make_update(
    action: CallbackAction,
    draft_id: int,
    *,
    chat_id: int = ADMIN_CHAT_ID,
    message_id: int = 555,
    secret: str = SECRET,
) -> dict[str, Any]:
    """Shape a Telegram callback_query Update payload."""
    data = encode_callback_data(action, draft_id=draft_id, secret=secret)
    return {
        "update_id": 100,
        "callback_query": {
            "id": "cb-1",
            "from": {"id": chat_id, "is_bot": False, "first_name": "User"},
            "message": {"message_id": message_id, "chat": {"id": chat_id}},
            "data": data,
        },
    }


def _context(db_session: Session, tg: _StubTg) -> HandlerContext:
    return HandlerContext(
        session=db_session,
        telegram=tg,
        admin_chat_id=ADMIN_CHAT_ID,
        callback_secret=SECRET,
    )


# ----------------- APPROVE --------------------------------------------------


def test_approve_flips_job_to_approved(db_session: Session) -> None:
    """The hot path: user taps Approve. Handler flips
    ``job.status = APPROVED`` and stamps ``draft.decided_at``. Does NOT
    touch ``draft.state`` -- the draft is still the source of truth for
    what SendStage will send (it stays DRAFT_CREATED until Gmail
    accepts the send).
    """
    draft = _seed_draft(db_session)
    tg = _StubTg()
    ctx = _context(db_session, tg)
    update = _make_update(CallbackAction.APPROVE, draft.id)

    result = asyncio.run(handle_callback(update, ctx))

    db_session.refresh(draft)
    job = db_session.get(JobApplication, draft.job_id)
    assert job is not None
    assert result.action is CallbackAction.APPROVE
    assert result.draft_id == draft.id
    assert job.status == JobStatus.APPROVED
    assert draft.state == EmailDraftState.DRAFT_CREATED  # unchanged
    assert draft.decided_at is not None
    # Toast + card edit happened
    assert len(tg.answers) == 1
    assert len(tg.edits) == 1


def test_approve_writes_event_audit_row(db_session: Session) -> None:
    draft = _seed_draft(db_session, source_job_id="j-approve-evt")
    ctx = _context(db_session, _StubTg())
    asyncio.run(handle_callback(_make_update(CallbackAction.APPROVE, draft.id), ctx))

    events = db_session.exec(
        select(JobApplicationEvent).where(JobApplicationEvent.job_id == draft.job_id)
    ).all()
    assert any(
        e.from_status == JobStatus.AWAITING_APPROVAL and e.to_status == JobStatus.APPROVED
        for e in events
    )


def test_approve_writes_incoming_telegram_message(db_session: Session) -> None:
    draft = _seed_draft(db_session, source_job_id="j-approve-tg")
    ctx = _context(db_session, _StubTg())
    asyncio.run(handle_callback(_make_update(CallbackAction.APPROVE, draft.id), ctx))

    tg_rows = db_session.exec(
        select(TelegramMessage).where(TelegramMessage.email_draft_id == draft.id)
    ).all()
    assert any(
        m.direction == TelegramDirection.INCOMING and m.kind == TelegramKind.APPROVAL
        for m in tg_rows
    )


# ----------------- REJECT ---------------------------------------------------


def test_reject_marks_superseded_and_user_rejected(db_session: Session) -> None:
    draft = _seed_draft(db_session, source_job_id="j-reject")
    tg = _StubTg()
    ctx = _context(db_session, tg)
    update = _make_update(CallbackAction.REJECT, draft.id)

    asyncio.run(handle_callback(update, ctx))

    db_session.refresh(draft)
    job = db_session.get(JobApplication, draft.job_id)
    assert job is not None
    assert draft.state == EmailDraftState.SUPERSEDED
    assert draft.decided_at is not None
    assert job.status == JobStatus.USER_REJECTED
    assert job.rejection_reason == RejectionReason.USER_REJECTED


# ----------------- REGENERATE ----------------------------------------------


def test_regenerate_moves_job_back_to_tailored(db_session: Session) -> None:
    """Regenerate is a defer -- the next pipeline run will draft a
    fresh email via DraftStage. We mark the existing draft SUPERSEDED
    so the partial unique index lets the new one land.
    """
    draft = _seed_draft(db_session, source_job_id="j-regen")
    tg = _StubTg()
    ctx = _context(db_session, tg)
    update = _make_update(CallbackAction.REGENERATE, draft.id)

    asyncio.run(handle_callback(update, ctx))

    db_session.refresh(draft)
    job = db_session.get(JobApplication, draft.job_id)
    assert job is not None
    assert draft.state == EmailDraftState.SUPERSEDED
    assert draft.decided_at is not None
    assert job.status == JobStatus.TAILORED


# ----------------- AUTHZ ----------------------------------------------------


def test_handler_rejects_wrong_chat_id(db_session: Session) -> None:
    draft = _seed_draft(db_session, source_job_id="j-wrong")
    ctx = _context(db_session, _StubTg())
    update = _make_update(CallbackAction.APPROVE, draft.id, chat_id=999)

    with pytest.raises(UnauthorizedError, match="chat"):
        asyncio.run(handle_callback(update, ctx))

    # No mutation
    db_session.refresh(draft)
    job = db_session.get(JobApplication, draft.job_id)
    assert job is not None
    assert draft.state == EmailDraftState.DRAFT_CREATED
    assert job.status == JobStatus.AWAITING_APPROVAL


def test_handler_rejects_bad_hmac(db_session: Session) -> None:
    draft = _seed_draft(db_session, source_job_id="j-hmac")
    ctx = _context(db_session, _StubTg())
    update = _make_update(CallbackAction.APPROVE, draft.id)
    # Flip the last hex char of the signature
    last = update["callback_query"]["data"][-1]
    swap = "0" if last != "0" else "1"
    update["callback_query"]["data"] = update["callback_query"]["data"][:-1] + swap

    with pytest.raises(UnauthorizedError, match="signature"):
        asyncio.run(handle_callback(update, ctx))


def test_handler_rejects_non_callback_update(db_session: Session) -> None:
    """A text message update (no callback_query) is a no-op -- the
    handler returns None without raising. This is what we want for
    e.g. typed-text from the user, which we don't process.
    """
    ctx = _context(db_session, _StubTg())
    # Plain text update -- no callback_query key.
    update: dict[str, Any] = {
        "update_id": 200,
        "message": {
            "message_id": 1,
            "chat": {"id": ADMIN_CHAT_ID},
            "text": "hi",
        },
    }
    result = asyncio.run(handle_callback(update, ctx))
    assert result is None
