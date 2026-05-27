# Knockknock — Design Specification

**Date:** 2026-05-28
**Codename:** Knockknock
**Author:** Nishant Gupta
**Repository:** https://github.com/ngofficial99/Knockknock
**Status:** Draft — pending implementation plan

---

## 1. Problem

Find SWE/SDE roles at Bangalore-based startups, prioritize the top 1% of postings, and reach the founder/hiring manager directly with a personalized, human-quality email and a tailored resume — automatically, hourly, while the user sleeps.

The user is currently at Zeotap and wants to leave before the company deteriorates further. Outreach quality matters more than volume: the goal is to be in the top 1% of applicants by sending early, personal, well-targeted messages — not to spray-and-pray.

## 2. Goals

- Discover new startup SWE/SDE roles in Bangalore within ~1 hour of posting
- Filter aggressively so only high-fit roles consume expensive Gemini Pro budget
- Look up founder + careers email reliably; cache results to avoid repeat costs
- Generate personalized emails per JD, attach the best-matching pre-built resume
- Always require human approval (via Telegram) before any email is sent
- Run autonomously on Google Cloud at ~$0–5/month
- Be debuggable when something breaks at 3 AM
- Have clean module boundaries so v2 (multi-tenant SaaS) is a refactor, not a rewrite

## 3. Non-Goals (v1)

- Multi-user / multi-tenant support
- Web UI (CLI + Telegram only)
- Reply tracking from Gmail inbox
- Automated resume *generation* (we maintain a fixed set of variants)
- Fully autonomous send (always requires Telegram approval in v1)
- Interview scheduling, ATS round tracking
- Mobile app
- PagerDuty / Sentry / external monitoring (Cloud Logging + Telegram alerts only)

## 4. High-Level Architecture

```
Cloud Scheduler (hourly cron)
        │
        ▼
Cloud Run Job "pipeline-runner" (scales to zero)
        │
        ├─ Scraper modules (Wellfound, YC WaaS, HN, Greenhouse, Lever, Ashby)
        ├─ Neon Postgres (state + audit + cache)
        ├─ Google Gemini API
        │     ├─ 2.5 Flash → scoring
        │     └─ 2.5 Pro   → email drafting
        ├─ Apollo / Hunter APIs (founder email lookup, cached in phonebook)
        ├─ Gmail API (drafts.create)
        └─ Telegram Bot API (outbound notification)
                │
                ▼
Cloud Run Service "telegram-bot" (always-on webhook)
        │
        └─ on approval → Gmail API (drafts.send)
```

**Two deployables, one repo.** Shared `db/`, `clients/`, `common/`. Only shared state is Neon Postgres.

### Core design rules

1. **DB-as-queue.** Every job has a `status` column; each stage processes rows in its input status. No message broker.
2. **Sequential processing within a run.** No parallelism v1. ~30–50 jobs/hour × ~5s each fits well inside Cloud Run Job's timeout.
3. **Idempotency.** UNIQUE constraints on `(source, source_job_id)` and `companies.domain`. Re-scraping is safe.
4. **First-mover prioritization.** Within a run, jobs are drafted in order of score DESC so the best jobs claim the limited Pro quota first.
5. **Approval gate.** Email send only triggers from a Telegram callback. No autonomous send in v1.

## 5. Data Flow (Happy Path)

```
DISCOVERED → PRE_FILTERED → SCORED → ENRICHED → TAILORED → DRAFTED → AWAITING_APPROVAL → APPROVED → SENT
              │                  │             │            │            │                      │
              ↓                  ↓             ↓            ↓            ↓                      ↓
       PRE_FILTER_         SCORE_       ENRICH_       (resume     (no Pro budget        USER_REJECTED
       REJECTED            REJECTED     FAILED        selected)   left → wait next day)

Any stage error: → ERROR (retry up to 3x from current stage)
```

### Stage responsibilities

