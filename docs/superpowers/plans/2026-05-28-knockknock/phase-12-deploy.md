← [Index](00-index.md) · [Prev: phase-11-cli-digest.md](phase-11-cli-digest.md)

## Phase 12: Cloud Deployment (Cloud Run + Scheduler + Secret Manager + GitHub Actions WIF)

**Outcome:** Two production-grade deployables live on Google Cloud:
1. **`knockknock-pipeline`** — a Cloud Run **Job** triggered every hour by Cloud Scheduler. Each run executes one `knockknock pipeline run --once`, then exits.
2. **`knockknock-telegram`** — a Cloud Run **Service** (always-on, scales to one), serving the Telegram webhook at `POST /telegram/webhook`.

Both pull secrets from Google Secret Manager via the Cloud Run runtime, connect to Neon Postgres over its pooled connection string, and are built/deployed by GitHub Actions on push to `main` using **Workload Identity Federation** (no long-lived JSON keys). After this phase, you stop running anything locally except `knockknock status` / `errors` / `retry`.

### Prerequisites

- Phases 0–11 complete; CI green.
- A Google Cloud project with billing enabled. Pick a project ID (e.g. `knockknock-prod-2026`) and a region (we use `asia-south1` for low latency to Bangalore/Neon-Singapore; substitute as needed).
- `gcloud` CLI installed locally and authenticated (`gcloud auth login`).
- A Neon Postgres database with both **pooled** (port 6543, for the bot's async workload) and **direct** (port 5432, for the pipeline's migrations) connection URIs at hand.
- A Telegram bot already created via @BotFather; you have its token + your own chat ID.
- Gmail OAuth refresh token already generated locally (one-time consent flow; see appendix at end of this phase).

> **Note on cost & free tier:** Cloud Run Job hourly invocation + a single 256 MiB Service stays comfortably inside the GCP free tier (~$0–3/month). Neon free tier (3 GB) covers v1 easily. Secret Manager is free up to 6 secret versions per secret per month.

### Task 12.1: One-time GCP project bootstrap

**Files:**
- Create: `infra/bootstrap.sh`
- Create: `infra/README.md`

This script is **run once, manually, by the operator** (not by CI). It enables APIs, creates the Artifact Registry, the runtime service accounts, and the Workload Identity Pool/Provider for GitHub Actions. After this runs, every subsequent change ships through CI.

- [ ] **Step 1: Author the bootstrap script**

Create `infra/bootstrap.sh`:

```bash
#!/usr/bin/env bash
# One-time GCP project bootstrap. Idempotent: safe to re-run.
#
# Required env:
#   PROJECT_ID         e.g. knockknock-prod-2026
#   REGION             e.g. asia-south1
#   GITHUB_OWNER_REPO  e.g. ngofficial99/Knockknock

set -euo pipefail
: "${PROJECT_ID:?set PROJECT_ID}"
: "${REGION:?set REGION}"
: "${GITHUB_OWNER_REPO:?set GITHUB_OWNER_REPO}"

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
PIPELINE_SA="knockknock-pipeline@${PROJECT_ID}.iam.gserviceaccount.com"
TELEGRAM_SA="knockknock-telegram@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULER_SA="knockknock-scheduler@${PROJECT_ID}.iam.gserviceaccount.com"
DEPLOYER_SA="knockknock-deployer@${PROJECT_ID}.iam.gserviceaccount.com"
POOL_ID="github-pool"
PROVIDER_ID="github-provider"
AR_REPO="knockknock"

echo "==> Enabling APIs"
gcloud services enable \
  run.googleapis.com \
  cloudscheduler.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  cloudbuild.googleapis.com \
  --project "$PROJECT_ID"

echo "==> Creating Artifact Registry"
gcloud artifacts repositories describe "$AR_REPO" \
  --project "$PROJECT_ID" --location "$REGION" >/dev/null 2>&1 || \
gcloud artifacts repositories create "$AR_REPO" \
  --repository-format=docker \
  --location="$REGION" \
  --project="$PROJECT_ID"

create_sa () {
  local name="$1" display="$2"
  gcloud iam service-accounts describe "${name}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --project "$PROJECT_ID" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$name" \
    --display-name="$display" --project="$PROJECT_ID"
}

echo "==> Creating service accounts"
create_sa knockknock-pipeline "Pipeline Cloud Run Job"
create_sa knockknock-telegram "Telegram Cloud Run Service"
create_sa knockknock-scheduler "Cloud Scheduler invoker"
create_sa knockknock-deployer "GitHub Actions deployer"

echo "==> Granting runtime SAs access to Secret Manager"
for SA in "$PIPELINE_SA" "$TELEGRAM_SA"; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA}" \
    --role="roles/secretmanager.secretAccessor" --condition=None
done

echo "==> Granting Scheduler SA permission to invoke pipeline job"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SCHEDULER_SA}" \
  --role="roles/run.invoker" --condition=None

echo "==> Granting deployer SA permission to push images + deploy Cloud Run"
for ROLE in \
  roles/run.admin \
  roles/artifactregistry.writer \
  roles/iam.serviceAccountUser \
  roles/cloudscheduler.admin; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${DEPLOYER_SA}" \
    --role="$ROLE" --condition=None
done

echo "==> Configuring Workload Identity Federation for GitHub Actions"
gcloud iam workload-identity-pools describe "$POOL_ID" \
  --project "$PROJECT_ID" --location=global >/dev/null 2>&1 || \
gcloud iam workload-identity-pools create "$POOL_ID" \
  --project="$PROJECT_ID" --location=global \
  --display-name="GitHub Actions pool"

gcloud iam workload-identity-pools providers describe "$PROVIDER_ID" \
  --project="$PROJECT_ID" --location=global \
  --workload-identity-pool="$POOL_ID" >/dev/null 2>&1 || \
gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_ID" \
  --project="$PROJECT_ID" --location=global \
  --workload-identity-pool="$POOL_ID" \
  --display-name="GitHub OIDC" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository == '${GITHUB_OWNER_REPO}'" \
  --issuer-uri="https://token.actions.githubusercontent.com"

POOL_RESOURCE="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}"

echo "==> Allowing GitHub Actions to impersonate the deployer SA"
gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER_SA" \
  --project="$PROJECT_ID" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/${POOL_RESOURCE}/attribute.repository/${GITHUB_OWNER_REPO}"

echo ""
echo "Done. Set these as GitHub Actions repository variables:"
echo "  GCP_PROJECT_ID=${PROJECT_ID}"
echo "  GCP_REGION=${REGION}"
echo "  GCP_WORKLOAD_IDENTITY_PROVIDER=projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID}"
echo "  GCP_DEPLOYER_SA=${DEPLOYER_SA}"
echo "  GCP_PIPELINE_SA=${PIPELINE_SA}"
echo "  GCP_TELEGRAM_SA=${TELEGRAM_SA}"
echo "  GCP_SCHEDULER_SA=${SCHEDULER_SA}"
echo "  GCP_AR_REPO=${AR_REPO}"
```

