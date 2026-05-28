from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from knockknock.config.secrets import (
    LocalEnvSecrets,
    SecretsClient,
    build_secrets_client,
)
from knockknock.exceptions import ConfigError


def test_local_env_secrets_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOCKKNOCK_SECRET_GEMINI_API_KEY", "abc")
    client: SecretsClient = LocalEnvSecrets()
    assert client.get("gemini-api-key") == "abc"


def test_local_env_secrets_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KNOCKKNOCK_SECRET_MISSING_KEY", raising=False)
    client: SecretsClient = LocalEnvSecrets()
    with pytest.raises(ConfigError):
        client.get("missing-key")


def test_build_secrets_client_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOCKKNOCK_DATABASE_URL", "postgresql+psycopg://u:p@h/db")
    monkeypatch.setenv("KNOCKKNOCK_RUNTIME", "local")
    from knockknock.config.settings import Settings

    client = build_secrets_client(Settings())
    assert isinstance(client, LocalEnvSecrets)


def test_build_secrets_client_cloud_requires_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KNOCKKNOCK_DATABASE_URL", "postgresql+psycopg://u:p@h/db")
    monkeypatch.setenv("KNOCKKNOCK_RUNTIME", "cloud")
    monkeypatch.delenv("KNOCKKNOCK_GCP_PROJECT_ID", raising=False)
    from knockknock.config.settings import Settings

    with pytest.raises(ConfigError, match="gcp_project_id"):
        build_secrets_client(Settings())


def test_gsm_secrets_calls_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOCKKNOCK_DATABASE_URL", "postgresql+psycopg://u:p@h/db")
    monkeypatch.setenv("KNOCKKNOCK_RUNTIME", "cloud")
    monkeypatch.setenv("KNOCKKNOCK_GCP_PROJECT_ID", "my-proj")

    fake_response = MagicMock()
    fake_response.payload.data.decode.return_value = "secret-value"
    fake_client = MagicMock()
    fake_client.access_secret_version.return_value = fake_response

    with patch(
        "knockknock.config.secrets.secretmanager.SecretManagerServiceClient",
        return_value=fake_client,
    ):
        from knockknock.config.secrets import build_secrets_client
        from knockknock.config.settings import Settings

        client = build_secrets_client(Settings())
        value = client.get("gemini-api-key")
        assert value == "secret-value"
        fake_client.access_secret_version.assert_called_once_with(
            name="projects/my-proj/secrets/gemini-api-key/versions/latest"
        )