| Stage | Input status | Output status | Side effects |
|---|---|---|---|
| Discover | (n/a) | DISCOVERED | Insert companies + job_applications |
| Pre-filter | DISCOVERED | PRE_FILTERED / PRE_FILTER_REJECTED | Apply YAML-driven rules |
| Score | PRE_FILTERED | SCORED / SCORE_REJECTED | Gemini Flash call, log it |
| Enrich | SCORED | ENRICHED / ENRICH_FAILED | Phonebook lookup (cache-first) |
| Tailor | ENRICHED | TAILORED | Pick best resume variant (no LLM) |
| Draft | TAILORED | DRAFTED → AWAITING_APPROVAL | Gemini Pro call, Gmail draft, Telegram notify |
| (Telegram) | AWAITING_APPROVAL | APPROVED → SENT / USER_REJECTED | Gmail send on approve |

## 6. Module Breakdown

Top-level Python packages under `src/knockknock/`:

- **`config`** — Pydantic settings; loads env + Secret Manager + `job_preferences.yaml`
- **`db`** — SQLAlchemy engine, SQLModel models, repository helpers
- **`clients/`** — dumb wrappers per external API (Gemini, Apollo, Hunter, Gmail, Telegram, Secret Manager). No business logic.
- **`scrapers/`** — one file per source; implement `Scraper` Protocol; return `ScrapedJob` Pydantic
- **`phonebook/`** — cache-first lookup chain (Apollo → Hunter → pattern guess → `careers@<domain>`)
- **`resume/`** — manifest loader + tag-match variant selector
- **`email_gen/`** — prompt rendering + Gemini Pro call + strict output validation
- **`rate_limit/`** — Gemini RPM/TPM/RPD enforcement via `gemini_call_logs` table
- **`pipeline/`** — runner orchestrator + one file per stage
- **`telegram_bot/`** — FastAPI webhook receiver, handlers for approve / reject / regenerate / commands / digest
- **`cli/`** — Typer CLI for local debugging (`status`, `show`, `errors`, `retry`)
- **`common/`** — enums, logging, time helpers

### Isolation rules

- Stages communicate only through the database.
- Clients take primitives, return primitives. No ORM objects cross client boundaries.
- The pipeline runner and the telegram bot are independently deployable.
- Models never import clients. No "fat models."

## 7. Database Schema

**Engine:** Neon Postgres 17, AWS ap-south-1 (Singapore region — closest to Bangalore).
**Migrations:** Alembic. One migration per schema change.
**Connection:** `psycopg` (sync) for the pipeline, `asyncpg` for the telegram bot.

### Tables (10 total)

```
companies ──┬── companies_blacklist
            ├── phonebook (1:1)
            └── job_applications (1:N)
                  ├── job_application_events (1:N audit log)
                  ├── email_drafts (1:N, one active at a time)
                  │     └── telegram_messages
                  ├── gemini_call_logs
                  └── telegram_messages

pipeline_runs        (standalone)
scraper_states       (PK by source)
```

All table names plural. Timestamps `TIMESTAMPTZ` in UTC. Created/updated timestamps on every table with triggers.

#### `companies`

```sql
CREATE TABLE companies (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    domain          TEXT,
    careers_url     TEXT,
    size_bucket     company_size_bucket,           -- SEED, EARLY, GROWTH, LATE, UNKNOWN
    hq_location     TEXT,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT companies_domain_unique UNIQUE (domain)
);
CREATE INDEX companies_name_lower_idx ON companies (LOWER(name));
```

#### `companies_blacklist`

```sql
CREATE TABLE companies_blacklist (
    id          BIGSERIAL PRIMARY KEY,
    pattern     TEXT NOT NULL,                     -- ILIKE pattern
    reason      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT companies_blacklist_pattern_unique UNIQUE (pattern)
);
```

Seeded at startup from `job_preferences.yaml`, then extendable at runtime via Telegram `/blacklist` command.

#### `phonebook`

```sql
CREATE TABLE phonebook (
    id              BIGSERIAL PRIMARY KEY,
    company_id      BIGINT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    founder_name    TEXT,
    founder_email   TEXT,
    careers_email   TEXT NOT NULL,
    source          phonebook_source NOT NULL,     -- APOLLO, HUNTER, PATTERN_GUESS, MANUAL, SEED
    verified_at     TIMESTAMPTZ,
    last_lookup_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT phonebook_company_unique UNIQUE (company_id)
);
```

One row per company. `careers_email` always populated (falls back to `careers@<domain>`).

#### `job_applications`

