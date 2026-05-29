"""Telegram webhook callback handler -- DB-mediated approval flow.

The bot service receives Telegram ``Update`` payloads on its FastAPI
``/telegram/webhook`` endpoint (Task 9.4). For ``callback_query``
updates (inline-button taps), the endpoint forwards the parsed dict
to :func:`handle_callback`, which:

1. Verifies the message came from the admin chat ID (drops anything
   else as :class:`UnauthorizedError`).
2. Decodes + verifies the HMAC-signed ``callback_data`` (same code,
   raises :class:`UnauthorizedError` on bad signature).
3. Flips the appropriate DB rows:

   - **APPROVE**: ``job.status: AWAITING_APPROVAL -> APPROVED``. The
     pipeline's SendStage (Task 9.5) will pick it up and ask Gmail to
     send the existing draft. ``draft.state`` is left at
     ``DRAFT_CREATED`` -- SendStage transitions it to ``SENT`` after
     Gmail accepts.
   - **REJECT**: ``draft.state -> SUPERSEDED``,
     ``job.status -> USER_REJECTED``,
     ``job.rejection_reason -> USER_REJECTED``.
   - **REGENERATE**: ``draft.state -> SUPERSEDED``,
     ``job.status -> TAILORED``. The next pipeline run's DraftStage
     will produce a fresh draft (and the partial unique index on
     active drafts permits the new row because SUPERSEDED is not in
     the index predicate).

4. Writes a :class:`JobApplicationEvent` audit row with
   ``from_status -> to_status`` of the job + an optional ``note``
   carrying the action and tg message id.
5. Writes an inbound :class:`TelegramMessage` audit row
   (``direction=INCOMING``, ``kind=APPROVAL`` / ``REJECTION`` /
   ``COMMAND``) with ``email_draft_id`` populated.
6. Calls ``telegram.answer_callback`` (dismiss the spinner toast) and
   ``telegram.edit_card_to_status`` (strike the buttons with a status
   line).

The handler is intentionally narrow: it touches the DB and one TG
client. No Gemini, no Gmail, no scraping. That keeps the bot service's
threat model small -- the worst an attacker who somehow reaches the
webhook can do is flip job statuses (and that's already gated by
``X-Telegram-Bot-Api-Secret-Token`` + admin chat ID + HMAC).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog
from sqlmodel import Session

from knockknock.db.enums import (
    EmailDraftState,
    JobStatus,
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
from knockknock.telegram_bot.codec import (
    CallbackAction,
    decode_callback_data,
)

log = structlog.get_logger(__name__)


class UnauthorizedError(Exception):
    """Raised for any authorization failure: wrong chat id, bad HMAC,
    missing fields.

    The FastAPI app catches this and returns ``200 OK`` (we don't want
    to leak detail back to Telegram -- a 4xx would tell an attacker
    which axis of authz failed).
    """


class _TelegramProto(Protocol):
    """Minimal subset of :class:`knockknock.clients.telegram.TelegramClient`
    used by this handler. Lets unit tests stub with a tiny dataclass.
    """

    async def answer_callback(self, *, query_id: str, text: str) -> None: ...
    async def edit_card_to_status(self, *, message_id: int, status_text: str) -> None: ...


@dataclass(slots=True)
class HandlerContext:
    """Everything :func:`handle_callback` needs to do its work.

    Built once per request by the FastAPI layer (with a fresh DB
    session) so the handler itself stays a pure function from
    (update, ctx) to (DB effects + TG calls + result).
    """

    session: Session
    telegram: _TelegramProto
    admin_chat_id: int
    callback_secret: str


@dataclass(frozen=True, slots=True)
class HandlerResult:
    """Returned to the FastAPI app for logging/observability."""

    action: CallbackAction
    draft_id: int
    chat_id: int
    tg_message_id: int


async def handle_callback(update: dict[str, Any], ctx: HandlerContext) -> HandlerResult | None:
    """Top-level dispatch.

    Returns ``None`` for non-callback updates (e.g. typed text) so the
    FastAPI layer can short-circuit cheaply. Raises
    :class:`UnauthorizedError` for any authz failure.
    """
    cb = update.get("callback_query")
    if not cb:
        # Not a button tap -- ignore. We don't process plain text here;
        # the digest / alerts flow handles its own outbound messages.
        return None

    query_id = cb.get("id")
    data = cb.get("data")
    message = cb.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    tg_message_id = message.get("message_id")
    if query_id is None or data is None or chat_id is None or tg_message_id is None:
        raise UnauthorizedError("Malformed callback_query payload")

    if int(chat_id) != int(ctx.admin_chat_id):
        # Not from the admin user. Don't leak that we know about
        # other chats -- just refuse.
        log.warning(
            "telegram.handler.wrong_chat",
            chat_id=chat_id,
            admin_chat_id=ctx.admin_chat_id,
        )
        raise UnauthorizedError(f"Unknown chat id {chat_id}")

    try:
        action, draft_id = decode_callback_data(data, secret=ctx.callback_secret)
    except ValueError as exc:
        # Map any codec failure to UnauthorizedError so the caller has
        # one exception type to catch. Preserve the "signature" word
        # for tests / logs.
        raise UnauthorizedError(f"Bad callback signature: {exc}") from exc

    draft = ctx.session.get(EmailDraft, draft_id)
    if draft is None:
        # The draft was deleted (or never existed) -- this is suspicious
        # but not fatal. Refuse so we don't accidentally flip an
        # unrelated job.
        raise UnauthorizedError(f"Unknown draft id {draft_id}")

    job = ctx.session.get(JobApplication, draft.job_id)
    if job is None:
        # Shouldn't happen given FK constraints, but defend anyway.
        raise UnauthorizedError(f"Draft {draft_id} has no parent job")

    # --- dispatch ----------------------------------------------------
    if action is CallbackAction.APPROVE:
        toast, status_text = _apply_approve(ctx.session, draft, job, tg_message_id)
        tg_kind = TelegramKind.APPROVAL
    elif action is CallbackAction.REJECT:
        toast, status_text = _apply_reject(ctx.session, draft, job, tg_message_id)
        tg_kind = TelegramKind.REJECTION
    elif action is CallbackAction.REGENERATE:
        toast, status_text = _apply_regenerate(ctx.session, draft, job, tg_message_id)
        tg_kind = TelegramKind.COMMAND
    else:  # pragma: no cover - exhaustive
        raise UnauthorizedError(f"Unhandled action {action}")

    # --- audit: inbound TG message row -------------------------------
    ctx.session.add(
        TelegramMessage(
            job_id=job.id,
            email_draft_id=draft.id,
            direction=TelegramDirection.INCOMING,
            kind=tg_kind,
            chat_id=int(chat_id),
            message_id=int(tg_message_id),
            payload={"action": action.value, "data": data, "query_id": query_id},
        )
    )
    ctx.session.flush()
    ctx.session.commit()

    # --- TG UX (best-effort, after the DB commit) --------------------
    await ctx.telegram.answer_callback(query_id=str(query_id), text=toast)
    await ctx.telegram.edit_card_to_status(message_id=int(tg_message_id), status_text=status_text)

    return HandlerResult(
        action=action,
        draft_id=int(draft.id) if draft.id is not None else draft_id,
        chat_id=int(chat_id),
        tg_message_id=int(tg_message_id),
    )


# ============ action implementations =======================================
#
# Each ``_apply_*`` mutates ``draft`` + ``job`` in-place, writes the
# state-transition audit row, and returns a (toast, card_status_text)
# pair that the dispatcher uses to talk to the TG client.
#
# They flush but do NOT commit -- the top-level handler commits once,
# after all mutations and after writing the inbound-message row.


def _apply_approve(
    session: Session, draft: EmailDraft, job: JobApplication, tg_message_id: int
) -> tuple[str, str]:
    from_status = job.status
    job.status = JobStatus.APPROVED
    draft.decided_at = datetime.now(UTC)
    session.add(job)
    session.add(draft)
    _write_event(
        session,
        job_id=job.id,
        from_status=from_status,
        to_status=JobStatus.APPROVED,
        note="user approved via telegram",
        payload={"tg_message_id": tg_message_id, "draft_id": draft.id},
    )
    session.flush()
    to = draft.to_recipients[0] if draft.to_recipients else "?"
    return ("Approved - sending soon", f"\u2705 Approved \u2014 sending to {to}")


def _apply_reject(
    session: Session, draft: EmailDraft, job: JobApplication, tg_message_id: int
) -> tuple[str, str]:
    from_status = job.status
    draft.state = EmailDraftState.SUPERSEDED
    draft.decided_at = datetime.now(UTC)
    job.status = JobStatus.USER_REJECTED
    job.rejection_reason = RejectionReason.USER_REJECTED
    session.add(draft)
    session.add(job)
    _write_event(
        session,
        job_id=job.id,
        from_status=from_status,
        to_status=JobStatus.USER_REJECTED,
        note="user rejected via telegram",
        payload={"tg_message_id": tg_message_id, "draft_id": draft.id},
    )
    session.flush()
    return ("Rejected", "\u274c Rejected")


def _apply_regenerate(
    session: Session, draft: EmailDraft, job: JobApplication, tg_message_id: int
) -> tuple[str, str]:
    from_status = job.status
    draft.state = EmailDraftState.SUPERSEDED
    draft.decided_at = datetime.now(UTC)
    # Drop the job back to TAILORED so the next DraftStage pass redrafts.
    # We keep ``job.resume_variant_key`` so the regen uses the same resume.
    job.status = JobStatus.TAILORED
    session.add(draft)
    session.add(job)
    _write_event(
        session,
        job_id=job.id,
        from_status=from_status,
        to_status=JobStatus.TAILORED,
        note="user requested regenerate via telegram",
        payload={"tg_message_id": tg_message_id, "draft_id": draft.id},
    )
    session.flush()
    return ("Regenerating - new draft on next run", "\u21bb Regenerating")


def _write_event(
    session: Session,
    *,
    job_id: int | None,
    from_status: JobStatus,
    to_status: JobStatus,
    note: str,
    payload: dict[str, Any],
) -> None:
    if job_id is None:
        # Should be unreachable -- FK constraint on job_application_events
        # would explode anyway. Defensive log + skip.
        log.warning("telegram.handler.audit_skipped_no_job_id", note=note)
        return
    session.add(
        JobApplicationEvent(
            job_id=job_id,
            from_status=from_status,
            to_status=to_status,
            note=note,
            # JSONB-safe round-trip: dict -> json -> dict ensures all
            # values are serializable up front (so we don't fail at
            # commit time on a stray datetime).
            payload=json.loads(json.dumps(payload, default=str)),
        )
    )