- [ ] **Step 2: Author `infra/README.md`**

Create `infra/README.md`:

```markdown
# Infra

One-time, manual scripts. Not run by CI.

## Bootstrap

```bash
export PROJECT_ID=knockknock-prod-2026
export REGION=asia-south1
export GITHUB_OWNER_REPO=ngofficial99/Knockknock
chmod +x infra/bootstrap.sh
./infra/bootstrap.sh
```

After this completes, copy the printed `GCP_*` values into the GitHub repo's
*Settings → Secrets and variables → Actions → Variables* tab. Then run
`infra/secrets.sh` (Task 12.2) to populate Secret Manager.

## Files
- `bootstrap.sh` — enables APIs, creates SAs + Artifact Registry + WIF pool.
- `secrets.sh` — creates Secret Manager secrets and seeds them.
- `cloud_scheduler.sh` — creates the hourly Cloud Scheduler trigger.
```

- [ ] **Step 3: Commit**

```bash
chmod +x infra/bootstrap.sh
git add infra/bootstrap.sh infra/README.md
git commit -m "infra: add one-time GCP bootstrap script (APIs, SAs, Artifact Registry, WIF)"
```

### Task 12.2: Secret Manager seeding script

**Files:**
- Create: `infra/secrets.sh`

Defines the canonical secret names + seeds them. Re-runnable to rotate values.

- [ ] **Step 1: Author the script**

Create `infra/secrets.sh`:

```bash
#!/usr/bin/env bash
# Create/update Secret Manager secrets. Re-run to rotate.
#
# Required env:
#   PROJECT_ID
# Optional env (each `_VAL` skipped if unset):
#   DATABASE_URL_VAL
#   DATABASE_URL_DIRECT_VAL
#   GEMINI_API_KEY_VAL
#   APOLLO_API_KEY_VAL
#   HUNTER_API_KEY_VAL
#   GOOGLE_OAUTH_CLIENT_ID_VAL
#   GOOGLE_OAUTH_CLIENT_SECRET_VAL
#   GMAIL_REFRESH_TOKEN_VAL
#   TELEGRAM_BOT_TOKEN_VAL
#   TELEGRAM_ADMIN_CHAT_ID_VAL
#   TELEGRAM_WEBHOOK_SECRET_VAL
#   TELEGRAM_CALLBACK_SECRET_VAL

set -euo pipefail
: "${PROJECT_ID:?set PROJECT_ID}"

SECRETS=(
  DATABASE_URL
  DATABASE_URL_DIRECT
  GEMINI_API_KEY
  APOLLO_API_KEY
  HUNTER_API_KEY
  GOOGLE_OAUTH_CLIENT_ID
  GOOGLE_OAUTH_CLIENT_SECRET
  GMAIL_REFRESH_TOKEN
  TELEGRAM_BOT_TOKEN
  TELEGRAM_ADMIN_CHAT_ID
  TELEGRAM_WEBHOOK_SECRET
  TELEGRAM_CALLBACK_SECRET
)

for NAME in "${SECRETS[@]}"; do
  gcloud secrets describe "$NAME" --project="$PROJECT_ID" >/dev/null 2>&1 || \
    gcloud secrets create "$NAME" --project="$PROJECT_ID" --replication-policy=automatic

  VAR="${NAME}_VAL"
  VALUE="${!VAR-}"
  if [[ -n "$VALUE" ]]; then
    echo "==> Adding new version to $NAME"
    printf '%s' "$VALUE" | gcloud secrets versions add "$NAME" \
      --project="$PROJECT_ID" --data-file=-
  else
    echo "    Skipping $NAME (no $VAR set)"
  fi
done

echo ""
echo "Tip: generate fresh signing secrets with:"
echo "  python -c \"import secrets; print(secrets.token_urlsafe(32))\""
```

