"""Enrich stage: SCORED → ENRICHED / ENRICH_FAILED.

For each SCORED job the stage resolves a :class:`PhonebookEntry` via
:class:`PhonebookLookup` (cache → Apollo → Hunter → pattern-guess →
``careers@<domain>``/``hr@<domain>`` fallback). When a company has no
domain at all the chain raises :class:`ValueError` and we mark the job
``ENRICH_FAILED`` with ``RejectionReason.NO_EMAIL_FOUND``.

Ordering rationale: highest ``score`` first, then ``discovered_at ASC``
to break ties FIFO. This way if Apollo/Hunter quotas exhaust mid-run,
the most promising leads have already been enriched.

Every status transition writes a :class:`JobApplicationEvent` row with
``run_id`` in the JSON payload so the audit log threads back to the
pipeline run that performed the change.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog
from sqlalchemy import asc, desc
from sqlmodel import Session, select

from knockknock.db.enums import JobStatus, RejectionReason
from knockknock.db.models import Company, JobApplication, JobApplicationEvent
from knockknock.phonebook.lookup import PhonebookLookup
from knockknock.pipeline.stage import StageContext, StageResult

log = structlog.get_logger(__name__)


@dataclass
class EnrichStage:
    """Resolve a phonebook contact for each SCORED job in score-desc order."""

    session: Session
    chain: PhonebookLookup
    name: str = field(default="enrich", init=False)

    def run(self, ctx: StageContext) -> StageResult:
        rows = self.session.exec(
            select(JobApplication, Company)
            .join(Company, JobApplication.company_id == Company.id)  # type: ignore[arg-type]
            .where(JobApplication.status == JobStatus.SCORED)
            .order_by(
                desc(JobApplication.score),  # type: ignore[arg-type]
                asc(JobApplication.discovered_at),  # type: ignore[arg-type]
            )
        ).all()

        processed = 0
        advanced = 0
        rejected = 0
        errors = 0

        for job, company in rows:
            processed += 1
            prev_status = job.status
            try:
                outcome = self.chain.resolve(session=self.session, company=company)
            except ValueError as exc:
                # Company has no domain — no fallback possible.
                job.status = JobStatus.ENRICH_FAILED
                job.rejection_reason = RejectionReason.NO_EMAIL_FOUND
                job.rejection_detail = str(exc)
                self.session.add(job)
                self.session.add(
                    JobApplicationEvent(
                        job_id=job.id,
                        from_status=prev_status,
                        to_status=job.status,
                        note="enrich_failed: no usable domain",
                        payload={
                            "run_id": ctx.run_id,
                            "reason": RejectionReason.NO_EMAIL_FOUND.value,
                        },
                    )
                )
                self.session.flush()
                rejected += 1
                log.info(
                    "enrich.no_email",
                    job_id=job.id,
                    company_id=company.id,
                    run_id=ctx.run_id,
                )
                continue
            except Exception as exc:
                # Defensive: never let an unexpected provider bug halt the stage.
                errors += 1
                log.warning(
                    "enrich.error",
                    job_id=job.id,
                    company_id=company.id,
                    error_code=type(exc).__name__,
                    error=str(exc),
                    run_id=ctx.run_id,
                )
                continue

            job.status = JobStatus.ENRICHED
            self.session.add(job)
            self.session.add(
                JobApplicationEvent(
                    job_id=job.id,
                    from_status=prev_status,
                    to_status=job.status,
                    note=f"enriched via {outcome.source.value}",
                    payload={
                        "run_id": ctx.run_id,
                        "source": outcome.source.value,
                        "has_founder_email": outcome.founder_email is not None,
                    },
                )
            )
            self.session.flush()
            advanced += 1

        log.info(
            "enrich.summary",
            processed=processed,
            advanced=advanced,
            rejected=rejected,
            errors=errors,
            run_id=ctx.run_id,
        )
        return StageResult(
            stage=self.name,
            processed=processed,
            advanced=advanced,
            rejected=rejected,
            errors=errors,
        )
