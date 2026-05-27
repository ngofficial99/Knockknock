← [Index](00-index.md) · [Prev: phase-01-db-schema.md](phase-01-db-schema.md) · [Next: phase-03-pipeline-hn.md](phase-03-pipeline-hn.md)

## Phase 2: Configuration Loading & Secret Manager

**Outcome:** A typed `Settings` object loads env vars (DSN, log level, mode flags), a `JobPreferences` Pydantic model loads `config/job_preferences.yaml`, and a `SecretsClient` resolves runtime secrets either from env (local) or Google Secret Manager (cloud). All three are unit-tested.

### Task 2.1: Pydantic settings (env-driven)

**Files:**
- Create: `src/knockknock/config/__init__.py`
- Create: `src/knockknock/config/settings.py`
- Create: `tests/test_config/__init__.py`
- Create: `tests/test_config/test_settings.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_config/__init__.py` (empty).

Create `tests/test_config/test_settings.py`:

```python
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
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_config/test_settings.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement settings**

Create `src/knockknock/config/__init__.py` (empty).

Create `src/knockknock/config/settings.py`:

```python
"""Runtime settings sourced from environment variables."""

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
```

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/test_config/test_settings.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/knockknock/config tests/test_config
git commit -m "feat(config): add Pydantic Settings from env vars"
```

### Task 2.2: Job preferences model + YAML loader

**Files:**
- Create: `src/knockknock/config/preferences.py`
- Create: `config/job_preferences.yaml`
- Create: `tests/test_config/test_preferences.py`
- Create: `tests/fixtures/preferences_sample.yaml`

- [ ] **Step 1: Write the seed YAML file (committed to repo)**

Create `config/job_preferences.yaml`:

```yaml
candidate:
  name: "Nishant Gupta"
  current_role: "Backend Engineer at Zeotap"
  years_experience: 4
  location: "Bengaluru, India"

target:
  locations:
    - "Bengaluru"
    - "Bangalore"
    - "Remote (India)"
  titles_allow:
    - "Software Engineer"
    - "Software Development Engineer"
    - "Backend Engineer"
    - "Full Stack Engineer"
    - "Platform Engineer"
  titles_deny:
    - "Manager"
    - "Lead"
    - "Director"
    - "Intern"
    - "Frontend Engineer"
  seniority_allow:
    - "Mid"
    - "Senior"
    - "SDE 2"
    - "SDE 3"
  company_size_allow:
    - "SEED"
    - "SERIES_A"
    - "SERIES_B"
    - "SERIES_C_PLUS"

skills:
  must_have_any:
    - "Python"
    - "Java"
    - "Go"
    - "Node.js"
    - "TypeScript"
  nice_to_have:
    - "Kafka"
    - "Kubernetes"
    - "AWS"
    - "GCP"
    - "Postgres"
    - "Redis"

scoring:
  min_score_to_draft: 70
  weight_skill_match: 40
  weight_seniority_match: 25
  weight_location_match: 20
  weight_company_stage: 15

limits:
  daily_drafts_cap: 40
  hourly_discover_cap: 50
  gemini_pro_rpd_ceiling: 500

sources:
  hn:
    enabled: true
    months_lookback: 2
  wellfound:
    enabled: true
    query: "backend OR python OR java location:bangalore"
  yc_waas:
    enabled: true
    query: "backend bangalore"
  greenhouse:
    enabled: true
    boards: ["razorpay", "swiggy", "cred", "groww"]
  lever:
    enabled: true
    boards: ["postman", "atlan"]
  ashby:
    enabled: true
    boards: ["zerodha"]
```

- [ ] **Step 2: Write failing test**

Create `tests/fixtures/preferences_sample.yaml`:

```yaml
candidate:
  name: "Test User"
  current_role: "Engineer"
  years_experience: 3
  location: "Bengaluru"
target:
  locations: ["Bengaluru"]
  titles_allow: ["Software Engineer"]
  titles_deny: ["Manager"]
  seniority_allow: ["Mid"]
  company_size_allow: ["SEED", "SERIES_A"]
skills:
  must_have_any: ["Python"]
  nice_to_have: []
scoring:
  min_score_to_draft: 70
  weight_skill_match: 40
  weight_seniority_match: 25
  weight_location_match: 20
  weight_company_stage: 15
limits:
  daily_drafts_cap: 40
  hourly_discover_cap: 50
  gemini_pro_rpd_ceiling: 500
sources:
  hn:
    enabled: true
    months_lookback: 1
  wellfound:
    enabled: false
    query: ""
  yc_waas:
    enabled: false
    query: ""
  greenhouse:
    enabled: false
    boards: []
  lever:
    enabled: false
    boards: []
  ashby:
    enabled: false
    boards: []
```

Create `tests/test_config/test_preferences.py`:

```python
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
                    "name": "x", "current_role": "x",
                    "years_experience": 1, "location": "x",
                },
                "target": {
                    "locations": ["x"], "titles_allow": ["x"], "titles_deny": [],
                    "seniority_allow": ["x"], "company_size_allow": ["SEED"],
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
                    "daily_drafts_cap": 1, "hourly_discover_cap": 1,
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
```

