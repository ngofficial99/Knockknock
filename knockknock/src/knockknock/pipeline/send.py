"""Send stage: APPROVED -> SENT via Gmail.

The second half of the Phase 9 approval flow. The bot service flips
``job.status: AWAITING_APPROVAL -> APPROVED`` when the user taps the
inline "Approve" button; SendStage picks those rows up on the next
pipeline run, asks Gmail to send the previously-created draft, and
moves the job + draft to SENT.

Why this lives in the pipeline (not the bot service): the bot service
intentionally has no Gmail surface -- per the Phase 9 brainstorm, its
job is to flip DB rows and nothing else. Concentrating Gmail in the
cron pipeline keeps the bot's blast radius minimal (the worst an
attacker who reaches the webhook can do is flip job statuses).

Per-row behaviour:

1. Load the active EmailDraft (``state=DRAFT_CREATED``,
   ``gmail_draft_id IS NOT NULL``) for the APPROVED job. If absent,
   move the job to ERROR (auditable inconsistency -- the bot couldn't
   have approved without a draft).
2. Call ``gmail.send_draft(draft.gmail_draft_id)``. On success: stamp
   ``draft.sent_at = now()``, ``draft.state = SENT``, ``job.status =
   SENT``, audit event with the Gmail message_id in payload.
3. On :class:`ExternalServiceError`: mark the draft FAILED, leave the
   job at APPROVED so the next run retries. We don't want a transient
   Gmail outage to force the user to re-approve.

Drift from the original Phase 9 spec, intentional:

- ``EmailDraft`` has no ``gmail_message_id`` column; the spec assumed
  one. We persist the message_id in the audit event's JSONB payload
  instead, which is sufficient for "what did we actually send" forensics.
- Recipient assembly + body composition already happened in DraftStage
  -- SendStage just hands Gmail the draft id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import structlog
from sqlalchemy import asc
from sqlmodel import Session, select

from knockknock.clients.gmail import GmailDraftSpec
from knockknock.db.enums import EmailDraftState, JobStatus
from knockknock.db.models import EmailDraft, JobApplication, JobApplicationEvent
from knockknock.exceptions import ExternalServiceError
from knockknock.pipeline.stage import StageContext, StageResult

log = structlog.get_logger(__name__)


class _GmailLike(Protocol):
    """Minimal Gmail surface SendStage depends on.

    ``create_draft`` is here only to mirror the real GmailClient shape
    (production code reuses the same client across DraftStage +
    SendStage). SendStage itself only calls ``send_draft``.
    """

    def create_draft(self, spec: GmailDraftSpec) -> str: ...
    def send_draft(self, draft_id: str) -> str: ...


@dataclass
class SendStage:
    """Move ``APPROVED`` rows to ``SENT`` by sending their Gmail drafts."""

    session: Session
    gmail: _GmailLike
    name: str = field(default="send", init=False)

    def run(self, ctx: StageContext) -> StageResult:
        # Oldest first -- if the user approved A then B, send A first so
        # the recipient sees them in the order intended. ``id`` ASC is a
        # close-enough proxy for approval order since we don't index
        # ``decided_at`` (the audit event timestamps record the actual
        # tap order if anyone needs to dig).
        jobs = self.session.exec(
            select(JobApplication)
            .where(JobApplication.status == JobStatus.APPROVED)
            .order_by(asc(JobApplication.id))  # type: ignore[arg-type]
        ).all()

        processed = 0
        advanced = 0
        errors = 0

        for job in jobs:
            processed += 1

            # Find the active draft for this job. There can be at most
            # one DRAFT_CREATED row per job thanks to the partial unique
            # index ix_email_drafts_active_per_job.
            draft = self.session.exec(
                select(EmailDraft)
                .where(EmailDraft.job_id == job.id)
                .where(EmailDraft.state == EmailDraftState.DRAFT_CREATED)
            ).first()

            if draft is None:
                _mark_job_error(
                    self.session,
                    job=job,
                    message=(
                        "no active DRAFT_CREATED row for APPROVED job; "
                        "bot service approved without a draft?"
                    ),
                    run_id=ctx.run_id,
                )
                errors += 1
                continue

            if not draft.gmail_draft_id:
                _mark_job_error(
                    self.session,
                    job=job,
                    message=("DRAFT_CREATED row missing gmail_draft_id; draft stage corruption?"),
                    run_id=ctx.run_id,
                )
                errors += 1
                continue

            try:
                message_id = self.gmail.send_draft(draft.gmail_draft_id)
            except ExternalServiceError as exc:
                # Transient Gmail outage: mark the draft FAILED for
                # visibility, but leave the job at APPROVED so the next
                # run retries. The user already approved -- we don't
                # want to force them to re-approve a flake.
                draft.state = EmailDraftState.FAILED
                self.session.add(draft)
                job.last_error = f"gmail send failed: {exc}"
                job.retry_count = (job.retry_count or 0) + 1
                self.session.add(job)
                self.session.flush()
                errors += 1
                log.warning(
                    "send.gmail_failed",
                    job_id=job.id,
                    draft_id=draft.id,
                    error=str(exc),
                    run_id=ctx.run_id,
                )
                continue

            # Happy path: stamp + transition + audit.
            now = datetime.now(UTC)
            draft.state = EmailDraftState.SENT
            draft.sent_at = now
            self.session.add(draft)

            prev_status = job.status
            job.status = JobStatus.SENT
            job.last_error = None
            self.session.add(job)

            self.session.add(
                JobApplicationEvent(
                    job_id=job.id,
                    from_status=prev_status,
                    to_status=JobStatus.SENT,
                    note="sent via gmail",
                    payload={
                        "run_id": ctx.run_id,
                        "gmail_draft_id": draft.gmail_draft_id,
                        "gmail_message_id": message_id,
                    },
                )
            )
            self.session.flush()
            advanced += 1
            log.info(
                "send.sent",
                job_id=job.id,
                draft_id=draft.id,
                gmail_message_id=message_id,
                run_id=ctx.run_id,
            )

        log.info(
            "send.summary",
            processed=processed,
            advanced=advanced,
            errors=errors,
            run_id=ctx.run_id,
        )
        return StageResult(
            stage=self.name,
            processed=processed,
            advanced=advanced,
            rejected=0,
            errors=errors,
        )


def _mark_job_error(
    session: Session,
    *,
    job: JobApplication,
    message: str,
    run_id: int,
) -> None:
    """Move a job to ERROR with ``last_error`` + ``retry_count`` bump and
    an audit event. Mirrors ``draft._mark_job_error`` to keep error-row
    shapes consistent across stages.
    """
    prev_status = job.status
    job.last_error = message
    job.retry_count = (job.retry_count or 0) + 1
    job.status = JobStatus.ERROR
    session.add(job)
    session.add(
        JobApplicationEvent(
            job_id=job.id,
            from_status=prev_status,
            to_status=JobStatus.ERROR,
            note=message[:500],
            payload={"run_id": run_id},
        )
    )
    session.flush()
