"""Background poller: post Telegram cards for new DRAFT_CREATED drafts.

Architecture (per Phase 9 brainstorm):

The bot service runs a long-lived asyncio task -- :func:`run_forever`
below -- that wakes every ``poll_interval_seconds`` (default 60s),
calls :func:`poll_once`, and goes back to sleep. ``poll_once`` is the
unit of work and is the only thing tests exercise; ``run_forever`` is
a thin sleep+repeat shell that's easier to verify by inspection than
through pytest.

What each tick does:

1. ``LEFT JOIN`` ``email_drafts`` (state=DRAFT_CREATED) against
   ``telegram_messages`` filtered to ``direction=OUTGOING`` AND
   ``kind=DRAFT_PREVIEW``. Drafts whose right-side join is NULL are
   "new" -- never previewed.
2. For each new draft, build a :class:`DraftCardPayload` and call
   ``telegram.send_draft_card``. On success, persist an OUTGOING
   DRAFT_PREVIEW :class:`TelegramMessage` row so the next tick will
   filter this draft out. On failure, log + skip -- the next tick
   tries again. We deliberately do NOT persist on failure: a missing
   audit row is the only thing that lets us retry without race.

Why poll instead of "fire from DraftStage":

- DraftStage runs in the cron pipeline (Phase 8), which doesn't have
  the Telegram bot token loaded. Forcing it to would smear the
  optional ``[telegram]`` extra across the cron's import graph.
- Pollers are also self-healing: if the bot was down when DraftStage
  ran, the next poll picks the draft up. No work lost.

Why pull score from JobApplication into the payload:

The Telegram card shows ``score=8/10`` at the top so the operator can
triage at a glance. The draft itself doesn't carry the score (it lives
on JobApplication) so we join through.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

import structlog
from sqlalchemy import asc, desc
from sqlmodel import Session, select

from knockknock.clients.telegram import DraftCardPayload
from knockknock.db.enums import (
    EmailDraftState,
    TelegramDirection,
    TelegramKind,
)
from knockknock.db.models import (
    Company,
    EmailDraft,
    JobApplication,
    TelegramMessage,
)

log = structlog.get_logger(__name__)


class _TgLike(Protocol):
    """Minimal Telegram surface the poller depends on."""

    async def send_draft_card(self, payload: DraftCardPayload) -> int: ...


# Truncate body to this many chars before stuffing it in the
# DraftCardPayload preview. The Telegram client wrapper itself trims
# again to fit the 4096-char message limit; this is a "send only what
# we need" cap so the JSONB ``payload`` (if we ever attach the preview
# to the audit row) doesn't bloat.
_PREVIEW_CHARS = 600


async def poll_once(*, session: Session, telegram: _TgLike) -> int:
    """One poll cycle. Returns the number of cards successfully posted.

    Selects ``DRAFT_CREATED`` drafts that have no OUTGOING DRAFT_PREVIEW
    Telegram message row yet, posts a card for each in score-desc order,
    and persists an OUTGOING row per successful send.

    Failures are logged and skipped; the next tick retries them. We
    intentionally do not persist a TelegramMessage row on failure --
    otherwise the LEFT JOIN would filter the draft out and the operator
    would never see the card.
    """
    # LEFT JOIN telegram_messages on (draft_id, OUTGOING, DRAFT_PREVIEW);
    # rows where the join is NULL are drafts that have never been previewed.
    # Order by score desc, then discovered_at asc so the best candidates
    # show up first even when the bot's catching up after downtime.
    stmt = (
        select(EmailDraft, JobApplication, Company)
        .join(JobApplication, JobApplication.id == EmailDraft.job_id)  # type: ignore[arg-type]
        .join(Company, Company.id == JobApplication.company_id)  # type: ignore[arg-type]
        .outerjoin(
            TelegramMessage,
            (TelegramMessage.email_draft_id == EmailDraft.id)  # type: ignore[arg-type]
            & (TelegramMessage.direction == TelegramDirection.OUTGOING)
            & (TelegramMessage.kind == TelegramKind.DRAFT_PREVIEW),
        )
        .where(EmailDraft.state == EmailDraftState.DRAFT_CREATED)
        .where(TelegramMessage.id.is_(None))  # type: ignore[union-attr]
        .order_by(
            desc(JobApplication.score),  # type: ignore[arg-type]
            asc(JobApplication.discovered_at),  # type: ignore[arg-type]
        )
    )

    posted = 0
    for draft, job, company in session.exec(stmt).all():
        # to_recipients is JSONB list[str], non-empty by DraftStage invariant
        # (it refuses to persist a DRAFT_CREATED with no recipients).
        to_email = draft.to_recipients[0] if draft.to_recipients else ""
        cc_email: str | None = None
        if draft.cc_recipients:
            cc_email = draft.cc_recipients[0]

        payload = DraftCardPayload(
            draft_id=int(draft.id) if draft.id is not None else 0,
            company=company.name,
            role_title=job.title,
            score=int(job.score or 0),
            resume_variant=job.resume_variant_key or "",
            to_email=to_email,
            cc_email=cc_email,
            preview=(draft.body or "")[:_PREVIEW_CHARS],
        )

        try:
            tg_message_id = await telegram.send_draft_card(payload)
        except Exception as exc:
            # Any TG-side failure (TelegramError or otherwise) is logged
            # and skipped. Crucially we do NOT persist an audit row --
            # the next poll cycle must retry.
            log.warning(
                "poller.send_failed",
                draft_id=draft.id,
                job_id=job.id,
                error=str(exc),
            )
            continue

        session.add(
            TelegramMessage(
                job_id=job.id,
                email_draft_id=draft.id,
                direction=TelegramDirection.OUTGOING,
                kind=TelegramKind.DRAFT_PREVIEW,
                chat_id=0,  # the TG client owns the admin chat id; 0 = "see config"
                message_id=int(tg_message_id),
                payload={
                    "score": int(job.score or 0),
                    "company": company.name,
                    "role_title": job.title,
                },
            )
        )
        session.flush()
        session.commit()
        posted += 1
        log.info(
            "poller.card_posted",
            draft_id=draft.id,
            job_id=job.id,
            tg_message_id=tg_message_id,
        )

    return posted


async def run_forever(
    *,
    session_factory: _SessionFactory,
    telegram: _TgLike,
    poll_interval_seconds: float = 60.0,
) -> None:
    """Long-lived poll loop. Cancels cleanly on :class:`asyncio.CancelledError`.

    Each iteration opens a fresh DB session, runs one poll cycle, and
    sleeps. Errors inside ``poll_once`` are caught + logged so a single
    bad iteration doesn't kill the loop. The session is closed even
    when ``poll_once`` raises (``with`` block).

    Cancellation: when the bot service shuts down (uvicorn lifespan
    or SIGTERM), the outer task ``cancel()`` raises
    :class:`asyncio.CancelledError` inside ``asyncio.sleep``; we re-raise
    so the supervisor sees a clean shutdown.
    """
    log.info("poller.starting", interval_s=poll_interval_seconds)
    while True:
        try:
            with session_factory() as session:
                await poll_once(session=session, telegram=telegram)
        except asyncio.CancelledError:
            log.info("poller.cancelled")
            raise
        except Exception as exc:
            # Catch-all: log + sleep + keep going. A single tick crashing
            # (e.g. transient DB outage) should not bring down the bot.
            log.warning("poller.tick_failed", error=str(exc))
        try:
            await asyncio.sleep(poll_interval_seconds)
        except asyncio.CancelledError:
            log.info("poller.cancelled")
            raise


# Production session factory shape: a context-manager-yielding callable
# (``lambda: Session(bind=engine)`` plus a ``with`` to ensure close()).
# We don't bind it to a concrete type here because the bot service's
# entrypoint (Task 9.7) constructs it from the engine; tests don't
# exercise ``run_forever``.
class _SessionFactory(Protocol):
    def __call__(self) -> _SessionCM: ...


class _SessionCM(Protocol):
    def __enter__(self) -> Session: ...
    def __exit__(self, *args: object) -> None: ...
