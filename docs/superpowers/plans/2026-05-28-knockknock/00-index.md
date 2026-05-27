# Knockknock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an hourly cron pipeline that scrapes niche startup job boards, scores fit with Gemini, drafts personalized Gmail emails with founder + careers@ recipients, and routes through Telegram for one-tap manual approval.

**Architecture:** Two Cloud Run deployables (a pipeline-runner Job triggered hourly + an always-on telegram-bot Service), one Neon Postgres acting as both store and queue (status column drives stage transitions), config in `job_preferences.yaml`, secrets in Google Secret Manager.

**Tech Stack:** Python 3.11+, uv, Pydantic v2, SQLModel + SQLAlchemy 2.0, Alembic, psycopg + asyncpg, httpx + tenacity, google-genai, google-api-python-client (Gmail), google-cloud-secret-manager, python-telegram-bot v21+, FastAPI + uvicorn, Playwright + selectolax, WeasyPrint + Jinja2, Typer + structlog + rich, pytest + respx + factory-boy + freezegun, ruff + mypy --strict, GitHub Actions + Workload Identity Federation, Cloud Run + Cloud Scheduler + Artifact Registry.

**Spec reference:** `docs/superpowers/specs/2026-05-28-knockknock-design.md`
**Repository:** https://github.com/ngofficial99/Knockknock

---

## Phase Index

Execute phases in order. Each phase ends with something demonstrable; you can stop after any phase and still have a working partial system.

| Phase | File | Outcome |
|-------|------|---------|
| 0 | [`phase-00-bootstrap.md`](phase-00-bootstrap.md) | Repo, uv, ruff/mypy/pre-commit, CI green, Neon reachable |
| 1 | [`phase-01-db-schema.md`](phase-01-db-schema.md) | 10 tables + 12 enums migrated to Neon, SQLModel classes, roundtrip integration test |
| 2 | [`phase-02-config.md`](phase-02-config.md) | Settings, JobPreferences YAML loader, SecretsClient (env + GSM), structlog |
| 3 | [`phase-03-pipeline-hn.md`](phase-03-pipeline-hn.md) | Pipeline skeleton + Stage protocol + HN scraper + discover stage; first real jobs in DB |
| 4 | [`phase-04-prefilter.md`](phase-04-prefilter.md) | Hard-rule pre-filter + blacklist YAML sync |
| 5 | [`phase-05-gemini-score.md`](phase-05-gemini-score.md) | Gemini client + RPM/TPM/RPD rate limiter + score stage with Flash |
| 6 | [`phase-06-phonebook-enrich.md`](phase-06-phonebook-enrich.md) | Apollo → Hunter → pattern-guess chain + enrich stage |
| 7 | [`phase-07-resume-tailor.md`](phase-07-resume-tailor.md) | Resume selector + tailor stage + `resumes/manifest.yaml` |
| 8 | [`phase-08-email-draft.md`](phase-08-email-draft.md) | Email generator (Gemini Pro) + validator + Gmail client + draft stage |
| 9 | [`phase-09-telegram.md`](phase-09-telegram.md) | Telegram bot FastAPI service + approve/reject/regenerate handlers + HMAC signing |
| 10 | [`phase-10-scrapers.md`](phase-10-scrapers.md) | Wellfound, YC WaaS, Greenhouse, Lever, Ashby scrapers |
| 11 | [`phase-11-cli-digest.md`](phase-11-cli-digest.md) | CLI (status/show/errors/retry), daily digest, observability polish |
| 12 | [`phase-12-deploy.md`](phase-12-deploy.md) | Cloud Run Job + Service + Scheduler + Secret Manager + GH Actions WIF |

## File Structure Map

This is the final shape of the repo at end of Phase 12. Phases reference exact paths from this map.