- [ ] **Step 2: Document in README**

Append to `infra/README.md`:

```markdown
## Seed/rotate secrets

```bash
export PROJECT_ID=knockknock-prod-2026

# Generate signing secrets once:
TELEGRAM_WEBHOOK_SECRET_VAL=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
TELEGRAM_CALLBACK_SECRET_VAL=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')

# Set the rest from your provider dashboards (Neon, Gemini, Apollo, Hunter, GCP OAuth, BotFather)
export DATABASE_URL_VAL="postgresql+psycopg://USER:PASS@ep-xxx-pooler.neon.tech/knockknock?sslmode=require"
export DATABASE_URL_DIRECT_VAL="postgresql+psycopg://USER:PASS@ep-xxx.neon.tech/knockknock?sslmode=require"
export GEMINI_API_KEY_VAL="..."
# ...etc

chmod +x infra/secrets.sh
./infra/secrets.sh
```
```

- [ ] **Step 3: Commit**

```bash
chmod +x infra/secrets.sh
git add infra/secrets.sh infra/README.md
git commit -m "infra: add Secret Manager seeding script"
```

### Task 12.3: Pipeline Dockerfile

**Files:**
- Create: `Dockerfile.pipeline`
- Modify: `.dockerignore`

The pipeline image needs:
- Python 3.11
- Playwright Chromium + system deps (heavy: ~600 MB)
- The full source tree
- An entrypoint that runs migrations then `knockknock pipeline run --once`

- [ ] **Step 1: Author the Dockerfile**

Create `Dockerfile.pipeline`:

```dockerfile
# syntax=docker/dockerfile:1.7
FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy AS runtime

# Non-root user
RUN useradd -m -u 1001 -s /bin/bash app
WORKDIR /app

# Install uv (used to install deps from uv.lock)
COPY --from=ghcr.io/astral-sh/uv:0.4.18 /uv /usr/local/bin/uv

# Copy lockfile + project metadata first so layer caches deps
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Now copy the source
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY resumes/manifest.yaml ./resumes/manifest.yaml
COPY config/ ./config/

# Install the project itself (now that src is present)
RUN uv sync --frozen --no-dev

# Drop privileges
RUN chown -R app:app /app
USER app

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LOG_FORMAT=json

# Pipeline Job entrypoint: run migrations, then one pass.
# `exec` so signals propagate (Cloud Run sends SIGTERM on cancel).
ENTRYPOINT ["/bin/bash", "-c", "uv run alembic upgrade head && exec uv run knockknock pipeline run --once"]
```

- [ ] **Step 2: Tighten `.dockerignore`**

In `.dockerignore`, ensure:

```
.git/
.github/
.venv/
__pycache__/
tests/
docs/
htmlcov/
.coverage
*.pyc
*.pyo
resumes/*.pdf
infra/
```

Resume PDFs are excluded — the image is **public artifact** to operators and CI; we don't want personal documents shipped inside it. Phase 12.6 mounts them as a Secret Manager-backed volume instead.

> **Note on resume PDFs:** Phase 7 stored resumes at `resumes/*.pdf`. For production, store each PDF in Secret Manager (one secret per variant, e.g. `RESUME_BACKEND_LLM`) and mount via Cloud Run's `--update-secrets` flag with `/app/resumes/backend-llm.pdf=RESUME_BACKEND_LLM:latest`. Manifest paths are relative to `/app/resumes/` and match.

- [ ] **Step 3: Local build smoke**

```bash
docker build -f Dockerfile.pipeline -t knockknock-pipeline:dev .
docker run --rm \
  -e DATABASE_URL="$LOCAL_DATABASE_URL" \
  -e GEMINI_API_KEY=... \
  -e ... \
  knockknock-pipeline:dev
```

Expected: image builds; the run will likely fail without secrets but you should see `alembic upgrade head` succeed.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile.pipeline .dockerignore
git commit -m "build(pipeline): add Playwright-based Dockerfile.pipeline"
```

### Task 12.4: Telegram bot Dockerfile

**Files:**
- Create: `Dockerfile.telegram`

The telegram image is much smaller — no Playwright, no Alembic, just FastAPI + uvicorn.

- [ ] **Step 1: Author the Dockerfile**

Create `Dockerfile.telegram`:

```dockerfile
# syntax=docker/dockerfile:1.7
FROM python:3.11-slim-bookworm AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1001 -s /bin/bash app
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.4.18 /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project --extra telegram

COPY src/ ./src/

RUN uv sync --frozen --no-dev --extra telegram \
    && chown -R app:app /app

USER app

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LOG_FORMAT=json \
    PORT=8080

EXPOSE 8080

