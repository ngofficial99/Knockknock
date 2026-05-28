"""Score stage: PRE_FILTERED → SCORED / SCORE_REJECTED via Gemini Flash.

This is the first stage that talks to Gemini, so it owns three side
effects per processed job:

1. ``GeminiCallLog`` row -- always, whether the call succeeded or not.
   Failed rows feed the day-of error rate dashboards (Phase 11).
2. ``JobApplication`` mutation -- ``score``, ``score_rationale``,
   ``status``, and ``rejection_reason`` (when low-score).
3. ``JobApplicationEvent`` row -- audit trail of the status transition
   with the prompt version and verdict in the JSONB payload.

The stage halts cleanly (returns a :class:`StageResult` with
``halted_reason="rate_limited"``) on any :class:`RateLimitedError` from
the limiter. The pipeline run is marked PARTIAL by the caller. Per-job
errors (non-JSON response, SDK exception) do NOT halt: the job stays at
``PRE_FILTERED`` and the next run retries it.

Threshold conversion: ``prefs.scoring.min_score_to_draft`` is on a 0-100
scale (matches the human-readable YAML), but Gemini scores are integers
in [1, 10]. We map by ``ceil(min/10)`` so e.g. ``min_score_to_draft=70``
requires a Gemini score >= 7. ``math.ceil`` keeps the lower-bound
inclusive (avoid floor + off-by-one when min is not a multiple of 10).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

import structlog
from sqlalchemy import asc
from sqlmodel import Session, select

from knockknock.clients.gemini import GeminiResponse
from knockknock.config.preferences import JobPreferences
from knockknock.db.enums import (
    GeminiModel,
    GeminiPurpose,
    JobStatus,
    RejectionReason,
)
from knockknock.db.models import (
    Company,
    GeminiCallLog,
    JobApplication,
    JobApplicationEvent,
)
from knockknock.exceptions import RateLimitedError
from knockknock.pipeline.stage import StageContext, StageResult
from knockknock.scoring.parser import parse_score
from knockknock.scoring.prompts import (
    PROMPT_VERSION,
    SYSTEM_INSTRUCTION,
    build_score_prompt,
)

log = structlog.get_logger(__name__)


class _GeminiLike(Protocol):
    """Minimal Gemini client surface the stage depends on."""

    def generate(
        self,
        *,
        model: GeminiModel,
        system_instruction: str,
        user_prompt: str,
        response_mime_type: str,
    ) -> GeminiResponse: ...


class _LimiterLike(Protocol):
    """Minimal limiter surface the stage depends on."""

    def check(self, model: GeminiModel, purpose: GeminiPurpose) -> None: ...

    def compute_rpm_wait_seconds(self, model: GeminiModel) -> float | None: ...


@dataclass
class ScoreStage:
    """Score PRE_FILTERED jobs FIFO; halt cleanly on rate-limit exhaustion."""

    session: Session
    prefs: JobPreferences
    gemini: _GeminiLike
    limiter: _LimiterLike
    # Injectable for deterministic tests. Real callers don't override.
    sleep_fn: Callable[[float], None] = field(default=time.sleep)
    name: str = field(default="score", init=False)
    # Score stage always uses Flash; Pro is reserved for DRAFT/REGENERATE.
    _model: GeminiModel = field(default=GeminiModel.FLASH_2_5, init=False)

    def run(self, ctx: StageContext) -> StageResult:
        threshold = self._threshold_1_to_10()
        rows = self.session.exec(
            select(JobApplication, Company)
            .join(Company, JobApplication.company_id == Company.id)  # type: ignore[arg-type]
            .where(JobApplication.status == JobStatus.PRE_FILTERED)
            .order_by(asc(JobApplication.discovered_at))  # type: ignore[arg-type]
        ).all()

        processed = 0
        advanced = 0
        rejected = 0
        errors = 0
        halted_reason: str | None = None

        for job, company in rows:
            # Pre-call: enforce daily/safety quotas BEFORE consuming a call.
            try:
                self.limiter.check(self._model, GeminiPurpose.SCORE)
            except RateLimitedError as exc:
                log.warning(
                    "score.halted",
                    reason=str(exc),
                    processed=processed,
                    run_id=ctx.run_id,
                )
                halted_reason = "rate_limited"
                break

            # Soft RPM smoothing. The +0.1 absorbs clock skew; cap at 65s
            # to keep CI/local-dev runs from sleeping forever if the
            # limiter ever returns a runaway value (defence-in-depth).
            wait = self.limiter.compute_rpm_wait_seconds(self._model)
            if wait and wait > 0:
                self.sleep_fn(min(wait + 0.1, 65.0))

            prompt = build_score_prompt(
                self.prefs,
                company_name=company.name,
                role_title=job.title,
                location=job.location or "",
                description=job.description or "",
            )

            started = time.monotonic()
            try:
                response = self.gemini.generate(
                    model=self._model,
                    system_instruction=SYSTEM_INSTRUCTION,
                    user_prompt=prompt,
                    response_mime_type="application/json",
                )
                parsed = parse_score(response.text)
            except Exception as exc:  # All failures captured; job retried next run.
                latency_ms = int((time.monotonic() - started) * 1000)
                self.session.add(
                    GeminiCallLog(
                        job_id=job.id,
                        model=self._model,
                        purpose=GeminiPurpose.SCORE,
                        prompt_tokens=0,
                        completion_tokens=0,
                        latency_ms=latency_ms,
                        success=False,
                        error_code=type(exc).__name__,
                    )
                )
                errors += 1
                log.warning(
                    "score.call_failed",
                    job_id=job.id,
                    error_code=type(exc).__name__,
                    error=str(exc),
                    run_id=ctx.run_id,
                )
                self.session.flush()
                continue

            latency_ms = int((time.monotonic() - started) * 1000)
            self.session.add(
                GeminiCallLog(
                    job_id=job.id,
                    model=self._model,
                    purpose=GeminiPurpose.SCORE,
                    prompt_tokens=response.tokens_input,
                    completion_tokens=response.tokens_output,
                    latency_ms=latency_ms,
                    success=True,
                )
            )

            # Persist verdict.
            prev_status = job.status
            job.score = parsed.score
            job.score_rationale = parsed.rationale
            if parsed.score >= threshold:
                job.status = JobStatus.SCORED
                advanced += 1
            else:
                job.status = JobStatus.SCORE_REJECTED
                job.rejection_reason = RejectionReason.SCORE_LOW
                job.rejection_detail = f"gemini score {parsed.score} < threshold {threshold}"
                rejected += 1
            processed += 1

            self.session.add(job)
            self.session.add(
                JobApplicationEvent(
                    job_id=job.id,
                    from_status=prev_status,
                    to_status=job.status,
                    note=f"score={parsed.score} prompt={PROMPT_VERSION}",
                    payload={
                        "score": parsed.score,
                        "rationale": parsed.rationale,
                        "matched_must_have": list(parsed.matched_must_have),
                        "matched_nice_to_have": list(parsed.matched_nice_to_have),
                        "seniority_match": parsed.seniority_match,
                        "company_stage_match": parsed.company_stage_match,
                        "prompt_version": PROMPT_VERSION,
                    },
                )
            )
            self.session.flush()

        log.info(
            "score.summary",
            processed=processed,
            advanced=advanced,
            rejected=rejected,
            errors=errors,
            halted=halted_reason is not None,
            run_id=ctx.run_id,
        )
        return StageResult(
            stage=self.name,
            processed=processed,
            advanced=advanced,
            rejected=rejected,
            errors=errors,
            halted_reason=halted_reason,
        )

    def _threshold_1_to_10(self) -> int:
        """Map ``prefs.scoring.min_score_to_draft`` (0-100) to a 1-10 threshold.

        ``ceil`` keeps the comparison inclusive: ``min_score_to_draft=70``
        requires Gemini score >= 7. A min of 0 yields a threshold of 1
        (since scores are clamped to >=1 anyway).
        """
        return max(1, math.ceil(self.prefs.scoring.min_score_to_draft / 10))
