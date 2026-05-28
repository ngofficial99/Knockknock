"""Pipeline orchestrator. Stages run in order; errors are caught per-stage."""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from knockknock.pipeline.stage import Stage, StageContext, StageResult

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PipelineSummary:
    """Aggregate output of one ``run_once`` call."""

    run_id: int
    results: list[StageResult]


@dataclass
class PipelineRunner:
    """Sequentially runs stages; an exception in one stage does not stop the rest."""

    stages: list[Stage] = field(default_factory=list)

    def run_once(self, ctx: StageContext) -> PipelineSummary:
        """Execute every stage; if one errors, log and continue to the next."""
        results: list[StageResult] = []
        for stage in self.stages:
            try:
                result = stage.run(ctx)
            except Exception as exc:
                log.exception(
                    "stage.failed",
                    stage=stage.name,
                    run_id=ctx.run_id,
                    error=str(exc),
                )
                result = StageResult(
                    stage=stage.name,
                    processed=0,
                    advanced=0,
                    rejected=0,
                    errors=1,
                )
            results.append(result)
            log.info(
                "stage.completed",
                stage=stage.name,
                processed=result.processed,
                advanced=result.advanced,
                rejected=result.rejected,
                errors=result.errors,
            )
        return PipelineSummary(run_id=ctx.run_id, results=results)
