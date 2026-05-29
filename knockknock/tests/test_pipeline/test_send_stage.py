"""Tests for SendStage -- the pipeline stage that turns APPROVED jobs into SENT.

SendStage is the second half of the DB-mediated approval flow: the
Telegram bot service flips ``job.status: AWAITING_APPROVAL -> APPROVED``
in response to a button tap; SendStage polls for APPROVED rows on the
next pipeline run and asks Gmail to actually send the previously-created
draft.

Why this lives in the pipeline (not the bot service): the bot service
has no Gmail credentials and no Gemini surface -- per Phase 9 brainstorm,
its threat model is "the worst an attacker who reaches the webhook can
do is flip job statuses". Concentrating Gmail in the cron pipeline keeps
the bot's blast radius minimal.

Per-row behaviour:

1. Load the active EmailDraft (``state=DRAFT_CREATED``,
   ``gmail_draft_id IS NOT NULL``) for the APPROVED job. If none exists,
   the job is moved to ERROR (auditable inconsistency -- the bot
   shouldn't have been able to approve without a draft).
2. Call ``gmail.send_draft(draft.gmail_draft_id)``. On success: stamp
   ``draft.sent_at = now()``, ``draft.state = SENT``, ``job.status =
   SENT``, audit event with the Gmail message_id in the payload.
3. On :class:`ExternalServiceError`: mark draft FAILED, leave job at
   APPROVED so the next run retries (the user already approved -- we
   don't want to make them re-approve a transient Gmail outage).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlmodel import Session, select

from knockknock.clients.gmail import GmailDraftSpec
from knockknock.db.enums import (
    CompanySizeBucket,
    EmailDraftState,
    JobSource,
    JobStatus,
)
from knockknock.db.models import (
    Company,
    EmailDraft,
    JobApplication,
    JobApplicationEvent,
)
from knockknock.exceptions import ExternalServiceError
from knockknock.pipeline.send import SendStage
from knockknock.pipeline.stage import StageContext


@dataclass
class _StubGmail:
    """Records sends; can be configured to raise.

    The real GmailClient is read-only for SendStage's purposes -- we
    only need ``send_draft(draft_id) -> message_id``. ``create_draft``
    is present on the Protocol shape only because production wiring
    reuses the same client object.
    """

    fail: bool = False
    sent: list[str] = field(default_factory=list)
    message_id: str = "msg_xyz"

    def send_draft(self, draft_id: str) -> str:
        if self.fail:
            raise ExternalServiceError("Gmail send broke")
        self.sent.append(draft_id)
        return self.message_id

    # SendStage doesn't call create_draft, but the production GmailClient
    # has it -- including it keeps the structural Protocol satisfied if
    # we ever widen the Protocol.
    def create_draft(self, spec: GmailDraftSpec) -> str:  # pragma: no cover
        return "unused"


def _seed_approved(
    db_session: Session,
    *,
    source_job_id: str = "j-send-1",
    draft_state: EmailDraftState = EmailDraftState.DRAFT_CREATED,
    gmail_draft_id: str | None = "gd_1",
) -> tuple[JobApplication, EmailDraft]:
    """Seed a Company + APPROVED job + DRAFT_CREATED EmailDraft.

    Mirrors what the bot service leaves behind: the user tapped Approve,
    so the job is at APPROVED, the draft is still DRAFT_CREATED (the bot
    doesn't transition the draft -- SendStage does, after Gmail accepts).
    """
    company = Company(
        name=f"Send-{source_job_id}",
        domain=f"send-{source_job_id}.test",
        size_bucket=CompanySizeBucket.SERIES_A,
    )
    db_session.add(company)
    db_session.flush()
    job = JobApplication(
        company_id=company.id,
        source=JobSource.HN,
        source_job_id=source_job_id,
        title="Backend",
        location="Bengaluru",
        apply_url=f"https://send-{source_job_id}.test/jobs/1",
        description="Build distributed things.",
        status=JobStatus.APPROVED,
        score=8,
        resume_variant_key="backend-distributed",
    )
    db_session.add(job)
    db_session.flush()
    draft = EmailDraft(
        job_id=job.id,
        subject="Senior Backend Engineer -- Nishant Gupta",
        body="Hi team,\n\nI'd love to chat.\n\nBest,\nNishant",
        to_recipients=["careers@send.test"],
        gmail_draft_id=gmail_draft_id,
        state=draft_state,
    )
    db_session.add(draft)
    db_session.flush()
    return job, draft


# ----------------- happy path: APPROVED -> SENT -----------------------------


def test_send_stage_moves_approved_to_sent(db_session: Session) -> None:
    """The hot path: an APPROVED job with a DRAFT_CREATED draft becomes
    SENT after Gmail accepts the send. Both the draft and the job
    transition in lockstep, and ``draft.sent_at`` is stamped.
    """
    job, draft = _seed_approved(db_session)
    gmail = _StubGmail(message_id="msg_alpha")
    stage = SendStage(session=db_session, gmail=gmail)

    result = stage.run(StageContext(run_id=1))

    assert result.stage == "send"
    assert result.processed == 1
    assert result.advanced == 1
    assert result.errors == 0
    # Gmail got the draft id
    assert gmail.sent == ["gd_1"]
    # State transitions
    db_session.refresh(job)
    db_session.refresh(draft)
    assert job.status == JobStatus.SENT
    assert draft.state == EmailDraftState.SENT
    assert draft.sent_at is not None
    assert draft.sent_at.tzinfo is not None  # tz-aware


def test_send_stage_writes_audit_event_with_message_id(db_session: Session) -> None:
    """Audit row: from_status=APPROVED -> to_status=SENT with the Gmail
    message_id in the payload so an operator can grep history for "what
    did we actually send for job X".
    """
    job, _ = _seed_approved(db_session, source_job_id="j-send-evt")
    gmail = _StubGmail(message_id="msg_beta")
    SendStage(session=db_session, gmail=gmail).run(StageContext(run_id=2))

    events = db_session.exec(
        select(JobApplicationEvent).where(JobApplicationEvent.job_id == job.id)
    ).all()
    sent_evt = next(
        (e for e in events if e.to_status == JobStatus.SENT),
        None,
    )
    assert sent_evt is not None
    assert sent_evt.from_status == JobStatus.APPROVED
    assert sent_evt.payload is not None
    assert sent_evt.payload.get("gmail_message_id") == "msg_beta"
    assert sent_evt.payload.get("run_id") == 2


# ----------------- multiple rows --------------------------------------------


def test_send_stage_processes_multiple_approved_rows(db_session: Session) -> None:
    job_a, _ = _seed_approved(db_session, source_job_id="j-multi-a")
    job_b, _ = _seed_approved(db_session, source_job_id="j-multi-b")
    gmail = _StubGmail()
    result = SendStage(session=db_session, gmail=gmail).run(StageContext(run_id=3))

    assert result.processed == 2
    assert result.advanced == 2
    db_session.refresh(job_a)
    db_session.refresh(job_b)
    assert job_a.status == JobStatus.SENT
    assert job_b.status == JobStatus.SENT


def test_send_stage_ignores_non_approved_jobs(db_session: Session) -> None:
    """Jobs in other statuses must not be touched. Seed an
    AWAITING_APPROVAL job alongside an APPROVED one; only the APPROVED
    one should move.
    """
    awaiting_job, _ = _seed_approved(db_session, source_job_id="j-await")
    awaiting_job.status = JobStatus.AWAITING_APPROVAL
    db_session.add(awaiting_job)
    db_session.flush()

    approved_job, _ = _seed_approved(db_session, source_job_id="j-approved-only")
    gmail = _StubGmail()
    result = SendStage(session=db_session, gmail=gmail).run(StageContext(run_id=4))

    assert result.processed == 1
    db_session.refresh(awaiting_job)
    db_session.refresh(approved_job)
    assert awaiting_job.status == JobStatus.AWAITING_APPROVAL
    assert approved_job.status == JobStatus.SENT


# ----------------- error paths ----------------------------------------------


def test_send_stage_gmail_failure_keeps_job_at_approved(db_session: Session) -> None:
    """Transient Gmail outage must not lose the user's approval. The
    draft is marked FAILED for visibility, the job stays at APPROVED
    so the next pipeline run retries.
    """
    job, draft = _seed_approved(db_session, source_job_id="j-gmail-fail")
    gmail = _StubGmail(fail=True)
    result = SendStage(session=db_session, gmail=gmail).run(StageContext(run_id=5))

    assert result.processed == 1
    assert result.advanced == 0
    assert result.errors == 1
    db_session.refresh(job)
    db_session.refresh(draft)
    # Job stays APPROVED so the next run re-picks it.
    assert job.status == JobStatus.APPROVED
    # Draft transitions to FAILED so the operator sees it in the digest.
    assert draft.state == EmailDraftState.FAILED
    assert job.last_error is not None
    assert "gmail" in job.last_error.lower()


def test_send_stage_missing_draft_marks_job_error(db_session: Session) -> None:
    """An APPROVED job with no DRAFT_CREATED draft is a corruption -- the
    bot service should not have been able to approve without one. Move
    to ERROR so the operator notices in the digest.
    """
    job, _draft = _seed_approved(
        db_session, source_job_id="j-no-draft", draft_state=EmailDraftState.SUPERSEDED
    )
    gmail = _StubGmail()
    result = SendStage(session=db_session, gmail=gmail).run(StageContext(run_id=6))

    assert result.processed == 1
    assert result.errors == 1
    db_session.refresh(job)
    assert job.status == JobStatus.ERROR
    assert job.last_error is not None
    # Gmail was never called -- nothing to send.
    assert gmail.sent == []


def test_send_stage_no_gmail_draft_id_marks_error(db_session: Session) -> None:
    """A DRAFT_CREATED row with no gmail_draft_id is a corruption (the
    draft stage should never create such a row). Same handling as the
    missing-draft case.
    """
    job, _draft = _seed_approved(db_session, source_job_id="j-no-gid", gmail_draft_id=None)
    gmail = _StubGmail()
    result = SendStage(session=db_session, gmail=gmail).run(StageContext(run_id=7))

    assert result.errors == 1
    db_session.refresh(job)
    assert job.status == JobStatus.ERROR
    assert gmail.sent == []


# ----------------- name + empty queue ---------------------------------------


def test_send_stage_empty_queue_is_a_noop(db_session: Session) -> None:
    """No APPROVED rows -> stage returns 0 counters, doesn't crash."""
    gmail = _StubGmail()
    result = SendStage(session=db_session, gmail=gmail).run(StageContext(run_id=8))
    assert result.processed == 0
    assert result.advanced == 0
    assert result.errors == 0
    assert gmail.sent == []


def test_send_stage_name() -> None:
    """Stage name must be ``send`` -- the runner / digest keys off it."""
    stage = SendStage(session=None, gmail=_StubGmail())  # type: ignore[arg-type]
    assert stage.name == "send"