# Cloud Run injects $PORT; uvicorn binds 0.0.0.0
ENTRYPOINT ["/bin/bash", "-c", "exec uv run python -m knockknock.telegram_bot"]
```

> **Note on `--extra telegram`:** add a `[project.optional-dependencies] telegram = [...]` group to `pyproject.toml` containing only the bot-runtime deps (`fastapi`, `uvicorn`, `python-telegram-bot`, `psycopg`, `sqlalchemy`, `pydantic`, `structlog`). This keeps the telegram image small (~150 MB vs ~1.2 GB for the pipeline). The pipeline image installs the full default deps.

- [ ] **Step 2: Update `pyproject.toml`**

In `pyproject.toml`, add:

```toml
[project.optional-dependencies]
telegram = [
  "fastapi>=0.110",
  "uvicorn[standard]>=0.30",
  "python-telegram-bot>=21.0",
  "psycopg>=3.1",
  "sqlalchemy>=2.0",
  "sqlmodel>=0.0.16",
  "pydantic>=2.6",
  "pydantic-settings>=2.2",
  "structlog>=24.1",
  "google-cloud-secret-manager>=2.20",
  "google-api-python-client>=2.130",
  "google-auth>=2.30",
]
```

- [ ] **Step 3: Local build smoke**

```bash
docker build -f Dockerfile.telegram -t knockknock-telegram:dev .
docker run --rm -p 8080:8080 \
  -e DATABASE_URL=... \
  -e TELEGRAM_BOT_TOKEN=... \
  -e TELEGRAM_WEBHOOK_SECRET=... \
  knockknock-telegram:dev
```

Then `curl localhost:8080/healthz` → `{"ok": true}`.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile.telegram pyproject.toml
git commit -m "build(telegram): add slim FastAPI Dockerfile.telegram with extras"
```

### Task 12.5: Secret-driven Settings adaptation

**Files:**
- Modify: `src/knockknock/config/settings.py`
- Modify: `src/knockknock/config/secrets.py`

In production, the runtime SA reads secrets via Cloud Run's `--update-secrets` flag which maps secrets to env vars. So our `Settings` and `SecretsClient` need to prefer env vars when present, and only fall back to direct Secret Manager API calls when running locally with a `GOOGLE_CLOUD_PROJECT` set.

- [ ] **Step 1: Adapt `SecretsClient`**

In `src/knockknock/config/secrets.py`, ensure `build_secrets_client` returns an env-first impl:

```python
"""Secret resolution: env first, then Google Secret Manager."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import structlog

log = structlog.get_logger(__name__)


class SecretsClient(Protocol):
    def get(self, name: str) -> str: ...


@dataclass(slots=True)
class EnvFirstSecretsClient:
    project_id: str | None

    def get(self, name: str) -> str:
        env_value = os.environ.get(name)
        if env_value:
            return env_value
        if not self.project_id:
            raise KeyError(f"Secret {name!r} not in env and no GOOGLE_CLOUD_PROJECT set")
        # Lazy import so unit tests don't need the dep
        from google.cloud import secretmanager  # noqa: PLC0415

        client = secretmanager.SecretManagerServiceClient()
        path = client.secret_version_path(self.project_id, name, "latest")
        log.info("secrets.fetch", name=name, source="gsm")
        response = client.access_secret_version(request={"name": path})
        return response.payload.data.decode("utf-8")


def build_secrets_client(settings) -> SecretsClient:
    return EnvFirstSecretsClient(project_id=settings.google_cloud_project)
```

- [ ] **Step 2: Add `google_cloud_project` to Settings**

In `src/knockknock/config/settings.py`:

```python
google_cloud_project: str | None = Field(default=None, alias="GOOGLE_CLOUD_PROJECT")
```

- [ ] **Step 3: Test**

In `tests/test_config/test_secrets.py`, add:

```python
def test_env_first_secrets_client_prefers_env(monkeypatch) -> None:
    from knockknock.config.secrets import EnvFirstSecretsClient

    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    client = EnvFirstSecretsClient(project_id=None)
    assert client.get("GEMINI_API_KEY") == "from-env"


def test_env_first_secrets_client_errors_without_project(monkeypatch) -> None:
    from knockknock.config.secrets import EnvFirstSecretsClient

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = EnvFirstSecretsClient(project_id=None)
    import pytest

    with pytest.raises(KeyError):
        client.get("GEMINI_API_KEY")
```

```bash
uv run pytest tests/test_config/test_secrets.py -v
```

- [ ] **Step 4: Commit**

```bash
git add src/knockknock/config/settings.py src/knockknock/config/secrets.py tests/test_config/test_secrets.py
git commit -m "feat(config): env-first SecretsClient for Cloud Run secret-env injection"
```

### Task 12.6: GitHub Actions deploy workflow

**Files:**
- Create: `.github/workflows/deploy.yml`

Builds both images, pushes to Artifact Registry, then deploys the Cloud Run Job + Service. Triggered on push to `main` after CI passes.

- [ ] **Step 1: Author the workflow**

Create `.github/workflows/deploy.yml`:

