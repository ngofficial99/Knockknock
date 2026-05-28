"""Typed loader for ``config/job_preferences.yaml``.

Validates against a strict Pydantic model. The single ``load_preferences``
helper reads YAML, raises :class:`ConfigError` for syntax/shape problems and
:class:`FileNotFoundError` for missing files.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from knockknock.db.enums import CompanySizeBucket
from knockknock.exceptions import ConfigError


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    # Outbound signature address — appears in the email body and is the
    # ``From:`` on the Gmail draft created by Phase 8. Required because
    # we refuse to send anonymous cold emails. Validated as ``EmailStr``
    # so an obviously bad value fails at preferences-load time, not at
    # the Gmail API boundary.
    email: EmailStr
    current_role: str
    years_experience: int = Field(ge=0, le=50)
    location: str
    # Optional one-line elevator pitch fed into the Pro draft prompt. Kept
    # optional so the existing test fixture / minimal YAML still validates;
    # when absent the prompt falls back to ``current_role`` + experience.
    pitch: str | None = None


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")
    locations: list[str] = Field(min_length=1)
    titles_allow: list[str] = Field(min_length=1)
    titles_deny: list[str]
    seniority_allow: list[str] = Field(min_length=1)
    company_size_allow: list[CompanySizeBucket] = Field(min_length=1)


class Skills(BaseModel):
    model_config = ConfigDict(extra="forbid")
    must_have_any: list[str] = Field(min_length=1)
    nice_to_have: list[str]


class Scoring(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_score_to_draft: int = Field(ge=0, le=100)
    weight_skill_match: int = Field(ge=0, le=100)
    weight_seniority_match: int = Field(ge=0, le=100)
    weight_location_match: int = Field(ge=0, le=100)
    weight_company_stage: int = Field(ge=0, le=100)


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    daily_drafts_cap: int = Field(ge=1)
    hourly_discover_cap: int = Field(ge=1)
    gemini_pro_rpd_ceiling: int = Field(ge=1)


class HnSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    months_lookback: int = Field(ge=1, le=12)


class QuerySource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    query: str


class BoardsSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    boards: list[str]


class Sources(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hn: HnSource
    wellfound: QuerySource
    yc_waas: QuerySource
    greenhouse: BoardsSource
    lever: BoardsSource
    ashby: BoardsSource


class JobPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate: Candidate
    target: Target
    skills: Skills
    scoring: Scoring
    limits: Limits
    sources: Sources

    @model_validator(mode="after")
    def _weights_sum_to_100(self) -> JobPreferences:
        total = (
            self.scoring.weight_skill_match
            + self.scoring.weight_seniority_match
            + self.scoring.weight_location_match
            + self.scoring.weight_company_stage
        )
        if total != 100:
            raise ValueError(f"scoring weights must sum to 100, got {total}")
        return self


def load_preferences(path: Path | str) -> JobPreferences:
    """Load and validate ``job_preferences.yaml``."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"preferences file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"preferences file must be a mapping, got {type(raw).__name__}")
    return JobPreferences.model_validate(raw)
