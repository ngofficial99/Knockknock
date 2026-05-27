← [Index](00-index.md) · [Next: phase-01-db-schema.md](phase-01-db-schema.md)

## Phase 0: Repo Bootstrap, Tooling, CI Skeleton

**Outcome:** Empty repo on `main`, `uv` virtualenv, ruff + mypy + pre-commit + pytest configured, GitHub Actions CI passing on a trivial "hello" module, `.env`-driven Neon DSN reachable.

### Task 0.1: Initialise repo and `uv` project

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `.gitignore`
- Create: `README.md`

- [ ] **Step 1: Clone the repo locally**

```bash
cd /Users/nishantgupta/Documents/personal-project
git clone https://github.com/ngofficial99/Knockknock.git knockknock
cd knockknock
```

- [ ] **Step 2: Pin Python version**

Create `.python-version`:

```
3.11
```

- [ ] **Step 3: Initialise `uv` project**

```bash
uv init --name knockknock --package --no-readme
```

- [ ] **Step 4: Overwrite `pyproject.toml`**

```toml
[project]
name = "knockknock"
version = "0.1.0"
description = "Personalized job-application pipeline for Bangalore startups"
requires-python = ">=3.11,<3.13"
dependencies = [
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "sqlmodel>=0.0.22",
    "sqlalchemy>=2.0.30",
    "alembic>=1.13",
    "psycopg[binary]>=3.2",
    "asyncpg>=0.29",
    "httpx>=0.27",
    "tenacity>=8.5",
    "google-genai>=0.3",
    "google-api-python-client>=2.130",
    "google-auth>=2.30",
    "google-auth-oauthlib>=1.2",
    "google-cloud-secret-manager>=2.20",
    "python-telegram-bot>=21.3",
    "fastapi>=0.111",
    "uvicorn[standard]>=0.30",
    "playwright>=1.44",
    "selectolax>=0.3.21",
    "weasyprint>=62.3",
    "jinja2>=3.1",
    "typer>=0.12",
    "structlog>=24.2",
    "rich>=13.7",
    "pyyaml>=6.0",
]

[project.scripts]
knockknock = "knockknock.__main__:app"

[dependency-groups]
dev = [
    "pytest>=8.2",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5.0",
    "respx>=0.21",
    "factory-boy>=3.3",
    "freezegun>=1.5",
    "ruff>=0.5",
    "mypy>=1.10",
    "pre-commit>=3.7",
    "types-pyyaml>=6.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/knockknock"]

[tool.ruff]
line-length = 100
target-version = "py311"
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "N", "S", "C4", "SIM", "ARG", "RUF"]
ignore = ["S101"]  # allow assert in tests

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S", "ARG"]

[tool.mypy]
strict = true
python_version = "3.11"
mypy_path = "src"
packages = ["knockknock"]
plugins = ["pydantic.mypy"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "-ra --strict-markers --strict-config"
```

- [ ] **Step 5: Create `.gitignore`**

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
dist/
build/
*.egg-info/
.env
.env.local
*.db
*.sqlite3
.DS_Store
node_modules/
secrets/
```

- [ ] **Step 6: Create `README.md`**

```markdown
# Knockknock

Personalized job-application pipeline for Bangalore startups.

See `docs/superpowers/specs/2026-05-28-knockknock-design.md` for the design spec.

## Local setup

\`\`\`bash
uv sync
uv run pre-commit install
uv run pytest
\`\`\`
```

- [ ] **Step 7: Sync deps**

```bash
uv sync
```

Expected: virtualenv created at `.venv/`, `uv.lock` written.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock .python-version .gitignore README.md
git commit -m "chore: bootstrap uv project with deps"
```

### Task 0.2: Add ruff, mypy, pre-commit config

**Files:**
- Create: `.pre-commit-config.yaml`

- [ ] **Step 1: Write `.pre-commit-config.yaml`**

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.5.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files
        args: [--maxkb=500]
      - id: check-merge-conflict
      - id: detect-private-key
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.18.4
    hooks:
      - id: gitleaks
```

- [ ] **Step 2: Install hooks**

```bash
uv run pre-commit install
```

- [ ] **Step 3: Run hooks against all files**

```bash
uv run pre-commit run --all-files
```

Expected: all checks pass (or fix trivial whitespace and re-run).

- [ ] **Step 4: Commit**