```yaml
name: Deploy

on:
  push:
    branches: [main]
  workflow_dispatch: {}

permissions:
  contents: read
  id-token: write   # required for Workload Identity Federation

concurrency:
  group: deploy-${{ github.ref }}
  cancel-in-progress: false

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    needs: []
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Authenticate to Google Cloud (WIF)
        id: auth
        uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ vars.GCP_WORKLOAD_IDENTITY_PROVIDER }}
          service_account: ${{ vars.GCP_DEPLOYER_SA }}

      - name: Set up gcloud
        uses: google-github-actions/setup-gcloud@v2

      - name: Configure Docker for Artifact Registry
        run: gcloud auth configure-docker ${{ vars.GCP_REGION }}-docker.pkg.dev --quiet

      - name: Compute image tags
        id: tags
        run: |
          SHA=$(git rev-parse --short HEAD)
          BASE="${{ vars.GCP_REGION }}-docker.pkg.dev/${{ vars.GCP_PROJECT_ID }}/${{ vars.GCP_AR_REPO }}"
          echo "pipeline_image=${BASE}/pipeline:${SHA}" >> "$GITHUB_OUTPUT"
          echo "pipeline_image_latest=${BASE}/pipeline:latest" >> "$GITHUB_OUTPUT"
          echo "telegram_image=${BASE}/telegram:${SHA}" >> "$GITHUB_OUTPUT"
          echo "telegram_image_latest=${BASE}/telegram:latest" >> "$GITHUB_OUTPUT"

      - name: Build pipeline image
        run: |
          docker build -f Dockerfile.pipeline \
            -t ${{ steps.tags.outputs.pipeline_image }} \
            -t ${{ steps.tags.outputs.pipeline_image_latest }} .

      - name: Build telegram image
        run: |
          docker build -f Dockerfile.telegram \
            -t ${{ steps.tags.outputs.telegram_image }} \
            -t ${{ steps.tags.outputs.telegram_image_latest }} .

      - name: Push pipeline image
        run: |
          docker push ${{ steps.tags.outputs.pipeline_image }}
          docker push ${{ steps.tags.outputs.pipeline_image_latest }}

      - name: Push telegram image
        run: |
          docker push ${{ steps.tags.outputs.telegram_image }}
          docker push ${{ steps.tags.outputs.telegram_image_latest }}

      - name: Deploy Cloud Run Job (pipeline)
        run: |
          gcloud run jobs deploy knockknock-pipeline \
            --image=${{ steps.tags.outputs.pipeline_image }} \
            --region=${{ vars.GCP_REGION }} \
            --service-account=${{ vars.GCP_PIPELINE_SA }} \
            --task-timeout=30m \
            --max-retries=1 \
            --memory=2Gi \
            --cpu=2 \
            --set-env-vars=GOOGLE_CLOUD_PROJECT=${{ vars.GCP_PROJECT_ID }},LOG_FORMAT=json,LOG_LEVEL=INFO \
            --update-secrets=DATABASE_URL=DATABASE_URL:latest,DATABASE_URL_DIRECT=DATABASE_URL_DIRECT:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest,APOLLO_API_KEY=APOLLO_API_KEY:latest,HUNTER_API_KEY=HUNTER_API_KEY:latest,GOOGLE_OAUTH_CLIENT_ID=GOOGLE_OAUTH_CLIENT_ID:latest,GOOGLE_OAUTH_CLIENT_SECRET=GOOGLE_OAUTH_CLIENT_SECRET:latest,GMAIL_REFRESH_TOKEN=GMAIL_REFRESH_TOKEN:latest,TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest,TELEGRAM_ADMIN_CHAT_ID=TELEGRAM_ADMIN_CHAT_ID:latest,TELEGRAM_CALLBACK_SECRET=TELEGRAM_CALLBACK_SECRET:latest

      - name: Deploy Cloud Run Service (telegram-bot)
        run: |
          gcloud run deploy knockknock-telegram \
            --image=${{ steps.tags.outputs.telegram_image }} \
            --region=${{ vars.GCP_REGION }} \
            --service-account=${{ vars.GCP_TELEGRAM_SA }} \
            --memory=512Mi \
            --cpu=1 \
            --min-instances=1 \
            --max-instances=1 \
            --concurrency=20 \
            --allow-unauthenticated \
            --set-env-vars=GOOGLE_CLOUD_PROJECT=${{ vars.GCP_PROJECT_ID }},LOG_FORMAT=json,LOG_LEVEL=INFO \
            --update-secrets=DATABASE_URL=DATABASE_URL:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest,GOOGLE_OAUTH_CLIENT_ID=GOOGLE_OAUTH_CLIENT_ID:latest,GOOGLE_OAUTH_CLIENT_SECRET=GOOGLE_OAUTH_CLIENT_SECRET:latest,GMAIL_REFRESH_TOKEN=GMAIL_REFRESH_TOKEN:latest,TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest,TELEGRAM_ADMIN_CHAT_ID=TELEGRAM_ADMIN_CHAT_ID:latest,TELEGRAM_WEBHOOK_SECRET=TELEGRAM_WEBHOOK_SECRET:latest,TELEGRAM_CALLBACK_SECRET=TELEGRAM_CALLBACK_SECRET:latest

      - name: Print service URL
        id: url
        run: |
          URL=$(gcloud run services describe knockknock-telegram \
            --region=${{ vars.GCP_REGION }} --format='value(status.url)')
          echo "service_url=$URL" >> "$GITHUB_OUTPUT"
          echo "Telegram webhook URL: $URL/telegram/webhook"
```

> **Note on `--allow-unauthenticated`:** the Telegram webhook is a public POST endpoint by definition. Auth is layered on with the `X-Telegram-Bot-Api-Secret-Token` header check Phase 9 added; only requests signed with the webhook secret are accepted.

