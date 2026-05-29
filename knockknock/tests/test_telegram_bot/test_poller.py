"""Tests for the NotifyPoller -- the bot service's background "post draft cards" loop.

Architecture (per Phase 9 brainstorm): the bot service runs a tiny
asyncio background task that wakes every ~60s, queries
``email_drafts`` for ``state=DRAFT_CREATED`` rows that don't yet have
an OUTGOING DRAFT_PREVIEW :class:`TelegramMessage`, and posts an
approval card for each. The poller's job is purely "this draft hasn't
been shown to the user yet -> show it". Approve/Reject/Regenerate is
the webhook handler's job.

Why this lives in the bot (not the pipeline cron): the bot service
already has the Telegram bot token + a long-running asyncio loop for
the webhook. Asking the cron pipeline to import python-telegram-bot
just to post one card per draft would smear the optional ``[telegram]``
extra across the cron's import graph. Keeping it in the bot also means
"draft posted to TG" latency is bounded by the poll interval (60s),
not by the cron cadence (1 / day).

We test :func:`poll_once` -- a pure coroutine that does exactly one
poll cycle. The run-forever wrapper (sleep + repeat + cancel handling)
is trivial enough to test by hand.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from sqlmodel import Session, select

from knockknock.clients.telegram import DraftCardPayload
from knockknock.db.enums import (
    CompanySizeBucket,
    EmailDraftState,
    JobSource,
    JobStatus,
    TelegramDirection,
    TelegramKind,
)
from knockknock.db.models import (
    Company,
    EmailDraft,
    JobApplication,
    TelegramMessage,
)
from knockknock.telegram_bot.poller import poll_once


@dataclass
class _StubTg:
    """Records ``send_draft_card`` calls; returns synthetic message ids.

    The real TelegramClient lives in ``clients/telegram.py`` and talks
    to ``python-telegram-bot``. Here we only care that the poller
    builds the right :class:`DraftCardPayload` and persists the
    returned message id.
    """

    calls: list[DraftCardPayload] = field(default_factory=list)
    next_message_id: int = 10000
    fail: bool = False

    async def send_draft_card(self, payload: DraftCardPayload) -> int:
        if self.fail:
            raise RuntimeError("tg send broke")
        self.calls.append(payload)
        self.next_message_id += 1
        return self.next_message_id


def _seed_draft(
    db_session: Session,
    *,
    source_job_id: str = "j-poll-1",
    job_status: JobStatus = JobStatus.AWAITING_APPROVAL,
    draft_state: EmailDraftState = EmailDraftState.DRAFT_CREATED,
    score: int = 8,
    cc: list[str] | None = None,
) -> EmailDraft:
    """Standard fixture: company + AWAITING_APPROVAL job + DRAFT_CREATED draft.

    Mirrors what DraftStage leaves at the end of Phase 8: the draft is
    in the Gmail-side ``DRAFT_CREATED`` state, the job is
    ``AWAITING_APPROVAL``, and no Telegram message has been posted yet.
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
        apply_url=f"https://acme-{source_job_id}.test/jobs/1",
        description="Build distributed things.",
        status=job_status,
        score=score,
        resume_variant_key="backend-distributed",
    )
    db_session.add(job)
    db_session.flush()
    draft = EmailDraft(
        job_id=job.id,
        subject="Senior Backend Engineer -- Nishant Gupta",
        body="Hi team,\n\nI'd love to chat.\n\nBest,\nNishant",
        to_recipients=["careers@acme.test"],
        cc_recipients=cc,
        gmail_draft_id="gd_1",
        state=draft_state,
    )
    db_session.add(draft)
    db_session.flush()
    return draft


# ----------------- happy path -----------------------------------------------


def test_poll_once_posts_card_for_new_draft(db_session: Session) -> None:
    """A DRAFT_CREATED draft with no OUTGOING DRAFT_PREVIEW row gets a
    card posted. The Telegram message id is persisted in a new
    :class:`TelegramMessage` (direction=OUTGOING, kind=DRAFT_PREVIEW) so
    the poller won't post again on the next tick.
    """
    draft = _seed_draft(db_session)
    tg = _StubTg(next_message_id=12000)
    sent = asyncio.run(poll_once(session=db_session, telegram=tg))

    assert sent == 1
    assert len(tg.calls) == 1
    payload = tg.calls[0]
    assert payload.draft_id == draft.id
    assert payload.score == 8
    assert payload.to_email == "careers@acme.test"
    assert payload.resume_variant == "backend-distributed"
    # Persisted outgoing message row
    row = db_session.exec(
        select(TelegramMessage).where(TelegramMessage.email_draft_id == draft.id)
    ).one()
    assert row.direction == TelegramDirection.OUTGOING
    assert row.kind == TelegramKind.DRAFT_PREVIEW
    assert row.message_id == 12001


