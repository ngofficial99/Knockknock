"""Tailor stage: ENRICHED → TAILORED via deterministic resume selector.

For each ENRICHED job we tokenise the title + description, run the
tag-overlap selector against the loaded manifest, and write the chosen
variant key onto the job row. No LLM, no I/O beyond the DB — identical
inputs produce identical outputs, so reruns are safe.

Ordering matches the rest of the pipeline (``score DESC, discovered_at
ASC``) so that if the run halts midway, the most promising leads have
already been assigned a variant.

Failure mode: if the selector returns ``None`` (only happens when the
manifest is empty — an operator config bug) we leave the job at
ENRICHED, log an error, and tally it under ``errors``. We deliberately
do NOT transition to ``ENRICH_FAILED``: this is not a data problem,
it's a config problem and the next run with a fixed manifest should
pick the job up where it left off.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog
from sqlalchemy import asc, desc
from sqlmodel import Session, select

from knockknock.db.enums import JobStatus
from knockknock.db.models import JobApplication, JobApplicationEvent
from knockknock.pipeline.stage import StageContext, StageResult
from knockknock.resume.manifest import ResumeManifest
from knockknock.resume.selector import select_variant
from knockknock.resume.tokens import extract_job_tokens

log = structlog.get_logger(__name__)


@dataclass
class TailorStage:
    """Assign a resume variant to every ENRICHED job in score-desc order."""

    session: Session
    manifest: ResumeManifest
    name: str = field(default="tailor", init=False)

    def run(self, ctx: StageContext) -> StageResult:
        jobs = self.session.exec(
            select(JobApplication)
            .where(JobApplication.status == JobStatus.ENRICHED)
            .order_by(
                desc(JobApplication.score),  # type: ignore[arg-type]
                asc(JobApplication.discovered_at),  # type: ignore[arg-type]
            )
        ).all()

        processed = 0
        advanced = 0
        errors = 0

        for job in jobs:
            processed += 1
            tokens = extract_job_tokens(
                self.manifest,
                title=job.title,
                description=job.description,
            )
            chosen = select_variant(self.manifest, tokens)
            if chosen is None:
                # Operator config error — broken/empty manifest. Don't mutate the job.
                errors += 1
                log.error(
                    "tailor.no_variant_selected",
                    job_id=job.id,
                    manifest_variants=len(self.manifest.variants),
                    run_id=ctx.run_id,
                )
                continue

            prev_status = job.status
            job.resume_variant_key = chosen.key
            job.status = JobStatus.TAILORED
            self.session.add(job)
            self.session.add(
                JobApplicationEvent(
                    job_id=job.id,
                    from_status=prev_status,
                    to_status=job.status,
                    note=f"tailored variant={chosen.key}",
                    payload={
                        "run_id": ctx.run_id,
                        "variant_key": chosen.key,
                    },
                )
            )
            self.session.flush()
            advanced += 1

        log.info(
            "tailor.summary",
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