> **Note on `--max-instances=1` for telegram:** Webhook callback handling must be serialized to avoid double-handling an APPROVE click. With Cloud Run min=1/max=1 we get a single warm instance.

- [ ] **Step 2: Verify CI workflow won't block deploy**

Confirm `.github/workflows/ci.yml` (from Phase 0) uses `concurrency` group `ci-${{ github.ref }}` separate from `deploy-`, so deploy isn't cancelled when CI re-runs.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "ci(deploy): add build + deploy workflow (Cloud Run Job + Service via WIF)"
```

### Task 12.7: Cloud Scheduler hourly trigger

**Files:**
- Create: `infra/cloud_scheduler.sh`

After the deploy workflow runs once, the Cloud Run Job exists; this script wires Cloud Scheduler to invoke it hourly. Run once manually.

- [ ] **Step 1: Author the script**

Create `infra/cloud_scheduler.sh`:

```bash
#!/usr/bin/env bash
# Create (or update) the hourly Cloud Scheduler trigger for the pipeline job.
#
# Required env:
#   PROJECT_ID
#   REGION
#   SCHEDULER_SA   (knockknock-scheduler@${PROJECT_ID}.iam.gserviceaccount.com)

set -euo pipefail
: "${PROJECT_ID:?set PROJECT_ID}"
: "${REGION:?set REGION}"
: "${SCHEDULER_SA:?set SCHEDULER_SA}"

JOB_NAME="knockknock-pipeline-hourly"
JOB_URL="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/knockknock-pipeline:run"

ARGS=(
  --project="$PROJECT_ID"
  --location="$REGION"
  --schedule="0 * * * *"
  --time-zone="Asia/Kolkata"
  --uri="$JOB_URL"
  --http-method=POST
  --oauth-service-account-email="$SCHEDULER_SA"
  --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
  --attempt-deadline=120s
)

if gcloud scheduler jobs describe "$JOB_NAME" --project="$PROJECT_ID" --location="$REGION" >/dev/null 2>&1; then
  echo "==> Updating existing scheduler job"
  gcloud scheduler jobs update http "$JOB_NAME" "${ARGS[@]}"
else
  echo "==> Creating scheduler job"
  gcloud scheduler jobs create http "$JOB_NAME" "${ARGS[@]}"
fi

echo "Done. Pause/resume:"
echo "  gcloud scheduler jobs pause  ${JOB_NAME} --location=${REGION} --project=${PROJECT_ID}"
echo "  gcloud scheduler jobs resume ${JOB_NAME} --location=${REGION} --project=${PROJECT_ID}"
echo "Manual trigger:"
echo "  gcloud scheduler jobs run    ${JOB_NAME} --location=${REGION} --project=${PROJECT_ID}"
```

- [ ] **Step 2: Document in README**

Append to `infra/README.md`:

```markdown
## Hourly trigger

After the first successful deploy, run once:

```bash
export PROJECT_ID=knockknock-prod-2026
export REGION=asia-south1
export SCHEDULER_SA=knockknock-scheduler@${PROJECT_ID}.iam.gserviceaccount.com

chmod +x infra/cloud_scheduler.sh
./infra/cloud_scheduler.sh
```

Schedule is `0 * * * *` in `Asia/Kolkata` — top of every hour, IST.
```

- [ ] **Step 3: Commit**

```bash
chmod +x infra/cloud_scheduler.sh
git add infra/cloud_scheduler.sh infra/README.md
git commit -m "infra: add Cloud Scheduler hourly trigger script"
```

### Task 12.8: Register Telegram webhook

**Files:**
- Create: `infra/register_telegram_webhook.sh`

After deploy, Telegram needs to be told the new webhook URL.

- [ ] **Step 1: Author the script**

Create `infra/register_telegram_webhook.sh`:

```bash
#!/usr/bin/env bash
# Register the Cloud Run Service URL with Telegram. Idempotent.
#
# Required env:
#   PROJECT_ID
#   REGION
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_WEBHOOK_SECRET

set -euo pipefail
: "${PROJECT_ID:?set PROJECT_ID}"
: "${REGION:?set REGION}"
: "${TELEGRAM_BOT_TOKEN:?set TELEGRAM_BOT_TOKEN}"
: "${TELEGRAM_WEBHOOK_SECRET:?set TELEGRAM_WEBHOOK_SECRET}"

URL=$(gcloud run services describe knockknock-telegram \
  --region="$REGION" --project="$PROJECT_ID" --format='value(status.url)')
WEBHOOK_URL="${URL}/telegram/webhook"

echo "==> Setting webhook to ${WEBHOOK_URL}"
curl -fsS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -d "url=${WEBHOOK_URL}" \
  -d "secret_token=${TELEGRAM_WEBHOOK_SECRET}" \
  -d "allowed_updates=[\"callback_query\",\"message\"]" \
  -d "drop_pending_updates=true"

