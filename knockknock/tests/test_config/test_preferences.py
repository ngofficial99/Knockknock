from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from knockknock.config.preferences import JobPreferences, load_preferences

FIXTURE = Path(__file__).parent.parent / "fixtures" / "preferences_sample.yaml"


def _valid_prefs_dict(seed_path: str | None = None) -> dict[str, Any]:
    """Builds a minimal-valid prefs dict in the Phase-10 source shape."""
    return {
        "candidate": {
            "name": "x",
            "email": "x@example.com",
            "current_role": "x",
            "years_experience": 1,
            "location": "x",
        },
        "target": {
            "locations": ["x"],
            "titles_allow": ["x"],
            "titles_deny": [],
            "seniority_allow": ["x"],
            "company_size_allow": ["SEED"],
        },
        "skills": {"must_have_any": ["x"], "nice_to_have": []},
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
            "hn": {"enabled": False, "months_lookback": 1},
            "wellfound": {
                "enabled": True,
                "location": "Bangalore",
                "role_types": ["engineering"],
                "remote": True,
            },
            "yc_waas": {"enabled": True, "location": "India", "role": "engineer"},
            "greenhouse": {"enabled": bool(seed_path), "company_seed_list_path": seed_path},
            "lever": {"enabled": False, "company_seed_list_path": None},
            "ashby": {"enabled": False, "company_seed_list_path": None},
        },
    }


def test_load_preferences_parses_fixture() -> None:
    prefs = load_preferences(FIXTURE)
    assert isinstance(prefs, JobPreferences)
    assert prefs.candidate.name == "Test User"
    assert "Python" in prefs.skills.must_have_any
    assert prefs.scoring.min_score_to_draft == 70


def test_scoring_weights_must_sum_to_100() -> None:
    bad = _valid_prefs_dict()
    bad["scoring"] = {
        "min_score_to_draft": 70,
        "weight_skill_match": 10,
        "weight_seniority_match": 10,
        "weight_location_match": 10,
        "weight_company_stage": 10,
    }
    with pytest.raises(ValueError, match="must sum to 100"):
        JobPreferences.model_validate(bad)


def test_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_preferences(Path("/nonexistent/preferences.yaml"))


def test_invalid_company_size_rejected() -> None:
    """Errata E.2: SERIES_C_PLUS is no longer valid; SERIES_C + LATE_STAGE are."""
    bad = _valid_prefs_dict()
    bad["target"]["company_size_allow"] = ["SERIES_C_PLUS"]
    with pytest.raises(ValueError):
        JobPreferences.model_validate(bad)


# ---- Phase 10: expanded source sub-models ----


def test_ats_sources_carry_seed_paths(tmp_path: Path) -> None:
    """Greenhouse/Lever/Ashby take a seed-YAML path; Wellfound/YC WaaS take
    structured search params (location/role) instead of free-text queries."""
    seed = tmp_path / "gh.yaml"
    seed.write_text("companies: [{slug: stripe, name: Stripe}]\n")
    prefs = JobPreferences.model_validate(_valid_prefs_dict(seed_path=str(seed)))

    assert prefs.sources.greenhouse.enabled is True
    assert prefs.sources.greenhouse.company_seed_list_path == seed
    assert prefs.sources.wellfound.location == "Bangalore"
    assert prefs.sources.wellfound.role_types == ["engineering"]
    assert prefs.sources.wellfound.remote is True
    assert prefs.sources.yc_waas.role == "engineer"
    assert prefs.sources.yc_waas.location == "India"


def test_ats_source_enabled_without_seed_path_rejected() -> None:
    """An ATS source with enabled=True but no seed-list path is a config bug."""
    bad = _valid_prefs_dict()
    bad["sources"]["lever"] = {"enabled": True, "company_seed_list_path": None}
    with pytest.raises(ValueError, match="company_seed_list_path"):
        JobPreferences.model_validate(bad)
