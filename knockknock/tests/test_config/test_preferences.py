from __future__ import annotations

from pathlib import Path

import pytest

from knockknock.config.preferences import JobPreferences, load_preferences

FIXTURE = Path(__file__).parent.parent / "fixtures" / "preferences_sample.yaml"


def test_load_preferences_parses_fixture() -> None:
    prefs = load_preferences(FIXTURE)
    assert isinstance(prefs, JobPreferences)
    assert prefs.candidate.name == "Test User"
    assert "Python" in prefs.skills.must_have_any
    assert prefs.scoring.min_score_to_draft == 70


def test_scoring_weights_must_sum_to_100() -> None:
    with pytest.raises(ValueError, match="must sum to 100"):
        JobPreferences.model_validate(
            {
                "candidate": {
                    "name": "x",
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
                    "weight_skill_match": 10,
                    "weight_seniority_match": 10,
                    "weight_location_match": 10,
                    "weight_company_stage": 10,
                },
                "limits": {
                    "daily_drafts_cap": 1,
                    "hourly_discover_cap": 1,
                    "gemini_pro_rpd_ceiling": 1,
                },
                "sources": {
                    "hn": {"enabled": False, "months_lookback": 1},
                    "wellfound": {"enabled": False, "query": ""},
                    "yc_waas": {"enabled": False, "query": ""},
                    "greenhouse": {"enabled": False, "boards": []},
                    "lever": {"enabled": False, "boards": []},
                    "ashby": {"enabled": False, "boards": []},
                },
            }
        )


def test_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_preferences(Path("/nonexistent/preferences.yaml"))


def test_invalid_company_size_rejected() -> None:
    """Errata E.2: SERIES_C_PLUS is no longer valid; SERIES_C + LATE_STAGE are."""
    with pytest.raises(ValueError):
        JobPreferences.model_validate(
            {
                "candidate": {
                    "name": "x",
                    "current_role": "x",
                    "years_experience": 1,
                    "location": "x",
                },
                "target": {
                    "locations": ["x"],
                    "titles_allow": ["x"],
                    "titles_deny": [],
                    "seniority_allow": ["x"],
                    "company_size_allow": ["SERIES_C_PLUS"],
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
                    "wellfound": {"enabled": False, "query": ""},
                    "yc_waas": {"enabled": False, "query": ""},
                    "greenhouse": {"enabled": False, "boards": []},
                    "lever": {"enabled": False, "boards": []},
                    "ashby": {"enabled": False, "boards": []},
                },
            }
        )