echo ""
echo "==> Verifying webhook"
curl -fsS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo" | python -m json.tool
```

- [ ] **Step 2: Commit**

```bash
chmod +x infra/register_telegram_webhook.sh
git add infra/register_telegram_webhook.sh
git commit -m "infra: add script to register Telegram webhook after deploy"
```

### Task 12.9: Gmail OAuth refresh-token helper (one-time)

**Files:**
- Create: `infra/gmail_oauth_bootstrap.py`

Generating a Gmail refresh token requires a one-time browser consent. This script runs locally, prints the token, and exits. Operator then plugs the value into `infra/secrets.sh`.

- [ ] **Step 1: Author the helper**

Create `infra/gmail_oauth_bootstrap.py`:

```python
"""One-time helper: generate a Gmail OAuth refresh token.

Run locally (not in CI):

    GOOGLE_OAUTH_CLIENT_ID=... GOOGLE_OAUTH_CLIENT_SECRET=... \\
        uv run python infra/gmail_oauth_bootstrap.py

Pastes a URL into stdout; visit it, approve the scopes, paste the resulting
authorization code back into the terminal. The refresh token is printed at the
end; copy it into Secret Manager via `infra/secrets.sh`.
"""

from __future__ import annotations

import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
]


def main() -> int:
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    if not (client_id and client_secret):
        print("Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET first.", file=sys.stderr)
        return 2

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        SCOPES,
    )
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    if not creds.refresh_token:
        print("No refresh_token returned. Did you specify prompt=consent?", file=sys.stderr)
        return 3
    print("")
    print("=" * 70)
    print(f"Refresh token: {creds.refresh_token}")
    print("=" * 70)
    print("")
    print("Now run:")
    print(f'  GMAIL_REFRESH_TOKEN_VAL="{creds.refresh_token}" ./infra/secrets.sh')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Document**

Append to `infra/README.md`:

```markdown
## Gmail OAuth bootstrap (one-time)

```bash
export GOOGLE_OAUTH_CLIENT_ID=...
export GOOGLE_OAUTH_CLIENT_SECRET=...
uv run python infra/gmail_oauth_bootstrap.py
```

Opens a browser, asks you to grant `gmail.compose` + `gmail.send` scopes,
prints a refresh token. Copy it into Secret Manager via `infra/secrets.sh`.
```

- [ ] **Step 3: Commit**

```bash
git add infra/gmail_oauth_bootstrap.py infra/README.md
git commit -m "infra: add Gmail OAuth bootstrap helper for refresh-token generation"
```

### Task 12.10: Production-readiness checklist

**Files:**
- Create: `docs/runbook.md`

A one-page checklist the operator runs through before flipping Cloud Scheduler on.

- [ ] **Step 1: Author the runbook**

Create `docs/runbook.md`:

```markdown
# Knockknock Runbook

## Pre-flight checklist (before resuming Cloud Scheduler)

- [ ] Neon Postgres reachable from local: `uv run alembic upgrade head` succeeds against `DATABASE_URL_DIRECT`.
- [ ] `infra/bootstrap.sh` has been run once; the seven `GCP_*` variables are set in GitHub Actions repo variables.
- [ ] `infra/secrets.sh` has been run with all `*_VAL` env vars set; `gcloud secrets list` shows 12 secrets.
- [ ] Gmail OAuth refresh token tested locally: `uv run knockknock pipeline run --once --skip-after draft` produces a `gmail.drafts.create` call.
- [ ] First `Deploy` workflow run on `main` is green.
- [ ] `infra/register_telegram_webhook.sh` ran; `getWebhookInfo` shows `url` matches `knockknock-telegram` Service URL.
- [ ] `gcloud scheduler jobs run knockknock-pipeline-hourly` triggers a manual run; the run completes and `pipeline_runs.status='COMPLETED'`.
- [ ] First Telegram card received in admin chat; tap Reject → job moves to USER_REJECTED.
- [ ] `gcloud scheduler jobs resume knockknock-pipeline-hourly`.

## Daily ops

- Morning: read the Telegram digest sent at 09:00 IST (Phase 11.9, scheduled via a second Cloud Scheduler trigger calling `knockknock digest`).
- Approve drafts inline.
- Spot-check: `uv run knockknock status` from your laptop (uses Neon direct URL).

## Common interventions

| Symptom                                    | Command                                                         |
|--------------------------------------------|-----------------------------------------------------------------|
| One scraper auto-disabled                   | `knockknock errors`; fix root cause; toggle `scraper_states.is_disabled=false` |
| Gemini RPD ceiling hit                      | Wait until next IST midnight (limiter resets); no action needed |
| Job stuck in ERROR                          | `knockknock show <id>`; then `knockknock retry <id> --to SCORED`|
| Apollo/Hunter quota exhausted               | Phonebook will pattern-guess; check `knockknock errors`         |
| Wrong email sent to recipient               | Reply from Gmail directly; mark job `--to SENT` not relevant    |
| Bot doesn't respond to button tap           | Check Cloud Run logs for `telegram.webhook duration_ms`; verify `TELEGRAM_WEBHOOK_SECRET` env var matches Secret Manager |
| Refresh token expired                       | Re-run `infra/gmail_oauth_bootstrap.py`; rotate secret           |

## Rollback

The `Deploy` workflow tags every image as both `:<sha>` and `:latest`. Roll back via:

```bash
gcloud run jobs update knockknock-pipeline --region=$REGION \
  --image=$REGION-docker.pkg.dev/$PROJECT_ID/$AR_REPO/pipeline:<prev-sha>
gcloud run services update knockknock-telegram --region=$REGION \
  --image=$REGION-docker.pkg.dev/$PROJECT_ID/$AR_REPO/telegram:<prev-sha>
