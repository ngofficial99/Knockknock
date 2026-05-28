from __future__ import annotations

from datetime import UTC, datetime

import pytest

from knockknock.config.preferences import JobPreferences
from knockknock.db.enums import CompanySizeBucket, JobSource, RejectionReason
from knockknock.db.models import Company, JobApplication
from knockknock.filter.rules import RuleEngine


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
                "locations": ["Bengaluru", "Bangalore", "Remote (India)"],
                "titles_allow": ["Backend Engineer", "Software Engineer"],
                "titles_deny": ["Manager", "Intern", "Frontend Engineer"],
                "seniority_allow": ["Mid", "Senior", "SDE 2", "SDE 3"],
                "company_size_allow": ["SEED", "SERIES_A", "SERIES_B"],
            },
            "skills": {"must_have_any": ["Python", "Java"], "nice_to_have": []},
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


def _job(**overrides: object) -> JobApplication:
    defaults: dict[str, object] = {
        "company_id": 1,
        "source": JobSource.HN,
        "source_job_id": "hn-1",
        "title": "Backend Engineer",
        "location": "Bengaluru",
        "apply_url": "https://x.test/a",
        "description": "Backend role using Python and Postgres",
        "posted_at": datetime(2026, 5, 28, tzinfo=UTC),
    }
    defaults.update(overrides)
    return JobApplication(**defaults)


def _company(
    size: CompanySizeBucket = CompanySizeBucket.SERIES_A,
    name: str = "Acme",
    domain: str = "acme.test",
) -> Company:
    return Company(id=1, name=name, domain=domain, size_bucket=size)


@pytest.fixture
def engine() -> RuleEngine:
    return RuleEngine(prefs=_prefs(), blacklist_pattern=lambda name, domain: None)


def test_passes_when_all_rules_match(engine: RuleEngine) -> None:
    verdict = engine.evaluate(_job(), _company())
    assert verdict.passed
    assert verdict.reason is None


def test_rejects_when_title_is_denied(engine: RuleEngine) -> None:
    verdict = engine.evaluate(_job(title="Engineering Manager"), _company())
    assert not verdict.passed
    assert verdict.reason == RejectionReason.ROLE_MISMATCH


def test_rejects_when_title_not_in_allow_list(engine: RuleEngine) -> None:
    verdict = engine.evaluate(_job(title="Data Scientist"), _company())
    assert not verdict.passed
    assert verdict.reason == RejectionReason.ROLE_MISMATCH


def test_rejects_when_location_is_wrong(engine: RuleEngine) -> None:
    verdict = engine.evaluate(_job(location="San Francisco, US only"), _company())
    assert not verdict.passed
    assert verdict.reason == RejectionReason.LOCATION_MISMATCH


def test_rejects_when_no_required_skill_in_description(engine: RuleEngine) -> None:
    verdict = engine.evaluate(
        _job(description="A pure Rust shop with no relevant tools"),
        _company(),
    )
    assert not verdict.passed
    assert verdict.reason == RejectionReason.ROLE_MISMATCH


def test_rejects_when_company_size_not_allowed(engine: RuleEngine) -> None:
    # NOTE: Phase 4 plan referenced CompanySizeBucket.SERIES_C_PLUS, but our
    # enum has LATE_STAGE for that bucket. Substituting here.
    verdict = engine.evaluate(_job(), _company(size=CompanySizeBucket.LATE_STAGE))
    assert not verdict.passed
    assert verdict.reason == RejectionReason.OTHER


def test_rejects_when_blacklisted() -> None:
    engine = RuleEngine(prefs=_prefs(), blacklist_pattern=lambda name, domain: "zeotap")
    verdict = engine.evaluate(_job(), _company())
    assert not verdict.passed
    assert verdict.reason == RejectionReason.BLACKLISTED
    assert "zeotap" in (verdict.detail or "")


# ---- Audit-discovered data-quality filters (Phase 3 audit harness findings) ----


@pytest.mark.parametrize(
    "title",
    [
        "Acme Labs | Bengaluru | ONSITE",
        "Acme Labs | Remote",
        "TestCo | HYBRID",
        "Some Company | USA | Onsite",
        "Some Company | India",
    ],
)
def test_rejects_when_title_looks_like_location_header(engine: RuleEngine, title: str) -> None:
    verdict = engine.evaluate(_job(title=title), _company())
    assert not verdict.passed
    assert verdict.reason == RejectionReason.OTHER
    assert "title-looks-like-location" in (verdict.detail or "")


@pytest.mark.parametrize(
    "domain",
    ["linkedin.com", "bit.ly", "lnkd.in", "tinyurl.com", "goo.gl"],
)
def test_rejects_when_company_domain_is_generic_redirector(engine: RuleEngine, domain: str) -> None:
    verdict = engine.evaluate(_job(), _company(domain=domain))
    assert not verdict.passed
    assert verdict.reason == RejectionReason.OTHER
    assert "generic-redirector" in (verdict.detail or "")
