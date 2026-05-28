"""Integration tests for :class:`DraftStage`.

Task 8.3 is the integration point of Phase 8: Gemini Pro + limiter +
prompt + validator + Gmail + persistence. Tests stub the Gemini client
and Gmail client (no network) but use a real DB so the unique-active-
draft constraint, JSONB recipient arrays, and the
``GeminiCallLog``/``JobApplicationEvent`` audit-trail rows are
exercised end-to-end.

Contract drift from the original Phase 8 spec, intentional:

- ``EmailDraft.body`` is a single field (no body_text/body_html), so the
  Pro response shape is ``{"subject", "body"}`` and the draft row carries
  a single ``body``.
- Recipients are JSONB arrays (``to_recipients: list[str]``,
  ``cc_recipients: list[str] | None``) matching the DB columns.
- The ``EmailDraft`` row has NO ``phonebook_id`` / ``resume_variant_key``
  / ``failure_reason`` columns; the spec's references to those were
  spec-side drafts that the Phase 1 schema dropped.
- ``GeminiCallLog`` uses ``job_id`` / ``prompt_tokens`` /
  ``completion_tokens`` / ``error_code`` (not ``job_application_id`` /
  ``tokens_input`` / ``tokens_output`` / ``error_class``).
- ``JobApplicationEvent`` uses ``job_id`` / ``payload`` / ``note`` (not
  ``job_application_id`` / ``pipeline_run_id`` / ``stage`` / ``detail``).
- ``DraftStage.run`` takes a ``StageContext`` (matching the existing
  ``Stage`` protocol used by ``ScoreStage`` and ``TailorStage``), not
  ``session`` + ``run_id`` as separate kwargs.
- ``DraftState`` is ``EmailDraftState`` (actual enum name).
- ``prefs.candidate.name`` (not ``full_name``).
- Gmail signature attached server-side, so the validator forbids any
  email in the body and the prompt forbids a sign-off block.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, select

from knockknock.clients.gemini import GeminiResponse
from knockknock.clients.gmail import GmailDraftSpec
from knockknock.config.preferences import JobPreferences
from knockknock.db.enums import (
    CompanySizeBucket,
    EmailDraftState,
    GeminiModel,
    GeminiPurpose,
    JobSource,
    JobStatus,
    PhonebookSource,
)
from knockknock.db.models import (
    Company,
    EmailDraft,
    GeminiCallLog,
    JobApplication,
    JobApplicationEvent,
    PhonebookEntry,
)
from knockknock.exceptions import ExternalServiceError, RpdExhaustedError
from knockknock.pipeline.draft import DraftStage
from knockknock.pipeline.stage import StageContext

_counter = {"n": 0}


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


@dataclass
class _StubGemini:
    """Programmable Pro stub. Returns the next response from a queue."""

    responses: list[str]
    tokens_in: int = 120
    tokens_out: int = 220
    calls: list[dict[str, Any]] = field(default_factory=list)

    def generate(
        self,
        *,
        model: GeminiModel,
        system_instruction: str,
        user_prompt: str,
        response_mime_type: str,
    ) -> GeminiResponse:
        idx = len(self.calls)
        self.calls.append(
            {
                "model": model,
                "system_instruction": system_instruction,
                "user_prompt": user_prompt,
                "response_mime_type": response_mime_type,
            }
        )
        if idx >= len(self.responses):
            raise AssertionError(f"_StubGemini ran out of responses (call {idx + 1})")
        return GeminiResponse(
            text=self.responses[idx],
            tokens_input=self.tokens_in,
            tokens_output=self.tokens_out,
        )


@dataclass
class _NoLimit:
    """Always-permissive limiter for the happy paths."""

    def check(self, model: GeminiModel, purpose: GeminiPurpose) -> None:
        return None

    def compute_rpm_wait_seconds(self, model: GeminiModel) -> float | None:
        return None


@dataclass
class _HaltingLimiter:
    """Pre-check raises immediately so the stage never reaches Pro."""

    def check(self, model: GeminiModel, purpose: GeminiPurpose) -> None:
        raise RpdExhaustedError("pro exhausted")

    def compute_rpm_wait_seconds(self, model: GeminiModel) -> float | None:
        return None


@dataclass
class _StubGmail:
    """Recording Gmail stub. Captures the GmailDraftSpec for assertions."""

    draft_calls: list[GmailDraftSpec] = field(default_factory=list)
    fail_create: bool = False

    def create_draft(self, spec: GmailDraftSpec) -> str:
        if self.fail_create:
            raise ExternalServiceError("Gmail draft create failed: boom")
        self.draft_calls.append(spec)
        return f"draft_{len(self.draft_calls)}"

    def send_draft(self, draft_id: str) -> str:  # pragma: no cover - protocol parity
        return f"msg_{draft_id}"


def _load_pdf_stub(_variant_key: str) -> bytes:
    return b"%PDF-1.4 stub bytes"


def _load_pdf_missing(variant_key: str) -> bytes:
    raise FileNotFoundError(f"resumes/{variant_key}.pdf")


# ---------------------------------------------------------------------------
# Fixtures (DB seed helpers)
# ---------------------------------------------------------------------------


def _seed_company(session: Session, *, suffix: int | None = None) -> Company:
    if suffix is None:
        _counter["n"] += 1
        suffix = _counter["n"]
    company = Company(
        name=f"Acme{suffix}",
        domain=f"acme{suffix}.test",
        size_bucket=CompanySizeBucket.SERIES_A,
    )
    session.add(company)
    session.flush()
    return company


def _seed_phonebook_with_founder(session: Session, company: Company) -> PhonebookEntry:
    assert company.id is not None
    entry = PhonebookEntry(
        company_id=company.id,
        founder_name="Aarav Singh",
        founder_email=f"aarav@{company.domain}",
        careers_email=f"careers@{company.domain}",
        source=PhonebookSource.APOLLO,
        confidence=80,
    )
    session.add(entry)
    session.flush()
    return entry


def _seed_phonebook_careers_only(session: Session, company: Company) -> PhonebookEntry:
    assert company.id is not None
    entry = PhonebookEntry(
        company_id=company.id,
        founder_name=None,
        founder_email=None,
        careers_email=f"careers@{company.domain}",
        source=PhonebookSource.SEED,
        confidence=20,
    )
    session.add(entry)
    session.flush()
    return entry


def _seed_tailored_job(
    session: Session,
    company: Company,
    *,
    score: int = 8,
    discovered_at: datetime | None = None,
    title: str = "Senior Backend Engineer",
) -> JobApplication:
    _counter["n"] += 1
    suffix = _counter["n"]
    assert company.id is not None
    job = JobApplication(
        company_id=company.id,
        source=JobSource.HN,
        source_job_id=f"hn-{suffix}",
        title=title,
        location="Bengaluru",
        apply_url=f"https://{company.domain}/jobs/{suffix}",
        description="Build distributed Python services on Kubernetes with Kafka.",
        status=JobStatus.TAILORED,
        score=score,
        resume_variant_key="backend-distributed",
        discovered_at=discovered_at or datetime.now(UTC),
    )
    session.add(job)
    session.flush()
    return job


def _prefs() -> JobPreferences:
    """Minimal valid prefs that satisfy ``DraftStage``'s prompt-build path."""
    return JobPreferences.model_validate(
        {
            "candidate": {
                "name": "Nishant Gupta",
                "email": "ngofficial99@gmail.com",
                "current_role": "Software Engineer at Zeotap",
                "years_experience": 2,
                "location": "Bengaluru, India",
                "pitch": "Backend engineer on Zeotap's Platform team.",
            },
            "target": {
                "locations": ["Bengaluru"],
                "titles_allow": ["Backend"],
                "titles_deny": ["Manager"],
                "seniority_allow": ["Mid-level", "Senior"],
                "company_size_allow": ["SEED", "SERIES_A"],
            },
            "skills": {"must_have_any": ["Python"], "nice_to_have": ["Kafka"]},
            "scoring": {
                "min_score_to_draft": 70,
                "weight_skill_match": 40,
                "weight_seniority_match": 25,
                "weight_location_match": 20,
                "weight_company_stage": 15,
            },
            "limits": {
                "daily_drafts_cap": 1,
                "hourly_discover_cap": 1,
                "gemini_pro_rpd_ceiling": 500,
            },
            "sources": {
                "hn": {"enabled": True, "months_lookback": 1},
                "wellfound": {"enabled": False, "query": ""},
                "yc_waas": {"enabled": False, "query": ""},
                "greenhouse": {"enabled": False, "boards": []},
                "lever": {"enabled": False, "boards": []},
                "ashby": {"enabled": False, "boards": []},
            },
        }
    )