```
knockknock/
├── pyproject.toml                       # uv project config, deps, ruff/mypy
├── uv.lock                              # locked deps
├── .python-version                      # 3.11
├── .gitignore
├── .pre-commit-config.yaml
├── .dockerignore
├── README.md
├── alembic.ini
├── Dockerfile.pipeline                  # pipeline-runner image
├── Dockerfile.telegram                  # telegram-bot image
│
├── config/
│   ├── job_preferences.yaml             # filter/score/source params
│   └── blacklist.yaml                   # static blacklist seed
│
├── resumes/
│   ├── manifest.yaml                    # tag → PDF mapping
│   ├── backend_python.pdf
│   ├── backend_java.pdf
│   ├── fullstack_node.pdf
│   ├── ai_heavy.pdf
│   └── cloud_heavy.pdf
│
├── migrations/
│   ├── env.py                           # alembic env
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial_schema.py
│
├── src/knockknock/
│   ├── __init__.py
│   ├── __main__.py                      # CLI entrypoint (Typer)
│   ├── exceptions.py                    # KnockknockError hierarchy
│   ├── logging.py                       # structlog config
│   │
│   ├── config/
│   │   ├── __init__.py
│   │   ├── settings.py                  # Pydantic Settings (env vars)
│   │   ├── preferences.py               # JobPreferences model + loader
│   │   └── secrets.py                   # Secret Manager wrapper
│   │
│   ├── db/
│   │   ├── __init__.py
│   │   ├── engine.py                    # sync + async engine factory
│   │   ├── session.py                   # session context manager
│   │   ├── enums.py                     # 12 Postgres enums as PyEnums
│   │   └── models.py                    # SQLModel tables
│   │
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── runner.py                    # orchestrates stages per hourly run
│   │   ├── stage.py                     # Stage protocol/base
│   │   ├── discover.py                  # stage 1
│   │   ├── pre_filter.py                # stage 2
│   │   ├── score.py                     # stage 3
│   │   ├── enrich.py                    # stage 4
│   │   ├── tailor.py                    # stage 5
│   │   ├── draft.py                     # stage 6
│   │   └── digest.py                    # daily summary
│   │
│   ├── scrapers/
│   │   ├── __init__.py
│   │   ├── base.py                      # Scraper protocol + ScrapedJob dataclass
│   │   ├── registry.py                  # source enum → scraper class
│   │   ├── hn.py                        # HN "Who is hiring" via Algolia
│   │   ├── wellfound.py                 # Playwright
│   │   ├── yc_waas.py                   # work-at-a-startup
│   │   ├── greenhouse.py                # ATS
│   │   ├── lever.py                     # ATS
│   │   └── ashby.py                     # ATS
│   │
│   ├── filter/
│   │   ├── __init__.py
│   │   ├── rules.py                     # hard-rule pre-filter
│   │   └── blacklist.py                 # YAML → DB sync + matcher
│   │
│   ├── gemini/
│   │   ├── __init__.py
│   │   ├── client.py                    # google-genai wrapper
│   │   ├── rate_limiter.py              # RPM/TPM/RPD token bucket
│   │   ├── scorer.py                    # Flash scoring prompt
│   │   └── drafter.py                   # Pro email-drafting prompt
│   │
│   ├── phonebook/
│   │   ├── __init__.py
│   │   ├── service.py                   # cache-first lookup
│   │   ├── apollo.py                    # Apollo.io client
│   │   ├── hunter.py                    # Hunter.io fallback
│   │   └── pattern_guess.py             # heuristic guesser
│   │
│   ├── resume/
│   │   ├── __init__.py
│   │   ├── selector.py                  # tag-match algorithm
│   │   └── manifest.py                  # manifest.yaml loader
│   │
│   ├── email/
│   │   ├── __init__.py
│   │   ├── generator.py                 # build prompt for Gemini
│   │   ├── validator.py                 # block bad output
│   │   ├── gmail_client.py              # Gmail API wrapper
│   │   └── recipients.py                # founder + careers@ assembler
│   │
│   ├── telegram_bot/
│   │   ├── __init__.py
│   │   ├── app.py                       # FastAPI app for webhook
│   │   ├── handlers.py                  # approve/reject/regenerate
│   │   ├── signer.py                    # HMAC for callback_data
│   │   └── notifier.py                  # send-message helper
│   │
│   ├── observability/
│   │   ├── __init__.py
│   │   ├── metrics.py                   # PipelineRun bookkeeping
│   │   └── alerts.py                    # Telegram alert sink
│   │
│   └── cli/
│       ├── __init__.py
│       ├── status.py
│       ├── show.py
│       ├── errors.py
│       └── retry.py
│
├── tests/
│   ├── conftest.py                      # shared fixtures
│   ├── factories.py                     # factory-boy
│   ├── test_config/
│   ├── test_db/
│   ├── test_pipeline/
│   ├── test_scrapers/
│   ├── test_filter/
│   ├── test_gemini/
│   ├── test_phonebook/
│   ├── test_resume/
│   ├── test_email/
│   ├── test_telegram/
│   └── fixtures/                        # cassettes, sample HTML, JSON
│
├── .github/
│   └── workflows/
│       ├── ci.yml                       # ruff, mypy, pytest
│       └── deploy.yml                   # build + deploy via WIF
│
└── infra/
    ├── README.md
    ├── bootstrap.sh                     # one-time GCP project bootstrap (APIs, SAs, WIF pool)
    ├── secrets.sh                       # Secret Manager seed/rotate
    ├── cloud_scheduler.sh               # hourly trigger + daily digest trigger
    ├── register_telegram_webhook.sh     # one-time Telegram webhook URL registration
    └── gmail_oauth_bootstrap.py         # one-time refresh-token generator
```