```sql
CREATE TABLE job_applications (
    id                  BIGSERIAL PRIMARY KEY,
    company_id          BIGINT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    source              job_source NOT NULL,        -- WELLFOUND, YC_WAAS, HN, GREENHOUSE, LEVER, ASHBY
    source_job_id       TEXT NOT NULL,
    title               TEXT NOT NULL,
    location            TEXT NOT NULL,
    apply_url           TEXT NOT NULL,
    description         TEXT NOT NULL,              -- cleaned plaintext
    posted_at           TIMESTAMPTZ,
    discovered_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    status              job_status NOT NULL DEFAULT 'DISCOVERED',
    score               SMALLINT,                   -- 1..10, null until SCORED
    rejection_reason    rejection_reason,
    rejection_detail    TEXT,

    resume_variant_key  TEXT,                       -- soft ref to resumes/manifest.yaml
    last_error          TEXT,
    retry_count         SMALLINT NOT NULL DEFAULT 0,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT jobs_source_unique UNIQUE (source, source_job_id)
);
CREATE INDEX jobs_status_idx ON job_applications (status);
CREATE INDEX jobs_company_idx ON job_applications (company_id);
CREATE INDEX jobs_score_idx ON job_applications (score) WHERE score IS NOT NULL;
CREATE INDEX jobs_discovered_idx ON job_applications (discovered_at DESC);
```

#### `job_application_events`

```sql
CREATE TABLE job_application_events (
    id                  BIGSERIAL PRIMARY KEY,
    job_application_id  BIGINT NOT NULL REFERENCES job_applications(id) ON DELETE CASCADE,
    from_status         job_status,
    to_status           job_status NOT NULL,
    stage               pipeline_stage NOT NULL,
    note                TEXT,
    pipeline_run_id     BIGINT REFERENCES pipeline_runs(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX events_job_idx ON job_application_events (job_application_id, created_at);
```

#### `email_drafts`

```sql
CREATE TABLE email_drafts (
    id                      BIGSERIAL PRIMARY KEY,
    job_application_id      BIGINT NOT NULL REFERENCES job_applications(id) ON DELETE CASCADE,
    phonebook_id            BIGINT NOT NULL REFERENCES phonebook(id) ON DELETE RESTRICT,
    to_email                TEXT NOT NULL,
    cc_email                TEXT,
    subject                 TEXT NOT NULL,
    body_text               TEXT NOT NULL,
    body_html               TEXT NOT NULL,
    resume_variant_key      TEXT NOT NULL,
    gmail_draft_id          TEXT,
    gmail_message_id        TEXT,
    state                   draft_state NOT NULL DEFAULT 'GENERATED',   -- GENERATED, DRAFT_CREATED, SENT, FAILED, SUPERSEDED
    sent_at                 TIMESTAMPTZ,
    failure_reason          TEXT,
    is_regeneration_of      BIGINT REFERENCES email_drafts(id) ON DELETE SET NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX drafts_job_idx ON email_drafts (job_application_id);
CREATE INDEX drafts_state_idx ON email_drafts (state);
CREATE UNIQUE INDEX drafts_one_active_per_job
    ON email_drafts (job_application_id)
    WHERE state IN ('GENERATED', 'DRAFT_CREATED');
```

Partial unique index enforces at most one active (non-SUPERSEDED, non-SENT, non-FAILED) draft per job.

#### `gemini_call_logs`

```sql
CREATE TABLE gemini_call_logs (
    id                      BIGSERIAL PRIMARY KEY,
    job_application_id      BIGINT REFERENCES job_applications(id) ON DELETE SET NULL,
    model                   gemini_model NOT NULL,         -- FLASH_2_5, PRO_2_5
    purpose                 gemini_purpose NOT NULL,       -- SCORE, DRAFT_EMAIL, REGENERATE
    tokens_input            INTEGER NOT NULL,
    tokens_output           INTEGER NOT NULL,
    latency_ms              INTEGER NOT NULL,
    success                 BOOLEAN NOT NULL,
    error_class             TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX gemini_logs_recent_idx ON gemini_call_logs (created_at DESC);
CREATE INDEX gemini_logs_model_recent_idx ON gemini_call_logs (model, created_at DESC);
```

Prompts/responses NOT stored — reconstructable from job + prompt template version.

#### `telegram_messages`

