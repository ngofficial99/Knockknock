"""PipelineRun bookkeeping: open a row at start, close it with counts at end."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from sqlmodel import Session

from knockknock.db.enums import PipelineRunStatus
from knockknock.db.models import PipelineRun
from knockknock.pipeline.stage import StageResult


def begin_run(session: Session) -> int:
    """Insert a new PipelineRun row in RUNNING state and return its id."""
    row = PipelineRun(status=PipelineRunStatus.RUNNING)
    session.add(row)
    session.flush()
    assert row.id is not None
    return row.id


def finalize_run(session: Session, run_id: int, results: list[StageResult]) -> None:
    """Close the PipelineRun row with per-stage counts and overall status.

    Status policy:
        FAILED  - at least one error and nothing advanced anywhere
        PARTIAL - at least one error but some stage made progress
        SUCCESS - no errors anywhere
    """
    counts = {"discover": 0, "pre_filter": 0, "score": 0, "draft": 0}
    errors = sum(r.errors for r in results)
    advanced_total = sum(r.advanced for r in results)
    for r in results:
        if r.stage in counts:
            counts[r.stage] = r.advanced

    if errors > 0 and advanced_total == 0:
        status = PipelineRunStatus.FAILED
    elif errors > 0:
        status = PipelineRunStatus.PARTIAL
    else:
        status = PipelineRunStatus.SUCCESS

    row = session.get(PipelineRun, run_id)
    if row is None:
        return
    row.finished_at = datetime.now(UTC)
    row.status = status
    row.discovered_count = counts["discover"]
    row.pre_filtered_count = counts["pre_filter"]
    row.scored_count = counts["score"]
    row.drafted_count = counts["draft"]
    row.error_count = errors
    row.summary = {r.stage: asdict(r) for r in results}
    session.add(row)
    session.flush()