---

## Errata & cross-phase clarifications

> **Read this before starting Phase 1.** The phases were written sequentially and a few names drifted between earlier and later phases. The fixes below are small and belong in their respective phases; an executing agent should treat them as authoritative overrides.

### E.1 Module path drift (00-index "File Structure Map" vs phase content)

The "File Structure Map" above shows an early sketch. The actual paths used by Phases 5–12 are:

| Sketch (above)                          | Authoritative path used by phases                |
|-----------------------------------------|--------------------------------------------------|
| `gemini/client.py`                       | `clients/gemini.py`                              |
| `gemini/rate_limiter.py`                 | `rate_limit/gemini_limiter.py`                   |
| `gemini/scorer.py`                       | `scoring/prompts.py` + `scoring/parser.py`       |
| `gemini/drafter.py`                      | `email_gen/prompts.py`                           |
| `email/generator.py` + `email/validator.py` | `email_gen/prompts.py` + `email_gen/validator.py` |
| `email/gmail_client.py`                  | `clients/gmail.py`                               |
| `email/recipients.py`                    | inlined in `pipeline/draft.py` (recipient assembly is ~10 lines, not a module) |
| `phonebook/service.py`                   | `phonebook/lookup.py`                            |
| `phonebook/apollo.py`                    | `clients/apollo.py`                              |
| `phonebook/hunter.py`                    | `clients/hunter.py`                              |
| `phonebook/pattern_guess.py`             | `phonebook/email_patterns.py`                    |
| `telegram_bot/notifier.py`               | `clients/telegram.py`                            |
| `telegram_bot/signer.py`                 | `telegram_bot/codec.py`                          |

### E.2 Phase 1 — additional enum fixups

The design spec lists enum members that drifted in the Phase 1 implementation. When you reach Phase 1, define enums with the following values (the spec is authoritative; do not use any other names):