```sql
CREATE TABLE telegram_messages (
    id                      BIGSERIAL PRIMARY KEY,
    job_application_id      BIGINT REFERENCES job_applications(id) ON DELETE SET NULL,
    email_draft_id          BIGINT REFERENCES email_drafts(id) ON DELETE SET NULL,
    direction               telegram_direction NOT NULL,   -- OUTGOING, INCOMING
    kind                    telegram_kind NOT NULL,        -- DRAFT_PREVIEW, APPROVAL, REJECTION, COMMAND, DIGEST, ALERT
    telegram_message_id     BIGINT,
    payload_summary         TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX tg_msg_job_idx ON telegram_messages (job_application_id);
CREATE INDEX tg_msg_draft_idx ON telegram_messages (email_draft_id);
```

#### `pipeline_runs`

```sql
CREATE TABLE pipeline_runs (
    id                      BIGSERIAL PRIMARY KEY,
    started_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at            TIMESTAMPTZ,
    status                  pipeline_run_status NOT NULL DEFAULT 'RUNNING',   -- RUNNING, SUCCEEDED, FAILED, PARTIAL
    jobs_discovered         INTEGER NOT NULL DEFAULT 0,
    jobs_pre_filtered_in    INTEGER NOT NULL DEFAULT 0,
    jobs_pre_filtered_out   INTEGER NOT NULL DEFAULT 0,
    jobs_scored             INTEGER NOT NULL DEFAULT 0,
    jobs_passed_threshold   INTEGER NOT NULL DEFAULT 0,
    jobs_drafted            INTEGER NOT NULL DEFAULT 0,
    errors_count            INTEGER NOT NULL DEFAULT 0,
    last_error              TEXT,
    gemini_calls_made       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX runs_started_idx ON pipeline_runs (started_at DESC);
```

#### `scraper_states`

```sql
CREATE TABLE scraper_states (
    source                  job_source PRIMARY KEY,
    last_run_at             TIMESTAMPTZ NOT NULL,
    last_success_at         TIMESTAMPTZ,
    last_error              TEXT,
    consecutive_failures    SMALLINT NOT NULL DEFAULT 0,
    high_water_mark         TEXT
);
```

### Postgres enums

```sql
CREATE TYPE job_source           AS ENUM ('WELLFOUND','YC_WAAS','HN','GREENHOUSE','LEVER','ASHBY');
CREATE TYPE company_size_bucket  AS ENUM ('SEED','EARLY','GROWTH','LATE','UNKNOWN');
CREATE TYPE phonebook_source     AS ENUM ('APOLLO','HUNTER','PATTERN_GUESS','MANUAL','SEED');
CREATE TYPE job_status           AS ENUM (
    'DISCOVERED','PRE_FILTERED','PRE_FILTER_REJECTED',
    'SCORED','SCORE_REJECTED',
    'ENRICHED','ENRICH_FAILED',
    'TAILORED',
    'DRAFTED','AWAITING_APPROVAL',
    'APPROVED','SENT',
    'USER_REJECTED','ERROR'
);
CREATE TYPE rejection_reason     AS ENUM (
    'BLACKLISTED','LOCATION_MISMATCH','ROLE_MISMATCH','SENIORITY_MISMATCH',
    'SCORE_LOW','NO_EMAIL_FOUND','USER_REJECTED','MAX_RETRIES'
);
CREATE TYPE pipeline_stage       AS ENUM (
    'DISCOVER','PRE_FILTER','SCORE','ENRICH','TAILOR','DRAFT','TELEGRAM_RESPONSE','SEND'
);
CREATE TYPE pipeline_run_status  AS ENUM ('RUNNING','SUCCEEDED','FAILED','PARTIAL');
CREATE TYPE draft_state          AS ENUM ('GENERATED','DRAFT_CREATED','SENT','FAILED','SUPERSEDED');
CREATE TYPE gemini_model         AS ENUM ('FLASH_2_5','PRO_2_5');
CREATE TYPE gemini_purpose       AS ENUM ('SCORE','DRAFT_EMAIL','REGENERATE');
CREATE TYPE telegram_direction   AS ENUM ('OUTGOING','INCOMING');
CREATE TYPE telegram_kind        AS ENUM ('DRAFT_PREVIEW','APPROVAL','REJECTION','COMMAND','DIGEST','ALERT');
```

## 8. Configuration

### `config/job_preferences.yaml`

