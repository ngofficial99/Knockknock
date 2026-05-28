from __future__ import annotations

from sqlmodel import Session, select

from knockknock.config.preferences import JobPreferences
from knockknock.db.enums import (
    CompanySizeBucket,
    JobSource,
    JobStatus,
    RejectionReason,
)
from knockknock.db.models import Company, JobApplication, JobApplicationEvent
from knockknock.filter.rules import RuleEngine
from knockknock.pipeline.pre_filter import PreFilterStage
from knockknock.pipeline.stage import StageContext


def _prefs() -> JobPreferences:
    return JobPreferences.model_validate(
        {
            "candidate": {
                "name": "x",
                "current_role": "x",
                "years_experience": 4,
                "location": "Bengaluru",
            },
            "target": {
                "locations": ["Bengaluru"],
                "titles_allow": ["Backend Engineer"],
                "titles_deny": ["Manager"],
                "seniority_allow": ["Mid"],
                "company_size_allow": ["SEED", "SERIES_A"],
            },
            "skills": {"must_have_any": ["Python"], "nice_to_have": []},
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
                "gemini_pro_rpd_ceiling": 1,
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


def _seed(session: Session) -> tuple[int, int]:
    co = Company(name="Acme", domain="acme.test", size_bucket=CompanySizeBucket.SERIES_A)
    session.add(co)
    session.flush()
    assert co.id
    good = JobApplication(
        company_id=co.id,
        source=JobSource.HN,
        source_job_id="hn-good",
        title="Backend Engineer",
        location="Bengaluru",
        apply_url="https://x.test/a",
        description="Python role",
        status=JobStatus.DISCOVERED,
    )
    bad = JobApplication(
        company_id=co.id,
        source=JobSource.HN,
        source_job_id="hn-bad",
        title="Engineering Manager",
        location="Bengaluru",
        apply_url="https://x.test/b",
        description="Python role",
        status=JobStatus.DISCOVERED,
    )
    session.add_all([good, bad])
    session.flush()
    assert good.id and bad.id
    return good.id, bad.id


def test_pre_filter_advances_and_rejects(db_session: Session) -> None:
    good_id, bad_id = _seed(db_session)
    engine = RuleEngine(prefs=_prefs(), blacklist_pattern=lambda n, d: None)
    stage = PreFilterStage(session=db_session, engine=engine)
    result = stage.run(StageContext(run_id=1))
    assert result.processed == 2
    assert result.advanced == 1
    assert result.rejected == 1

    good = db_session.get(JobApplication, good_id)
    bad = db_session.get(JobApplication, bad_id)
    assert good is not None and good.status == JobStatus.PRE_FILTERED
    assert bad is not None and bad.status == JobStatus.PRE_FILTER_REJECTED
    assert bad.rejection_reason == RejectionReason.ROLE_MISMATCH

    events = db_session.exec(select(JobApplicationEvent)).all()
    assert {e.to_status for e in events} == {
        JobStatus.PRE_FILTERED,
        JobStatus.PRE_FILTER_REJECTED,
    }


def test_pre_filter_skips_already_processed_jobs(db_session: Session) -> None:
    _seed(db_session)
    engine = RuleEngine(prefs=_prefs(), blacklist_pattern=lambda n, d: None)
    PreFilterStage(session=db_session, engine=engine).run(StageContext(run_id=1))
    # Second run should not re-process anything since status moved off DISCOVERED.
    result = PreFilterStage(session=db_session, engine=engine).run(StageContext(run_id=2))
    assert result.processed == 0