# ---------------------------------------------------------------------------
# Pro response fixtures
# ---------------------------------------------------------------------------


# >200 chars, no emails in body, no sign-off, mentions "Aarav" for greeting.
_GOOD_DRAFT_JSON = (
    '{"subject":"Backend role at Acme","body":"Hi Aarav,\\n\\n'
    "I came across the Senior Backend Engineer role and the work on distributed Python "
    "services lines up well with what I do at Zeotap on reactive Vert.x pipelines and "
    "FastAPI ingestion services.\\n\\n"
    "Most recently I cut p99 latency on a multi-tenant ingestion service from 850ms to "
    "90ms by introducing a request-coalescing layer. Resume attached -- would love to "
    'chat about the platform team."}'
)

# Bracketed placeholders → validator rejects. Body is >200 chars so the
# only validator rule that fires is the placeholder check.
_BAD_DRAFT_JSON_PLACEHOLDER = (
    '{"subject":"Backend role at [Company]","body":"Hi [Recipient],\\n\\n'
    + (
        "I would love to discuss the role and what your team is building at "
        "distributed systems and Vert.x microservices. "
    )
    * 4
    + '"}'
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_draft_stage_happy_path_creates_gmail_draft_and_advances_job(
    db_session: Session,
) -> None:
    company = _seed_company(db_session)
    _seed_phonebook_with_founder(db_session, company)
    job = _seed_tailored_job(db_session, company)

    gemini = _StubGemini(responses=[_GOOD_DRAFT_JSON])
    gmail = _StubGmail()
    stage = DraftStage(
        session=db_session,
        prefs=_prefs(),
        gemini=gemini,
        limiter=_NoLimit(),
        gmail=gmail,
        load_resume_pdf=_load_pdf_stub,
    )

    result = stage.run(StageContext(run_id=1))

    db_session.refresh(job)
    assert job.status is JobStatus.AWAITING_APPROVAL
    assert job.last_error is None
    assert result.stage == "draft"
    assert result.advanced == 1
    assert result.errors == 0
    assert result.halted_reason is None
    assert len(gemini.calls) == 1

    # EmailDraft row persisted with DRAFT_CREATED state.
    draft = db_session.exec(select(EmailDraft).where(EmailDraft.job_id == job.id)).one()
    assert draft.state is EmailDraftState.DRAFT_CREATED
    assert draft.gmail_draft_id == "draft_1"
    assert draft.to_recipients == [f"aarav@{company.domain}"]
    assert draft.cc_recipients == [f"careers@{company.domain}"]
    assert draft.subject == "Backend role at Acme"
    assert "Aarav" in draft.body
    assert "ngofficial99@gmail.com" not in draft.body  # Gmail signature handles it

    # GmailDraftSpec carries the same recipients + the PDF attachment.
    assert len(gmail.draft_calls) == 1
    spec = gmail.draft_calls[0]
    assert spec.to_emails == [f"aarav@{company.domain}"]
    assert spec.cc_emails == [f"careers@{company.domain}"]
    assert spec.subject == "Backend role at Acme"
    assert spec.attachments == [
        (
            "backend-distributed.pdf",
            b"%PDF-1.4 stub bytes",
            "application/pdf",
        )
    ]


def test_draft_stage_writes_success_call_log(db_session: Session) -> None:
    company = _seed_company(db_session)
    _seed_phonebook_with_founder(db_session, company)
    job = _seed_tailored_job(db_session, company)

    stage = DraftStage(
        session=db_session,
        prefs=_prefs(),
        gemini=_StubGemini(responses=[_GOOD_DRAFT_JSON]),
        limiter=_NoLimit(),
        gmail=_StubGmail(),
        load_resume_pdf=_load_pdf_stub,
    )
    stage.run(StageContext(run_id=42))

    log = db_session.exec(select(GeminiCallLog).where(GeminiCallLog.job_id == job.id)).one()
    assert log.success is True
    assert log.model is GeminiModel.PRO_2_5
    assert log.purpose is GeminiPurpose.DRAFT_EMAIL
    assert log.prompt_tokens == 120
    assert log.completion_tokens == 220
    assert log.error_code is None


def test_draft_stage_writes_audit_event(db_session: Session) -> None:
    company = _seed_company(db_session)
    _seed_phonebook_with_founder(db_session, company)
    job = _seed_tailored_job(db_session, company)

    DraftStage(
        session=db_session,
        prefs=_prefs(),
        gemini=_StubGemini(responses=[_GOOD_DRAFT_JSON]),
        limiter=_NoLimit(),
        gmail=_StubGmail(),
        load_resume_pdf=_load_pdf_stub,
    ).run(StageContext(run_id=7))

    event = db_session.exec(
        select(JobApplicationEvent).where(JobApplicationEvent.job_id == job.id)
    ).one()
    assert event.from_status is JobStatus.TAILORED
    assert event.to_status is JobStatus.AWAITING_APPROVAL
    assert event.payload is not None
    assert event.payload["run_id"] == 7
    assert event.payload["variant_key"] == "backend-distributed"


def test_draft_stage_regenerates_after_validator_failure(db_session: Session) -> None:
    """Bad first attempt → one regenerate call → good → DRAFT_CREATED."""
    company = _seed_company(db_session)
    _seed_phonebook_with_founder(db_session, company)
    job = _seed_tailored_job(db_session, company)

    gemini = _StubGemini(responses=[_BAD_DRAFT_JSON_PLACEHOLDER, _GOOD_DRAFT_JSON])
    stage = DraftStage(
        session=db_session,
        prefs=_prefs(),
        gemini=gemini,
        limiter=_NoLimit(),
        gmail=_StubGmail(),
        load_resume_pdf=_load_pdf_stub,
    )
    result = stage.run(StageContext(run_id=1))

    db_session.refresh(job)
    assert job.status is JobStatus.AWAITING_APPROVAL
    assert len(gemini.calls) == 2
    assert result.advanced == 1
    assert result.errors == 0
    # Second call must use REGENERATE purpose (logged that way).
    logs = db_session.exec(
        select(GeminiCallLog).where(GeminiCallLog.job_id == job.id).order_by(GeminiCallLog.id)  # type: ignore[arg-type]
    ).all()
    assert len(logs) == 2
    assert logs[0].purpose is GeminiPurpose.DRAFT_EMAIL
    assert logs[0].success is False
    assert logs[0].error_code == "ValidationError"
    assert logs[1].purpose is GeminiPurpose.REGENERATE
    assert logs[1].success is True


def test_draft_stage_marks_job_error_on_second_validator_failure(db_session: Session) -> None:
    """Two bad attempts → job ERROR, last_error set, no email_drafts row."""
    company = _seed_company(db_session)
    _seed_phonebook_with_founder(db_session, company)
    job = _seed_tailored_job(db_session, company)

    gemini = _StubGemini(responses=[_BAD_DRAFT_JSON_PLACEHOLDER, _BAD_DRAFT_JSON_PLACEHOLDER])
    stage = DraftStage(
        session=db_session,
        prefs=_prefs(),
        gemini=gemini,
        limiter=_NoLimit(),
        gmail=_StubGmail(),
        load_resume_pdf=_load_pdf_stub,
    )
    result = stage.run(StageContext(run_id=1))

    db_session.refresh(job)
    assert job.status is JobStatus.ERROR
    assert job.last_error is not None
    assert "placeholder" in job.last_error.lower()
    assert job.retry_count == 1
    assert result.errors == 1
    assert result.advanced == 0
    # No persisted draft row (both attempts failed before Gmail).
    drafts = db_session.exec(select(EmailDraft).where(EmailDraft.job_id == job.id)).all()
    assert drafts == []
    # Two failed call logs.
    logs = db_session.exec(select(GeminiCallLog).where(GeminiCallLog.job_id == job.id)).all()
    assert len(logs) == 2
    assert all(log.success is False for log in logs)


def test_draft_stage_falls_back_to_careers_email_when_no_founder(db_session: Session) -> None:
    """No founder → To=[careers], Cc=None, greeting check skipped."""
    company = _seed_company(db_session)
    _seed_phonebook_careers_only(db_session, company)
    job = _seed_tailored_job(db_session, company)

    # Use a greeting-neutral body for the no-founder path: validator skips
    # the recipient_first_name check when it's empty.
    neutral_body = (
        '{"subject":"Backend role at Acme","body":"Hi Acme team,\\n\\n'
        "I came across the Senior Backend Engineer role and the work on distributed "
        "Python services lines up well with what I do at Zeotap on reactive Vert.x "
        "pipelines and FastAPI ingestion services.\\n\\n"
        "Resume attached -- would love to chat about the platform team and the "
        'ingestion work."}'
    )
    gmail = _StubGmail()
    stage = DraftStage(
        session=db_session,
        prefs=_prefs(),
        gemini=_StubGemini(responses=[neutral_body]),
        limiter=_NoLimit(),
        gmail=gmail,
        load_resume_pdf=_load_pdf_stub,
    )
    stage.run(StageContext(run_id=1))

    db_session.refresh(job)
    assert job.status is JobStatus.AWAITING_APPROVAL
    draft = db_session.exec(select(EmailDraft).where(EmailDraft.job_id == job.id)).one()
    assert draft.to_recipients == [f"careers@{company.domain}"]
    assert draft.cc_recipients is None
    # GmailDraftSpec mirrors the same recipient assembly.
    spec = gmail.draft_calls[0]
    assert spec.to_emails == [f"careers@{company.domain}"]
    assert spec.cc_emails is None


def test_draft_stage_halts_on_rpd_exhausted(db_session: Session) -> None:
    """RpdExhaustedError on pre-check → halt, no Pro call, halted_reason set."""
    company = _seed_company(db_session)
    _seed_phonebook_with_founder(db_session, company)
    _seed_tailored_job(db_session, company)

    gemini = _StubGemini(responses=[])
    stage = DraftStage(
        session=db_session,
        prefs=_prefs(),
        gemini=gemini,
        limiter=_HaltingLimiter(),
        gmail=_StubGmail(),
        load_resume_pdf=_load_pdf_stub,
    )
    result = stage.run(StageContext(run_id=1))

    assert gemini.calls == []
    assert result.advanced == 0
    assert result.errors == 0
    assert result.halted_reason == "rpd_exhausted"


def test_draft_stage_handles_gmail_failure(db_session: Session) -> None:
    """Gmail draft create raises → EmailDraft saved as FAILED, job stays TAILORED."""
    company = _seed_company(db_session)
    _seed_phonebook_with_founder(db_session, company)
    job = _seed_tailored_job(db_session, company)

    gmail = _StubGmail(fail_create=True)
    stage = DraftStage(
        session=db_session,
        prefs=_prefs(),
        gemini=_StubGemini(responses=[_GOOD_DRAFT_JSON]),
        limiter=_NoLimit(),
        gmail=gmail,
        load_resume_pdf=_load_pdf_stub,
    )
    result = stage.run(StageContext(run_id=1))

    db_session.refresh(job)
    assert job.status is JobStatus.TAILORED  # left for retry
    assert job.last_error is not None
    assert "gmail" in job.last_error.lower()
    assert job.retry_count == 1
    assert result.errors == 1
    assert result.advanced == 0

    draft = db_session.exec(select(EmailDraft).where(EmailDraft.job_id == job.id)).one()
    assert draft.state is EmailDraftState.FAILED
    assert draft.gmail_draft_id is None


def test_draft_stage_marks_error_when_resume_pdf_missing(db_session: Session) -> None:
    """Missing PDF on disk → job ERROR with last_error set to resume_pdf_missing."""
    company = _seed_company(db_session)
    _seed_phonebook_with_founder(db_session, company)
    job = _seed_tailored_job(db_session, company)

    stage = DraftStage(
        session=db_session,
        prefs=_prefs(),
        gemini=_StubGemini(responses=[_GOOD_DRAFT_JSON]),
        limiter=_NoLimit(),
        gmail=_StubGmail(),
        load_resume_pdf=_load_pdf_missing,
    )
    result = stage.run(StageContext(run_id=1))

    db_session.refresh(job)
    assert job.status is JobStatus.ERROR
    assert job.last_error is not None
    assert "resume_pdf_missing" in job.last_error
    assert result.errors == 1


def test_draft_stage_processes_in_score_desc_order(db_session: Session) -> None:
    """Higher score is processed first; verify via JobApplicationEvent.id ordering."""
    now = datetime.now(UTC)
    company_lo = _seed_company(db_session)
    _seed_phonebook_with_founder(db_session, company_lo)
    low = _seed_tailored_job(db_session, company_lo, score=6, discovered_at=now)

    company_hi = _seed_company(db_session)
    _seed_phonebook_with_founder(db_session, company_hi)
    high = _seed_tailored_job(db_session, company_hi, score=9, discovered_at=now)

    DraftStage(
        session=db_session,
        prefs=_prefs(),
        gemini=_StubGemini(responses=[_GOOD_DRAFT_JSON, _GOOD_DRAFT_JSON]),
        limiter=_NoLimit(),
        gmail=_StubGmail(),
        load_resume_pdf=_load_pdf_stub,
    ).run(StageContext(run_id=1))

    events = db_session.exec(
        select(JobApplicationEvent)
        .where(JobApplicationEvent.to_status == JobStatus.AWAITING_APPROVAL)
        .order_by(JobApplicationEvent.id)  # type: ignore[arg-type]
    ).all()
    assert len(events) == 2
    assert events[0].job_id == high.id
    assert events[1].job_id == low.id


def test_draft_stage_skips_non_tailored_jobs(db_session: Session) -> None:
    """ENRICHED / AWAITING_APPROVAL etc. are untouched -- only TAILORED advances."""
    company = _seed_company(db_session)
    _seed_phonebook_with_founder(db_session, company)
    job = _seed_tailored_job(db_session, company)
    job.status = JobStatus.ENRICHED
    db_session.add(job)
    db_session.flush()

    stage = DraftStage(
        session=db_session,
        prefs=_prefs(),
        gemini=_StubGemini(responses=[_GOOD_DRAFT_JSON]),
        limiter=_NoLimit(),
        gmail=_StubGmail(),
        load_resume_pdf=_load_pdf_stub,
    )
    result = stage.run(StageContext(run_id=1))

    assert result.advanced == 0
    assert result.errors == 0
    db_session.refresh(job)
    assert job.status is JobStatus.ENRICHED