User-editable, version-controlled. Drives source query params, filter rules, scoring weights, daily caps. Loaded at startup into a Pydantic `JobPreferences` model; invalid YAML = boot failure.

Starter template (to be tuned by user):

```yaml
profile:
  name: "Nishant Gupta"
  current_employer: "Zeotap"
  years_of_experience: 4
  current_location: "Bangalore"

target_roles:
  titles_include: ["software engineer","swe","sde","backend","fullstack","full-stack","platform engineer","senior software"]
  titles_exclude: ["intern","principal","staff","manager","director","qa","frontend only"]
  experience_years: { min: 2, max: 7 }

target_locations:
  patterns: ["%bangalore%","%bengaluru%","%blr%","%remote%india%","%remote-india%","%hybrid%bangalore%"]
  exclude:  ["%onsite%us%","%onsite%europe%"]

company_preferences:
  size_buckets_allowed: [SEED, EARLY, GROWTH]
  funding_stage_exclude: ["ipo","public"]
  blacklist:
    - { pattern: "zeotap", reason: "current employer" }
    - { pattern: "%scam%", reason: "low repute" }

skills:
  primary:   [python, go, postgres, kafka, kubernetes, aws, microservices, "distributed systems"]
  secondary: [fastapi, django, redis, docker, terraform, grpc, graphql]
  exposure:  [llm, langchain, rag, ml]
  avoid:     [".net", "c#", salesforce, sap, wordpress, php]

scoring:
  threshold: 7
  weights: { skill_primary: 3, skill_secondary: 2, skill_exposure: 1, skill_avoid_penalty: -2, seniority_match: 2, company_stage_match: 1 }

sources:
  wellfound:        { enabled: true,  query_params: { location: Bangalore, role_types: [engineering], remote: true } }
  yc_waas:          { enabled: true,  query_params: { location: India, role: engineer } }
  hn_who_is_hiring: { enabled: true,  keyword_pre_filter: [bangalore, bengaluru, remote-india] }
  ats_greenhouse:   { enabled: true,  company_seed_list_path: "config/seed_companies_greenhouse.yaml" }
  ats_lever:        { enabled: true,  company_seed_list_path: "config/seed_companies_lever.yaml" }
  ats_ashby:        { enabled: true,  company_seed_list_path: "config/seed_companies_ashby.yaml" }

limits:
  daily_pro_draft_cap: 40
  daily_rpd_safety_cap: 500
```

### `resumes/manifest.yaml`

Defines tag dictionary and per-variant tags + priority. Drives the resume selector (no LLM).

### `config/seed_companies_*.yaml`

Curated lists of ATS board URLs to crawl (one file per ATS provider).

## 9. External APIs

| Provider | Purpose | Tier | Limits |
|---|---|---|---|
| Gemini 2.5 Flash | Job scoring | Free | 1500 RPD, 15 RPM, 1M TPM |
| Gemini 2.5 Pro | Email drafting + regeneration | Free | 50 RPD, 2 RPM, 32K TPM |
| Apollo.io | Founder email lookup | Free | ~120 email credits/month |
| Hunter.io | Fallback email lookup | Free | 25 lookups/month |
| Gmail API | Draft + send | Free | 1B quota units/day (effectively unlimited) |
| Telegram Bot API | Notifications + callbacks | Free | 30 msg/sec |

Phonebook cache should keep Apollo+Hunter usage well within free tiers over time.

## 10. Rate Limiting

Single `gemini_limiter` module wraps every Gemini call. Tracks RPM/TPM/RPD against `gemini_call_logs`.

- **Pro draft soft cap:** 40/day (10-call buffer for regenerations within the 50 RPD ceiling)
- **Hard safety ceiling:** 500 RPD total across all models. If hit, all Gemini calls halt and a Telegram alert fires.
- **RPM enforcement:** sleep with jitter if next call would exceed window.
- **RPD enforcement:** raise `RpdExhausted`; affected jobs stay at their current status, picked up next day.

## 11. Approval Flow (Telegram)