```bash
git add .pre-commit-config.yaml
git commit -m "chore: add pre-commit hooks"
```

### Task 0.3: Add package skeleton + smoke test

**Files:**
- Create: `src/knockknock/__init__.py`
- Create: `src/knockknock/__main__.py`
- Create: `src/knockknock/exceptions.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_smoke.py`

- [ ] **Step 1: Write the failing smoke test**

Create `tests/test_smoke.py`:

```python
from knockknock import __version__


def test_version_is_string() -> None:
    assert isinstance(__version__, str)
    assert len(__version__) > 0
```

- [ ] **Step 2: Write conftest scaffold**

Create `tests/conftest.py`:

```python
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KNOCKKNOCK_DATABASE_URL", raising=False)
```

Create `tests/__init__.py` as empty file.

- [ ] **Step 3: Run test to see it fail**

```bash
uv run pytest tests/test_smoke.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'knockknock'`.

- [ ] **Step 4: Create the package**

Create `src/knockknock/__init__.py`:

```python
"""Knockknock: personalized job-application pipeline."""

__version__ = "0.1.0"
```

Create `src/knockknock/exceptions.py`:

```python
"""Top-level exception hierarchy."""

from __future__ import annotations


class KnockknockError(Exception):
    """Base for all Knockknock-raised errors."""


class ConfigError(KnockknockError):
    """Raised when configuration is missing or malformed."""


class ExternalServiceError(KnockknockError):
    """Raised when an external API call ultimately fails after retries."""


class RateLimitedError(ExternalServiceError):
    """Raised when an upstream provider returns a hard rate-limit response."""


class PipelineError(KnockknockError):
    """Raised when the pipeline cannot progress a job for a known reason."""
```

Create `src/knockknock/__main__.py`:

```python
"""CLI entrypoint; populated in Phase 11."""

from __future__ import annotations

import typer

app = typer.Typer(help="Knockknock CLI")


@app.command()
def version() -> None:
    """Print the package version."""
    from knockknock import __version__

    typer.echo(__version__)


if __name__ == "__main__":
    app()
```

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/test_smoke.py -v
```

Expected: PASS.

- [ ] **Step 6: Verify CLI works**

```bash
uv run knockknock version
```

Expected: prints `0.1.0`.

- [ ] **Step 7: Type-check and lint**

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
```

Expected: all clean.

- [ ] **Step 8: Commit**

```bash
git add src tests
git commit -m "feat: add package skeleton and smoke test"
```

### Task 0.4: GitHub Actions CI

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Write CI workflow**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v3
        with:
          version: "0.4.18"
          enable-cache: true

      - name: Pin Python
        run: uv python install 3.11

      - name: Install deps
        run: uv sync --frozen

      - name: Ruff lint
        run: uv run ruff check src tests

      - name: Ruff format
        run: uv run ruff format --check src tests

      - name: Mypy
        run: uv run mypy

      - name: Pytest
        run: uv run pytest --cov=knockknock --cov-report=term-missing
```

- [ ] **Step 2: Commit and push**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add lint+type+test workflow"
git push -u origin main
```

- [ ] **Step 3: Verify CI passes**

Open https://github.com/ngofficial99/Knockknock/actions and confirm the workflow run is green.

### Task 0.5: Neon connectivity probe (manual, no commit)

**Goal:** Confirm the Neon DSN from the user's Neon dashboard works before Phase 1 builds on it.

- [ ] **Step 1: Create a `.env` file locally (NOT committed; in `.gitignore` already)**

```
KNOCKKNOCK_DATABASE_URL=postgresql+psycopg://<user>:<password>@<host>/<db>?sslmode=require
```

- [ ] **Step 2: One-off connection check**

```bash
uv run python -c "import psycopg, os; conn = psycopg.connect(os.environ['KNOCKKNOCK_DATABASE_URL'].replace('postgresql+psycopg://','postgresql://')); print(conn.execute('select 1').fetchone()); conn.close()"
```

Expected: prints `(1,)`.

- [ ] **Step 3: Do NOT commit `.env`. Confirm with `git status` that `.env` is ignored.**

```bash
git status --ignored | grep .env
```

Expected: `.env` shown under Ignored files.

---

← [Index](00-index.md) · [Next: phase-01-db-schema.md](phase-01-db-schema.md)