- [ ] **Step 3: Run test to verify failure**

```bash
uv run pytest tests/test_config/test_preferences.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Implement `preferences.py`**

Create `src/knockknock/config/preferences.py`:

```python
"""Typed loader for config/job_preferences.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from knockknock.db.enums import CompanySizeBucket
from knockknock.exceptions import ConfigError


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    current_role: str
    years_experience: int = Field(ge=0, le=50)
    location: str


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
    def _weights_sum_to_100(self) -> "JobPreferences":
        s = (
            self.scoring.weight_skill_match
            + self.scoring.weight_seniority_match
            + self.scoring.weight_location_match
            + self.scoring.weight_company_stage
        )
        if s != 100:
            raise ValueError(f"scoring weights must sum to 100, got {s}")
        return self


def load_preferences(path: Path | str) -> JobPreferences:
    """Load and validate `job_preferences.yaml`."""
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
```

- [ ] **Step 5: Run test to verify pass**

```bash
uv run pytest tests/test_config/test_preferences.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 6: Validate the real YAML file loads**

```bash
uv run python -c "from knockknock.config.preferences import load_preferences; print(load_preferences('config/job_preferences.yaml').candidate.name)"
```

Expected: prints `Nishant Gupta`.

- [ ] **Step 7: Commit**

```bash
git add src/knockknock/config/preferences.py config/job_preferences.yaml tests/test_config/test_preferences.py tests/fixtures/preferences_sample.yaml
git commit -m "feat(config): add JobPreferences YAML loader with validation"
```

### Task 2.3: Secret Manager client with env fallback

**Files:**
- Create: `src/knockknock/config/secrets.py`
- Create: `tests/test_config/test_secrets.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_config/test_secrets.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from knockknock.config.secrets import LocalEnvSecrets, SecretsClient, build_secrets_client
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


def test_build_secrets_client_cloud_requires_project(monkeypatch: pytest.MonkeyPatch) -> None:
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
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_config/test_secrets.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement secrets module**

Create `src/knockknock/config/secrets.py`:

```python
"""Secret resolver supporting local env (dev) and Google Secret Manager (cloud)."""

from __future__ import annotations

import os
from typing import Protocol

from google.cloud import secretmanager

from knockknock.config.settings import Settings
from knockknock.exceptions import ConfigError


class SecretsClient(Protocol):
    def get(self, name: str) -> str: ...


class LocalEnvSecrets:
    """Reads secrets from env vars `KNOCKKNOCK_SECRET_<UPPER_WITH_UNDERSCORES>`."""

    def get(self, name: str) -> str:
        env_key = "KNOCKKNOCK_SECRET_" + name.upper().replace("-", "_")
        value = os.environ.get(env_key)
        if not value:
            raise ConfigError(f"missing secret: {name} (set {env_key})")
        return value


class GoogleSecretManagerSecrets:
    """Resolves secrets from GSM under `projects/<id>/secrets/<name>/versions/latest`."""

    def __init__(self, project_id: str) -> None:
        self._project_id = project_id
        self._client = secretmanager.SecretManagerServiceClient()

    def get(self, name: str) -> str:
        resource = f"projects/{self._project_id}/secrets/{name}/versions/latest"
        response = self._client.access_secret_version(name=resource)
        return response.payload.data.decode("utf-8")


def build_secrets_client(settings: Settings) -> SecretsClient:
    """Return the right secrets client for the runtime."""
    if settings.runtime == "local":
        return LocalEnvSecrets()
    if not settings.gcp_project_id:
        raise ConfigError("runtime=cloud requires KNOCKKNOCK_GCP_PROJECT_ID (gcp_project_id)")
    return GoogleSecretManagerSecrets(settings.gcp_project_id)
```

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/test_config/test_secrets.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/knockknock/config/secrets.py tests/test_config/test_secrets.py
git commit -m "feat(config): add SecretsClient (env + GSM)"
```

### Task 2.4: Structlog setup

**Files:**
- Create: `src/knockknock/logging.py`
- Create: `tests/test_config/test_logging.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_config/test_logging.py`:

```python
from __future__ import annotations

import structlog

from knockknock.logging import configure_logging


def test_configure_logging_returns_logger() -> None:
    logger = configure_logging(level="INFO", json=False)
    assert logger is not None
    assert isinstance(logger, structlog.stdlib.BoundLogger)


def test_configure_logging_json_mode() -> None:
    logger = configure_logging(level="DEBUG", json=True)
    assert logger is not None
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_config/test_logging.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement logging**

Create `src/knockknock/logging.py`:

```python
"""Structured logging configuration."""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.stdlib import BoundLogger


def configure_logging(*, level: str = "INFO", json: bool = False) -> BoundLogger:
    """Configure structlog with stdlib bridge. Returns a root bound logger."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper()),
    )

    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if json:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    return structlog.get_logger("knockknock")
```

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/test_config/test_logging.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/knockknock/logging.py tests/test_config/test_logging.py
git commit -m "feat: add structlog logging configuration"
```

---

← [Index](00-index.md) · [Prev: phase-01-db-schema.md](phase-01-db-schema.md) · [Next: phase-03-pipeline-hn.md](phase-03-pipeline-hn.md)
