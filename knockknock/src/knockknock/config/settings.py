"""Runtime settings sourced from environment variables.

All variables are prefixed ``KNOCKKNOCK_``. ``database_url`` is required;
everything else has a sensible default. ``runtime`` switches between
local-env-vars-for-secrets and Google Secret Manager (see
``knockknock.config.secrets.build_secrets_client``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide settings; loaded once at startup."""

    model_config = SettingsConfigDict(
        env_prefix="KNOCKKNOCK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = Field(min_length=10)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    runtime: Literal["local", "cloud"] = "local"
    preferences_path: str = "config/job_preferences.yaml"
    blacklist_path: str = "config/blacklist.yaml"
    gcp_project_id: str | None = None
