from __future__ import annotations

from sqlmodel import Session, select

from knockknock.db.enums import PipelineRunStatus
from knockknock.db.models import PipelineRun
from knockknock.observability.metrics import begin_run, finalize_run
from knockknock.pipeline.stage import StageResult


def test_begin_and_finalize_run(db_session: Session) -> None:
    run_id = begin_run(db_session)
    results = [
        StageResult(stage="discover", processed=5, advanced=5, rejected=0, errors=0),
    ]
    finalize_run(db_session, run_id, results)
    db_session.flush()
    row = db_session.exec(select(PipelineRun).where(PipelineRun.id == run_id)).one()
    assert row.status == PipelineRunStatus.SUCCESS
    assert row.discovered_count == 5
    assert row.finished_at is not None


def test_finalize_run_marks_failed_when_all_errors(db_session: Session) -> None:
    run_id = begin_run(db_session)
    results = [StageResult(stage="discover", processed=0, advanced=0, rejected=0, errors=1)]
    finalize_run(db_session, run_id, results)
    db_session.flush()
    row = db_session.exec(select(PipelineRun).where(PipelineRun.id == run_id)).one()
    assert row.status == PipelineRunStatus.FAILED
    assert row.error_count == 1
