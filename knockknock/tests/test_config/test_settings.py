from __future__ import annotations

import pytest

from knockknock.config.settings import Settings


def test_settings_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KNOCKKNOCK_DATABASE_URL", raising=False)
    with pytest.raises(ValueError):
        Settings()


def test_settings_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOCKKNOCK_DATABASE_URL", "postgresql+psycopg://u:p@h/db")
    monkeypatch.setenv("KNOCKKNOCK_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("KNOCKKNOCK_RUNTIME", "local")
    s = Settings()
    assert s.database_url.startswith("postgresql+psycopg")
    assert s.log_level == "DEBUG"
    assert s.runtime == "local"


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOCKKNOCK_DATABASE_URL", "postgresql+psycopg://u:p@h/db")
    s = Settings()
    assert s.log_level == "INFO"
    assert s.runtime == "local"
    assert s.preferences_path.endswith("job_preferences.yaml")


def test_settings_rejects_invalid_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOCKKNOCK_DATABASE_URL", "postgresql+psycopg://u:p@h/db")
    monkeypatch.setenv("KNOCKKNOCK_LOG_LEVEL", "TRACE")
    with pytest.raises(ValueError):
        Settings()


def test_settings_rejects_invalid_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOCKKNOCK_DATABASE_URL", "postgresql+psycopg://u:p@h/db")
    monkeypatch.setenv("KNOCKKNOCK_RUNTIME", "production")
    with pytest.raises(ValueError):
        Settings()