```
1. pipeline draft stage creates Gmail draft + Telegram card
2. card shows: company, role, score, resume variant, To/Cc, 400-char preview
3. inline buttons: [✅ Approve] [↻ Regenerate] [❌ Reject] [📄 Full email]
4. user taps button on phone
5. Telegram → webhook → telegram-bot Cloud Run service
6. handler verifies webhook secret + admin chat_id + HMAC-signed callback_data
7. on approve: Gmail drafts.send → state SENT
   on reject: state SUPERSEDED, job USER_REJECTED
   on regenerate: inline-trigger new draft via Pro (counts against budget), supersede old
8. bot replies with confirmation
```

Editing happens in Gmail mobile app, not in Telegram. Approve/Reject/Regenerate only.

## 12. Error Handling

### Retry policies

| Failure point | Where retried | Max attempts | Backoff | Terminal action |
|---|---|---|---|---|
| Scraper HTTP | scraper | 3 | exp 2–30s | scraper auto-disabled after 3 consecutive runs fail |
| Gemini transient | client | 3 | exp 2–30s | job → ERROR, retry next run |
| Gemini validation fail | email_gen | 1 rewrite | none | job → ERROR, surfaced in digest |
| Apollo/Hunter HTTP | client | 2 | 2s, 5s | fall through to next lookup option |
| Gmail draft create | stage | 3 | exp 2–30s | email_drafts → FAILED, retried next run |
| Gmail send (on approve) | telegram handler | 3 | exp 2–30s | Telegram reply "send failed" |
| DB connection | engine | 5 | exp 1–10s | run FAILED, Telegram alert |
| Unhandled exception | runner | 0 | n/a | run PARTIAL, Telegram alert |

After 3 retries at the same stage, job goes to terminal ERROR with `rejection_reason = MAX_RETRIES`.

### Observability layers

1. **Structured JSON logs** → Cloud Logging (queryable, transient)
2. **Postgres audit tables** → trend analysis, joins, long-lived
3. **Telegram alerts** → critical events only (pipeline failure, scraper disabled, RPD ceiling hit, Gemini auth)
4. **Daily digest at 9 AM IST** → counts per stage, errors, anomalies

No external monitoring SaaS in v1.

## 13. Security

### Threat model

Single-user system. Realistic threats: secret leakage in git/logs, Telegram webhook spoofing, Gemini key drain, Gmail OAuth abuse, prompt injection from job descriptions.

### Secrets

All secrets in Google Secret Manager. None in code, env vars (visible in `gcloud` output), or env files. Two service accounts with minimum-privilege Secret Manager access scoped to specific secrets.

Secrets stored and which service account can access each:

| Secret | `knockknock-pipeline` SA | `knockknock-telegram` SA |
|---|---|---|
| `gemini-api-key` | ✓ | ✓ (for regenerate) |
| `apollo-api-key` | ✓ | ✗ |
| `hunter-api-key` | ✓ | ✗ |
| `telegram-bot-token` | ✓ (send notifications) | ✓ (receive callbacks) |
| `telegram-webhook-secret` | ✗ | ✓ |
| `telegram-admin-chat-id` | ✓ | ✓ |
| `gmail-oauth-client-id` | ✓ | ✓ |
| `gmail-oauth-client-secret` | ✓ | ✓ |
| `gmail-oauth-refresh-token` | ✓ | ✓ |
| `neon-database-url` | ✓ | ✓ |

### Telegram webhook

Layered defense:
1. Secret token in `X-Telegram-Bot-Api-Secret-Token` header verified on every call
2. Only one allowed `chat_id` for commands and callbacks
3. HMAC-signed `callback_data` for approve/reject/regenerate
4. Idempotency via `email_drafts.state` check
5. HTTPS-only (Cloud Run enforces)

### Gmail OAuth scopes

- `gmail.compose` (create drafts)
- `gmail.send` (send)
- Explicitly NOT requesting `gmail.readonly` in v1

### SQL injection

All queries parameterized through SQLAlchemy. No `text(f"...{x}")`. Ruff rule `B608` enforces.

### Prompt injection defenses

1. Job description wrapped in `<jd>` tags and labeled as untrusted data
2. JD truncated to 1500 chars
3. Strict email validator rejects placeholders, "As an AI" patterns, URLs other than the apply_url, email addresses other than the user's
4. Gemini has no tool-use access — pure text generation
5. Telegram approval is the final gate before send

### Dependencies

`uv.lock` committed. Only top-1000 PyPI packages, all 2+ years old. Dependabot enabled. `pip-audit` in CI.