```

## Kill switch

```bash
gcloud scheduler jobs pause knockknock-pipeline-hourly --location=$REGION
gcloud run services update knockknock-telegram --region=$REGION --min-instances=0
```
```

- [ ] **Step 2: Commit**

```bash
git add docs/runbook.md
git commit -m "docs: add production runbook (pre-flight checklist, ops, rollback, kill switch)"
```

### Task 12.11: Daily digest scheduled trigger

**Files:**
- Modify: `infra/cloud_scheduler.sh`

Add a second scheduler entry that calls a tiny Cloud Run **Service** endpoint (or reuses the pipeline Job with an env override) at 09:00 IST daily.

Simplest path: add a second Cloud Run Job that invokes `knockknock digest --hours 24`.

- [ ] **Step 1: Add a digest job to the deploy workflow**

In `.github/workflows/deploy.yml`, append after the pipeline-job deploy step:

```yaml
      - name: Deploy Cloud Run Job (digest)
        run: |
          gcloud run jobs deploy knockknock-digest \
            --image=${{ steps.tags.outputs.pipeline_image }} \
            --region=${{ vars.GCP_REGION }} \
            --service-account=${{ vars.GCP_PIPELINE_SA }} \
            --task-timeout=5m \
            --memory=512Mi \
            --cpu=1 \
            --set-env-vars=GOOGLE_CLOUD_PROJECT=${{ vars.GCP_PROJECT_ID }},LOG_FORMAT=json \
            --update-secrets=DATABASE_URL=DATABASE_URL:latest,TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest,TELEGRAM_ADMIN_CHAT_ID=TELEGRAM_ADMIN_CHAT_ID:latest,TELEGRAM_CALLBACK_SECRET=TELEGRAM_CALLBACK_SECRET:latest \
            --command="/bin/bash" \
            --args="-c,uv run knockknock digest --hours 24"
```

- [ ] **Step 2: Extend `cloud_scheduler.sh`**

Append to `infra/cloud_scheduler.sh`:

```bash
DIGEST_JOB="knockknock-digest-daily"
DIGEST_URL="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/knockknock-digest:run"

DIGEST_ARGS=(
  --project="$PROJECT_ID"
  --location="$REGION"
  --schedule="0 9 * * *"
  --time-zone="Asia/Kolkata"
  --uri="$DIGEST_URL"
  --http-method=POST
  --oauth-service-account-email="$SCHEDULER_SA"
  --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
  --attempt-deadline=120s
)

if gcloud scheduler jobs describe "$DIGEST_JOB" --project="$PROJECT_ID" --location="$REGION" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$DIGEST_JOB" "${DIGEST_ARGS[@]}"
else
  gcloud scheduler jobs create http "$DIGEST_JOB" "${DIGEST_ARGS[@]}"
fi
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/deploy.yml infra/cloud_scheduler.sh
git commit -m "infra: add daily digest Cloud Run Job + 09:00 IST scheduler trigger"
```

### Task 12.12: First end-to-end live run

**Files:** (none — verification only)

- [ ] **Step 1: Manual trigger**

```bash
gcloud scheduler jobs run knockknock-pipeline-hourly \
  --location="$REGION" --project="$PROJECT_ID"
```

- [ ] **Step 2: Inspect logs**

```bash
gcloud run jobs executions list --job=knockknock-pipeline \
  --region="$REGION" --project="$PROJECT_ID" --limit=1
gcloud beta run jobs executions describe <EXEC_ID> \
  --region="$REGION" --project="$PROJECT_ID"
```

Expected: exit code 0; structured logs include `stage.completed` for all seven stages.

- [ ] **Step 3: Verify Telegram round-trip**

- A draft card should arrive in your admin chat within a few minutes (assuming any job scored ≥ threshold).
- Tap **Reject**. Within a second, the card text should update to "Rejected".
- `uv run knockknock show <job_id>` from your laptop confirms `status=USER_REJECTED`.

- [ ] **Step 4: Resume the scheduler**

```bash
gcloud scheduler jobs resume knockknock-pipeline-hourly \
  --location="$REGION" --project="$PROJECT_ID"
```

System is live. Knockknock is now hunting on its own.

---

### Phase 12 Wrap-up

After this phase:
- Two Cloud Run deployables: hourly pipeline Job + always-on telegram-bot Service.
- Cloud Scheduler runs the pipeline at `0 * * * *` IST and the digest at `0 9 * * *` IST.
- All 12 secrets live in Google Secret Manager; nothing is in env files or Git.
- GitHub Actions deploys on `main` via Workload Identity Federation — zero long-lived keys.
- Operator runbook covers pre-flight, daily ops, rollback, and kill switch.

**You're done with v1.** The pipeline runs autonomously. Your only manual step is tapping a button per opportunity that survives all six filters.

### Optional next moves

- Add `--metadata-from-file` to set a `cloud.googleapis.com/location` hint for log routing into a single BQ sink.
- Wire Cloud Monitoring alerts on `pipeline_runs.status='FAILED'` count > 0 in a 6h window.
- Add a second region (e.g. `asia-southeast1` Singapore) and a second Neon endpoint with replication for DR.

← [Index](00-index.md) · [Prev: phase-11-cli-digest.md](phase-11-cli-digest.md)