def test_poll_once_includes_cc_when_present(db_session: Session) -> None:
    """When ``cc_recipients`` is non-empty, the card's cc_email is set."""
    _seed_draft(db_session, source_job_id="j-cc", cc=["careers@globex.io"])
    tg = _StubTg()
    asyncio.run(poll_once(session=db_session, telegram=tg))
    assert tg.calls[0].cc_email == "careers@globex.io"


def test_poll_once_no_cc_when_empty(db_session: Session) -> None:
    _seed_draft(db_session, source_job_id="j-no-cc", cc=None)
    tg = _StubTg()
    asyncio.run(poll_once(session=db_session, telegram=tg))
    assert tg.calls[0].cc_email is None


# ----------------- idempotency ----------------------------------------------


def test_poll_once_is_idempotent(db_session: Session) -> None:
    """Second poll cycle posts nothing new for the same draft.

    The LEFT JOIN to ``telegram_messages`` filters out drafts that
    already have an OUTGOING DRAFT_PREVIEW row. This is what stops the
    bot from spamming the admin chat once per minute.
    """
    draft = _seed_draft(db_session, source_job_id="j-idem")
    tg = _StubTg()
    first = asyncio.run(poll_once(session=db_session, telegram=tg))
    second = asyncio.run(poll_once(session=db_session, telegram=tg))
    assert first == 1
    assert second == 0
    assert len(tg.calls) == 1
    rows = db_session.exec(
        select(TelegramMessage).where(TelegramMessage.email_draft_id == draft.id)
    ).all()
    assert len(rows) == 1


# ----------------- filtering ------------------------------------------------


def test_poll_once_skips_non_draft_created_state(db_session: Session) -> None:
    """GENERATED drafts are pre-Gmail; SENT / SUPERSEDED / FAILED are
    post-decision. None of them should be previewed.
    """
    _seed_draft(
        db_session,
        source_job_id="j-superseded",
        draft_state=EmailDraftState.SUPERSEDED,
    )
    _seed_draft(db_session, source_job_id="j-sent", draft_state=EmailDraftState.SENT)
    _seed_draft(db_session, source_job_id="j-failed", draft_state=EmailDraftState.FAILED)
    tg = _StubTg()
    sent = asyncio.run(poll_once(session=db_session, telegram=tg))
    assert sent == 0
    assert tg.calls == []


def test_poll_once_orders_by_score_desc(db_session: Session) -> None:
    """Higher-scored drafts get cards posted first -- if the bot is
    rate-limited or crashes mid-batch, the operator sees the best
    candidates first on the next cycle.
    """
    _seed_draft(db_session, source_job_id="j-low", score=5)
    _seed_draft(db_session, source_job_id="j-high", score=9)
    _seed_draft(db_session, source_job_id="j-mid", score=7)
    tg = _StubTg()
    asyncio.run(poll_once(session=db_session, telegram=tg))
    scores = [c.score for c in tg.calls]
    assert scores == [9, 7, 5]


# ----------------- error handling -------------------------------------------


def test_poll_once_tg_failure_does_not_persist_row(db_session: Session) -> None:
    """If Telegram send fails, we must NOT persist a TelegramMessage row
    -- otherwise the next poll cycle would skip this draft and the user
    never sees the card. Failures are logged and retried next tick.
    """
    draft = _seed_draft(db_session, source_job_id="j-tg-fail")
    tg = _StubTg(fail=True)
    sent = asyncio.run(poll_once(session=db_session, telegram=tg))
    assert sent == 0
    rows = db_session.exec(
        select(TelegramMessage).where(TelegramMessage.email_draft_id == draft.id)
    ).all()
    assert rows == []


# ----------------- empty queue ----------------------------------------------


def test_poll_once_empty_queue(db_session: Session) -> None:
    tg = _StubTg()
    sent = asyncio.run(poll_once(session=db_session, telegram=tg))
    assert sent == 0
    assert tg.calls == []
