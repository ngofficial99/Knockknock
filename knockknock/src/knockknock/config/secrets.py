"""Secret resolver supporting local env (dev) and Google Secret Manager (cloud).

The choice between backends is driven by ``Settings.runtime``:

* ``local`` → :class:`LocalEnvSecrets` reads ``KNOCKKNOCK_SECRET_<NAME>`` from env.
* ``cloud`` → :class:`GoogleSecretManagerSecrets` calls GSM with
  ``projects/<gcp_project_id>/secrets/<name>/versions/latest``.

Both backends expose the same :class:`SecretsClient` protocol so call sites can
remain backend-agnostic.
"""

from __future__ import annotations

import os
from typing import Protocol

from google.cloud import secretmanager

from knockknock.config.settings import Settings
from knockknock.exceptions import ConfigError


class SecretsClient(Protocol):
    """Minimal contract for resolving a named secret to its string value."""

    def get(self, name: str) -> str: ...


class LocalEnvSecrets:
    """Reads secrets from env vars ``KNOCKKNOCK_SECRET_<UPPER_WITH_UNDERSCORES>``."""

    def get(self, name: str) -> str:
        env_key = "KNOCKKNOCK_SECRET_" + name.upper().replace("-", "_")
        value = os.environ.get(env_key)
        if not value:
            raise ConfigError(f"missing secret: {name} (set {env_key})")
        return value


class GoogleSecretManagerSecrets:
    """Resolves secrets from GSM at ``projects/<id>/secrets/<name>/versions/latest``."""

    def __init__(self, project_id: str) -> None:
        self._project_id = project_id
        self._client = secretmanager.SecretManagerServiceClient()

    def get(self, name: str) -> str:
        resource = f"projects/{self._project_id}/secrets/{name}/versions/latest"
        response = self._client.access_secret_version(name=resource)
        return str(response.payload.data.decode("utf-8"))


def build_secrets_client(settings: Settings) -> SecretsClient:
    """Return the right secrets client for the current runtime."""
    if settings.runtime == "local":
        return LocalEnvSecrets()
    if not settings.gcp_project_id:
        raise ConfigError("runtime=cloud requires KNOCKKNOCK_GCP_PROJECT_ID (gcp_project_id)")
    return GoogleSecretManagerSecrets(settings.gcp_project_id)
