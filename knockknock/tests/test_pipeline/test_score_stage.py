"""Unit tests for :class:`ScoreStage`.

The stage is the integration point of Phase 5: Gemini client + limiter +
prompt + parser + persistence. Tests stub the Gemini client (so no
network) and use a real :class:`GeminiLimiter` against the test DB so
quota math is exercised end-to-end. Two halted-on-quota cases use a
hand-rolled limiter stub to make the failure mode explicit.

Threshold math: ``prefs.scoring.min_score_to_draft`` is on a 0-100 scale
(matches the YAML config we ship), but Gemini scores are 1-10. We test
both score >= ceil(70/10)=7 (SCORED) and score < 7 (SCORE_REJECTED).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, select

from knockknock.clients.gemini import GeminiResponse
from knockknock.config.preferences import JobPreferences
from knockknock.db.enums import (
    CompanySizeBucket,
    GeminiModel,
    GeminiPurpose,
    JobSource,
    JobStatus,
    RejectionReason,
)
from knockknock.db.models import (
    Company,
    GeminiCallLog,
    JobApplication,
    JobApplicationEvent,
)
from knockknock.exceptions import RpdExhaustedError
from knockknock.pipeline.score import ScoreStage
from knockknock.pipeline.stage import StageContext
from knockknock.rate_limit.gemini_limiter import (
    GeminiLimiter,
    LimiterConfig,
    ModelQuota,
)


@dataclass
class _StubGemini:
    """Programmable Gemini client stub.

    Records every call so tests can assert prompt-shape invariants
    (model, system_instruction, response_mime_type) without coupling to
    template wording.
    """

    text: str
    tokens_in: int = 100
    tokens_out: int = 20
    calls: list[dict[str, Any]] = field(default_factory=list)

    def generate(
        self,
        *,
        model: GeminiModel,
        system_instruction: str,
        user_prompt: str,
        response_mime_type: str,
    ) -> GeminiResponse:
        self.calls.append(
            {
                "model": model,
                "system_instruction": system_instruction,
                "user_prompt": user_prompt,
                "response_mime_type": response_mime_type,
            }
        )
        return GeminiResponse(
            text=self.text, tokens_input=self.tokens_in, tokens_output=self.tokens_out
        )


def _prefs() -> JobPreferences:
    return JobPreferences.model_validate(
        {
            "candidate": {
                "name": "Test User",
                "email": "test.user@example.com",
                "current_role": "Backend Engineer",
                "years_experience": 4,
                "location": "Bengaluru",
            },
            "target": {
                "locations": ["Bengaluru"],
                "titles_allow": ["Engineer"],
                "titles_deny": ["Manager"],
                "seniority_allow": ["Senior"],
                "company_size_allow": ["SEED", "SERIES_A"],
            },
            "skills": {"must_have_any": ["Python"], "nice_to_have": ["Kafka"]},
            "scoring": {
                # Threshold = ceil(70/10) = 7 on the Gemini 1-10 scale.
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
                "wellfound": {"enabled": False},
                "yc_waas": {"enabled": False},
                "greenhouse": {"enabled": False},
                "lever": {"enabled": False},
                "ashby": {"enabled": False},
            },
        }
    )


def _limiter(session: Session) -> GeminiLimiter:
    return GeminiLimiter(
        session,
        LimiterConfig(
            safety_ceiling_rpd=500,
            quotas={
                # rpm=999 means compute_rpm_wait_seconds never triggers a sleep.
                GeminiModel.FLASH_2_5: ModelQuota(rpm=999, tpm=10_000_000, rpd=1500),
                GeminiModel.PRO_2_5: ModelQuota(rpm=2, tpm=32_000, rpd=50),
            },
        ),
    )


_seed_counter = {"n": 0}


def _seed_job(session: Session, *, title: str = "Senior Backend Engineer") -> int:
    """Insert a fresh PRE_FILTERED job. Each call gets a unique company/domain
    so the ``companies_domain_unique`` constraint doesn't bite when a single
    test seeds multiple jobs."""
    _seed_counter["n"] += 1
    suffix = _seed_counter["n"]
    company = Company(
        name=f"Acme{suffix}",
        domain=f"acme{suffix}.test",
        size_bucket=CompanySizeBucket.SERIES_A,
    )
    session.add(company)
    session.flush()
    assert company.id is not None
    job = JobApplication(
        company_id=company.id,
        source=JobSource.HN,
        source_job_id=f"hn-{suffix}",
        title=title,
        location="Bengaluru",
        apply_url=f"https://acme{suffix}.test/jobs/1",
        description="Build distributed Python services on AWS with Kafka.",
        status=JobStatus.PRE_FILTERED,
        discovered_at=datetime.now(UTC),
    )
    session.add(job)
    session.flush()
    assert job.id is not None
    return job.id


_HIGH_SCORE_JSON = (
    '{"score": 8, "rationale": "great fit", '
    '"matched_must_have": ["Python"], "matched_nice_to_have": ["Kafka"], '
    '"seniority_match": true, "company_stage_match": true}'
)
_LOW_SCORE_JSON = (
    '{"score": 3, "rationale": "weak match", '
    '"matched_must_have": [], "matched_nice_to_have": [], '
    '"seniority_match": false, "company_stage_match": true}'
)


def test_score_stage_advances_high_score(db_session: Session) -> None:
    job_id = _seed_job(db_session)
    gemini = _StubGemini(text=_HIGH_SCORE_JSON)
    stage = ScoreStage(
        session=db_session,
        prefs=_prefs(),
        gemini=gemini,
        limiter=_limiter(db_session),
    )
    result = stage.run(StageContext(run_id=1))

    job = db_session.get(JobApplication, job_id)
    assert job is not None
    assert job.status == JobStatus.SCORED
    assert job.score == 8
    assert job.score_rationale == "great fit"
    assert result.advanced == 1
    assert result.rejected == 0
    assert result.processed == 1
    assert result.halted_reason is None

    # GeminiCallLog row written, success=True, correct model/purpose.
    log = db_session.exec(select(GeminiCallLog)).one()
    assert log.success is True
    assert log.model == GeminiModel.FLASH_2_5
    assert log.purpose == GeminiPurpose.SCORE
    assert log.job_id == job_id
    assert log.prompt_tokens == 100
    assert log.completion_tokens == 20

    # Event row written with the verdict in the payload.
    event = db_session.exec(
        select(JobApplicationEvent).where(JobApplicationEvent.job_id == job_id)
    ).one()
    assert event.to_status == JobStatus.SCORED
    assert event.from_status == JobStatus.PRE_FILTERED


def test_score_stage_rejects_low_score(db_session: Session) -> None:
    job_id = _seed_job(db_session)
    gemini = _StubGemini(text=_LOW_SCORE_JSON)
    stage = ScoreStage(
        session=db_session,
        prefs=_prefs(),
        gemini=gemini,
        limiter=_limiter(db_session),
    )
    result = stage.run(StageContext(run_id=1))

    job = db_session.get(JobApplication, job_id)
    assert job is not None
    assert job.status == JobStatus.SCORE_REJECTED
    assert job.score == 3
    assert job.rejection_reason == RejectionReason.SCORE_LOW
    assert result.rejected == 1
    assert result.advanced == 0


def test_score_stage_records_failed_call_on_parser_error(db_session: Session) -> None:
    """Non-JSON response: job stays PRE_FILTERED, ValueError captured on log."""
    job_id = _seed_job(db_session)
    gemini = _StubGemini(text="not-json")
    stage = ScoreStage(
        session=db_session,
        prefs=_prefs(),
        gemini=gemini,
        limiter=_limiter(db_session),
    )
    result = stage.run(StageContext(run_id=1))

    job = db_session.get(JobApplication, job_id)
    assert job is not None
    # Stays PRE_FILTERED so it can be retried on the next run.
    assert job.status == JobStatus.PRE_FILTERED
    assert job.score is None
    assert result.errors == 1
    assert result.advanced == 0
    assert result.rejected == 0

    log = db_session.exec(select(GeminiCallLog)).one()
    assert log.success is False
    assert log.error_code == "ValueError"


def test_score_stage_halts_on_rate_limit(db_session: Session) -> None:
    """RpdExhaustedError on pre-check → halt, no Gemini call, halted_reason set."""
    _seed_job(db_session, title="Job A")
    _seed_job(db_session, title="Job B")

    @dataclass
    class _HaltingLimiter:
        def check(self, model: GeminiModel, purpose: GeminiPurpose) -> None:
            raise RpdExhaustedError("flash exhausted")

        def compute_rpm_wait_seconds(self, model: GeminiModel) -> float | None:
            return None

    gemini = _StubGemini(text=_HIGH_SCORE_JSON)
    stage = ScoreStage(
        session=db_session,
        prefs=_prefs(),
        gemini=gemini,
        limiter=_HaltingLimiter(),
    )
    result = stage.run(StageContext(run_id=1))

    assert gemini.calls == []  # never called
    assert result.advanced == 0
    assert result.rejected == 0
    assert result.halted_reason == "rate_limited"
    assert result.processed == 0


def test_score_stage_only_processes_pre_filtered_jobs(db_session: Session) -> None:
    """Jobs in other statuses are ignored (idempotency)."""
    # One PRE_FILTERED, one DISCOVERED, one already SCORED.
    pre_id = _seed_job(db_session, title="Pre-filtered")
    other_id = _seed_job(db_session, title="Discovered")
    db_session.get(JobApplication, other_id).status = JobStatus.DISCOVERED  # type: ignore[union-attr]
    already_id = _seed_job(db_session, title="Already scored")
    db_session.get(JobApplication, already_id).status = JobStatus.SCORED  # type: ignore[union-attr]
    db_session.flush()

    gemini = _StubGemini(text=_HIGH_SCORE_JSON)
    stage = ScoreStage(
        session=db_session,
        prefs=_prefs(),
        gemini=gemini,
        limiter=_limiter(db_session),
    )
    result = stage.run(StageContext(run_id=1))

    assert result.processed == 1
    assert len(gemini.calls) == 1
    pre = db_session.get(JobApplication, pre_id)
    assert pre is not None and pre.status == JobStatus.SCORED