### Container

`gcr.io/distroless/python3-debian12` base. Non-root user. Multi-stage build. `.dockerignore` excludes tests/docs/.git/secrets.

### Logging

Structlog processor redacts emails to hash-prefixed form, never logs prompts at INFO level, scans every log event for secret patterns (`sk-`, `AIza`, JWT shapes) and replaces with `[REDACTED]`.

## 14. Deployment

### Infrastructure

- **Neon Postgres free tier** (Singapore region, AWS ap-south-1)
- **Cloud Scheduler** — single hourly cron `0 * * * *` (IST timezone)
- **Cloud Run Job** — `knockknock-pipeline`, scales to zero, triggered by Scheduler
- **Cloud Run Service** — `knockknock-telegram`, always-reachable webhook, scales to zero between requests
- **Artifact Registry** — `asia-south1-docker.pkg.dev/<project>/images/`
- **Secret Manager** — 10 secrets, scoped to two service accounts

### CI/CD (GitHub Actions, direct deploy — no GitOps manifest in v1)

**On PR:**
- ruff check + format
- mypy strict
- pytest unit
- create Neon branch DB
- alembic upgrade head on branch
- pytest integration + scrapers + e2e against branch
- delete Neon branch (always)

**On push to main:**
- Same checks as CI
- Authenticate to GCP via Workload Identity Federation (no JSON keys in GitHub)
- Build + push two images tagged `:${GITHUB_SHA}`
- `alembic upgrade head` against Neon main branch
- `gcloud run jobs update knockknock-pipeline --image=...:${GITHUB_SHA}`
- `gcloud run deploy knockknock-telegram --image=...:${GITHUB_SHA}`
- Smoke test: `curl https://telegram-service/healthz`
- Rollback job available via manual workflow trigger

### Estimated monthly cost (v1)

- Neon free tier: $0
- Cloud Run Jobs (24 runs/day × ~5 min × tiny): ~$0
- Cloud Run Service (telegram, scale to zero): ~$0
- Cloud Scheduler (3 free jobs): $0
- Cloud Logging (within free tier): $0
- Apollo / Hunter / Gemini / Gmail / Telegram free tiers: $0
- **Total: under $5/month**

## 15. Testing Strategy

Pragmatic, risk-weighted. The user is not test-driven by default; the strategy keeps tests focused on the dangerous paths.

### Mandatory tests (must exist before going live)

- `email_gen/validator.py` — placeholder / AI-mention / length checks
- `rate_limit/gemini_limiter.py` — RPM/TPM/RPD enforcement, freezegun-based
- `pre_filter` — blacklist + location + role rules
- E2E happy path — full pipeline against Neon branch DB with all external APIs mocked

### Layers

| Layer | Scope | Tools | Speed |
|---|---|---|---|
| Unit | Pure functions | pytest, freezegun, factory-boy | ms |
| Integration | Stages against real Postgres | pytest, Neon branch, respx | seconds |
| Scraper fixtures | Parse recorded HTML | pytest | ms |
| Scraper live | Real sites | pytest -m live (manual) | seconds |
| E2E | Full pipeline | pytest, mocked externals | ~1 minute |

External APIs mocked with `respx`. Recorded fixtures in `tests/fixtures/api_responses/`. Coverage target: ≥90% on critical modules, ≥75% overall (aspirational).

What we explicitly do NOT test: Gemini output quality (manual eval, approval gate is the safety net), real external API calls in CI, UI cosmetics, trivial getters.

## 16. Coding Standards

- Python 3.11+ with full type hints, `mypy --strict` in CI
- `ruff format` (Black-compatible), `ruff check` with `E, F, W, I, B, UP, S, SIM, TCH, RUF`
- Max 100 chars/line, 300 lines/file, 50 lines/function, 3 levels of nesting
- snake_case functions/vars, PascalCase classes, SCREAMING_SNAKE constants, plural snake_case table names
- Docstrings on public APIs only; comments only for non-obvious *why*; TODOs with name + date
- Custom exception hierarchy rooted at `KnockknockError`; no bare `except:`; never silently swallow
- Pydantic for boundaries, SQLModel for DB, TypedDict/dataclass for internals; no `Any`
- No `print`, no `time.sleep` in prod code, no f-string SQL, no scattered `os.environ`, no global mutable state