- **`JobStatus`** — DISCOVERED, PRE_FILTERED, PRE_FILTER_REJECTED, SCORED, SCORE_REJECTED, ENRICHED, ENRICH_FAILED, TAILORED, DRAFTED, AWAITING_APPROVAL, SENT, USER_REJECTED, ERROR.
- **`EmailDraftState`** (NOT `DraftState`) — GENERATED, DRAFT_CREATED, SENT, FAILED, SUPERSEDED. *Phase 8 and 9 import this name.*
- **`RejectionReason`** — BLACKLISTED, LOCATION_MISMATCH, ROLE_MISMATCH, SENIORITY_MISMATCH, SCORE_LOW, NO_EMAIL_FOUND, USER_REJECTED, MAX_RETRIES, OTHER. *Phase 5 uses `SCORE_LOW`; Phase 6 uses `NO_EMAIL_FOUND`.*
- **`CompanySizeBucket`** — SEED, SERIES_A, SERIES_B, SERIES_C, LATE_STAGE, UNKNOWN. *Phase 10 scrapers map size labels to these.*
- **`PhonebookSource`** — APOLLO, HUNTER, PATTERN_GUESS, MANUAL, SEED, CACHE. *Phase 6 reads `_PROVIDER_ORDER = (APOLLO, HUNTER)`; Phase 11 admin CLI shows `source.value`.*
- **`GeminiPurpose`** — SCORE, DRAFT_EMAIL, REGENERATE. *Phase 8 logs Pro calls with `DRAFT_EMAIL` and `REGENERATE`.*
- **`TelegramDirection`** — OUTGOING, INCOMING. *Phase 9 NotifyStage uses `OUTGOING`; handler writes `INCOMING`.*
- **`TelegramKind`** — DRAFT_PREVIEW, APPROVAL, REJECTION, COMMAND, DIGEST, ALERT. *Phase 9 writes one of {APPROVAL, REJECTION, COMMAND} per callback.*

If Phase 1's initial draft uses different names (e.g. `DraftState.PENDING`), prefer the names above. Migration: the schema isn't shipped yet at Phase 1, so just edit the enum source-of-truth file directly.

### E.3 Phase 1 — `SessionFactory` (referenced by Phase 11)

Phase 11's `cli/_deps.py` and `pipeline/factory.py` import `SessionFactory` from `knockknock.db.session`. Define it in Phase 1 alongside the `session_scope` context manager:

```python
# src/knockknock/db/session.py
from contextlib import contextmanager
from dataclasses import dataclass
from sqlalchemy.engine import Engine
from sqlmodel import Session


@dataclass(slots=True)
class SessionFactory:
    """Callable that yields a transactional Session.

    Pass this to stages/services as `session_factory: SessionFactory`,
    then use `with session_factory() as session: ...`.
    """

    engine: Engine

    @contextmanager
    def __call__(self):
        with Session(self.engine) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
```

### E.4 Phase 5 — `LimiterConfig` and `ModelQuota` are public types

Phase 11's `cli/_deps.py` imports these directly from `knockknock.rate_limit.gemini_limiter`. Phase 5 Task 5.1 already defines them — just confirm they are module-level public types (not nested inside `GeminiLimiter`).

### E.5 Phase 8 — `DraftStage.__init__` signature

Phase 11's `build_pipeline` calls `DraftStage(session_factory=, gemini=, limiter=, gmail=, preferences=, candidate_name=, candidate_email=)`. Adjust Phase 8 Task 8.3's stage constructor to accept exactly those keyword arguments. `load_resume_pdf` and `sleep_fn` from the earlier sketch should become **instance methods or module-level helpers** (default implementations baked into the stage), with test overrides done by subclassing or monkeypatching — not by constructor injection.

### E.6 Phase 9 — `NotifyStage.__init__` signature

Phase 11's factory calls `NotifyStage(session_factory=, telegram=)`. Phase 9 Task 9.3 currently shows `NotifyStage(telegram)`. Add `session_factory: SessionFactory` as the first keyword argument; the stage opens its own session inside `run_async`.

### E.7 Phase 10 — `build_scrapers` and `PlaywrightFetcher`

Both are required by Phase 11. Phase 10 Task 10.6 defines `PlaywrightFetcher` and Task 10.9 expands `build_scrapers()` to its final 6-source form — make sure to follow these tasks; do not skip them.

### E.8 Phase 11 — add `e2e` test task before factory

Insert a new Task 11.0 before 11.1: write `tests/test_e2e/test_pipeline_happy_path.py` that:

- Uses a SQLite or transactional Neon branch fixture.
- Stubs out every external client (`GeminiClient`, `ApolloClient`, `HunterClient`, `GmailClient`, `TelegramClient`, scrapers).
- Feeds one fake `ScrapedJob` through all seven stages.
- Asserts the final `JobApplication.status` is `AWAITING_APPROVAL` and exactly one `EmailDraft` row exists with `state=DRAFT_CREATED`.

This satisfies the spec's mandatory E2E test (§15) before Phase 12 deploys.
