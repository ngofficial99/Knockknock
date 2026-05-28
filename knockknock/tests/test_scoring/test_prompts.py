"""Unit tests for the score prompt builder.

These tests assert structural invariants only -- specific copy of the
rubric is a deliberate non-goal so the template can evolve without
churning the suite. We verify:

- candidate / job context lands in the prompt
- preference lists (skills, locations, seniority) are visible to the LLM
- long descriptions are truncated under the context budget
"""

from __future__ import annotations

from pathlib import Path

from knockknock.config.preferences import JobPreferences, load_preferences
from knockknock.scoring.prompts import PROMPT_VERSION, build_score_prompt


def _prefs(tmp_path: Path) -> JobPreferences:
    """Load real ``job_preferences.yaml`` into the tmp dir for isolation."""
    src = Path("config/job_preferences.yaml").read_text(encoding="utf-8")
    p = tmp_path / "prefs.yaml"
    p.write_text(src, encoding="utf-8")
    return load_preferences(p)


def test_build_score_prompt_includes_job_context(tmp_path: Path) -> None:
    prefs = _prefs(tmp_path)
    prompt = build_score_prompt(
        prefs,
        company_name="Acme",
        role_title="Senior Backend Engineer",
        location="Bangalore",
        description="Build distributed Python services on AWS.",
    )
    assert "Acme" in prompt
    assert "Senior Backend Engineer" in prompt
    assert "Bangalore" in prompt
    assert "distributed Python services" in prompt


def test_build_score_prompt_includes_preference_skills(tmp_path: Path) -> None:
    prefs = _prefs(tmp_path)
    prompt = build_score_prompt(
        prefs,
        company_name="Acme",
        role_title="Backend Engineer",
        location="Remote",
        description="x",
    )
    # Must-have skills visible (Python is in fixture).
    assert "Python" in prompt
    # Nice-to-have skills visible (Kafka is in fixture).
    assert "Kafka" in prompt
    # Seniority targets and locations visible.
    assert "Senior" in prompt
    assert "Bengaluru" in prompt or "Bangalore" in prompt


def test_build_score_prompt_truncates_long_description(tmp_path: Path) -> None:
    prefs = _prefs(tmp_path)
    description = "X" * 50_000
    prompt = build_score_prompt(
        prefs,
        company_name="Acme",
        role_title="SDE",
        location="Bangalore",
        description=description,
    )
    assert len(prompt) < 20_000  # well under context budget
    assert "[truncated]" in prompt


def test_build_score_prompt_handles_empty_description(tmp_path: Path) -> None:
    prefs = _prefs(tmp_path)
    prompt = build_score_prompt(
        prefs,
        company_name="Acme",
        role_title="SDE",
        location="",
        description="",
    )
    # Should not crash and should surface a placeholder.
    assert "no description" in prompt.lower()


def test_prompt_version_is_stable() -> None:
    """Prompt version is the audit-trail key for score lineage; don't change casually."""
    assert PROMPT_VERSION == "score-v1"