### Approved dependency list

Locked at design time. Notable choices: `psycopg` (not psycopg2), `google-genai` (not deprecated `google-generativeai`), `selectolax` (faster than BeautifulSoup), `uv` (not poetry/pip-tools), `ruff` (replaces black+isort+flake8).

Core: `pydantic ≥2.6`, `pydantic-settings ≥2.2`, `sqlmodel ≥0.0.16`, `sqlalchemy ≥2.0`, `alembic ≥1.13`, `psycopg ≥3.1`, `asyncpg ≥0.29`.
HTTP & retry: `httpx ≥0.27`, `tenacity ≥8.2`.
LLM & Google: `google-genai ≥0.5`, `google-api-python-client`, `google-auth-oauthlib`, `google-cloud-secret-manager`, `google-cloud-logging`.
Telegram & FastAPI: `python-telegram-bot ≥21`, `fastapi ≥0.110`, `uvicorn[standard] ≥0.27`.
Scraping: `playwright ≥1.42`, `selectolax ≥0.3`.
PDF & templating: `weasyprint ≥61`, `jinja2 ≥3.1`.
CLI & logging: `typer ≥0.12`, `structlog ≥24.1`, `rich ≥13.7`.
Testing: `pytest ≥8`, `pytest-asyncio ≥0.23`, `pytest-cov ≥4`, `respx ≥0.20`, `factory-boy ≥3.3`, `freezegun ≥1.4`.
Dev tools: `ruff ≥0.4`, `mypy ≥1.9`, `pre-commit ≥3.7`.

Excluded deliberately: Celery, Redis, Airflow, Django, Flask, requests, BeautifulSoup4, black, isort, flake8, mock.

### Branch & commit conventions

- `main` always deployable, direct push blocked
- Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, etc.)
- Squash-merge to main
- PR title = release note

## 17. Repo Layout (high-level)

```
knockknock/
├── .github/workflows/         # ci.yml, deploy.yml
├── pyproject.toml             # uv + tool configs
├── alembic/versions/          # migrations
├── config/
│   ├── job_preferences.yaml
│   ├── seed_companies_greenhouse.yaml
│   ├── seed_companies_lever.yaml
│   └── seed_companies_ashby.yaml
├── resumes/                   # PDFs + manifest.yaml
├── docker/                    # pipeline.Dockerfile, telegram.Dockerfile
├── deploy/                    # cloud-run-*.yaml, scheduler.yaml
├── scripts/                   # one-time setup scripts
├── docs/                      # architecture, runbook, prompts/
├── src/knockknock/            # see Section 6
└── tests/                     # unit, integration, scrapers, e2e
```

## 18. Future (v2) Considerations

These are explicitly out of scope for v1 but the design accommodates them:

- **Multi-tenant SaaS** — add `user_id` column via migration, scope every repository query, per-user secrets in Secret Manager
- **Auth + onboarding UI** (Next.js) — separate frontend service
- **Stripe billing** — separate billing module
- **Real async queue** (PubSub) — replaces DB-as-queue when concurrency demands it
- **Reply tracking** — requires `gmail.readonly` scope and an inbox-polling job
- **Auto-send tier** — flip approval gate via per-user config flag once quality is trusted
- **Cloud Deploy (GitOps)** — replaces direct CI/CD when multi-environment promotion is needed
- **PagerDuty / Sentry** — adds external monitoring beyond Cloud Logging
- **One-time seed scraper for Crunchbase / YC directory / Tracxn** — builds initial phonebook + ATS board lists

## 19. Open Questions

These are user-side deliverables, not design decisions. Tracked here so the implementation plan can sequence them:

- **ATS seed company lists** — user to curate `config/seed_companies_*.yaml` over a weekend (target: 30–50 Bangalore startups per ATS provider)
- **Resume variants** — user to assemble 5–10 PDFs and write `resumes/manifest.yaml` tags
- **Telegram card UX** — exact formatting and full-email expansion to be iterated during implementation; out of scope for the spec
- **HN scraper enablement** — enabled from day one; the scraper handles the monthly thread refresh internally by detecting the latest "Ask HN: Who is hiring?" thread URL via the Algolia HN search API

## 20. Approval

Pending user review before transitioning to implementation plan.
