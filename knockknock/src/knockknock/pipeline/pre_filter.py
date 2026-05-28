"""Pre-filter stage: hard rules + blacklist, mutate ``JobApplication.status``.

Reads every ``JobApplication`` in status ``DISCOVERED``, evaluates the
``RuleEngine``, and writes either:

- ``status = PRE_FILTERED`` for verdicts that pass, OR
- ``status = PRE_FILTER_REJECTED`` + ``rejection_reason`` + ``rejection_detail``
  for verdicts that fail.

Each transition also writes a ``JobApplicationEvent`` for the audit log.
Transaction boundary is owned by the caller (``session_scope``); the stage
only ``flush``es so in-transaction subsequent stages can see the new statuses.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog
from sqlmodel import Session, select

from knockknock.db.enums import JobStatus
from knockknock.db.models import Company, JobApplication, JobApplicationEvent
from knockknock.filter.rules import RuleEngine
from knockknock.pipeline.stage import StageContext, StageResult

log = structlog.get_logger(__name__)


@dataclass
class PreFilterStage:
    session: Session
    engine: RuleEngine
    name: str = field(default="pre_filter", init=False)

    def run(self, ctx: StageContext) -> StageResult:
        rows = self.session.exec(
            select(JobApplication, Company)
            .join(Company, JobApplication.company_id == Company.id)  # type: ignore[arg-type]
            .where(JobApplication.status == JobStatus.DISCOVERED)
        ).all()

        processed = advanced = rejected = errors = 0
        for job, company in rows:
            try:
                verdict = self.engine.evaluate(job, company)
            except Exception as exc:
                log.exception(
                    "pre_filter.eval_failed",
                    job_id=job.id,
                    run_id=ctx.run_id,
                    error=str(exc),
                )
                errors += 1
                continue

            processed += 1
            prev = job.status
            if verdict.passed:
                job.status = JobStatus.PRE_FILTERED
                advanced += 1
            else:
                job.status = JobStatus.PRE_FILTER_REJECTED
                job.rejection_reason = verdict.reason
                job.rejection_detail = verdict.detail
                rejected += 1

            self.session.add(job)
            self.session.add(
                JobApplicationEvent(
                    job_id=job.id,
                    from_status=prev,
                    to_status=job.status,
                    note=verdict.detail,
                )
            )

        self.session.flush()
        return StageResult(
            stage=self.name,
            processed=processed,
            advanced=advanced,
            rejected=rejected,
            errors=errors,
        )
