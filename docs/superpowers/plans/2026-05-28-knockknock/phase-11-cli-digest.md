← [Index](00-index.md) · [Prev: phase-10-scrapers.md](phase-10-scrapers.md) · [Next: phase-12-deploy.md](phase-12-deploy.md)

## Phase 11: CLI Subcommands, Daily Digest, Observability Polish

**Outcome:** A clean operator experience. `knockknock status` shows pipeline run history with per-stage counts. `knockknock show <job_id>` dumps a single job's full lifecycle. `knockknock errors` lists jobs in ERROR with their `last_error`. `knockknock retry <job_id>` resets a job back to a prior status for re-processing. `knockknock digest` emits a daily summary to Telegram. A `build_pipeline()` factory centralizes wiring so the CLI, the hourly Cloud Run Job, and tests all get the same stage list. The `Stage` protocol is extended to allow async stages (NotifyStage) cleanly. Structlog gets per-run + per-stage binding so every line in a run is filterable.

### Prerequisites

- Phases 0–10 complete.
- All stage modules exist (`discover`, `pre_filter`, `score`, `enrich`, `tailor`, `draft`, `notify`).
- Settings already exposes `database_url` and Secret Manager access.

> **Note on async Stage:** Phase 9 added `NotifyStage.run_async`. Phase 11 unifies this: the runner accepts both sync `Stage` and async `AsyncStage`, dispatching via `inspect.iscoroutinefunction`. The single `run_once()` entry point internally calls `asyncio.run` for the whole pipeline if any stage is async.

### Task 11.1: Async-capable Stage protocol + runner upgrade

**Files:**
- Modify: `src/knockknock/pipeline/stage.py`
- Modify: `src/knockknock/pipeline/runner.py`
- Modify: `tests/test_pipeline/test_runner.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_pipeline/test_runner.py`:

```python
import asyncio
from dataclasses import dataclass

from knockknock.pipeline.runner import PipelineRunner
from knockknock.pipeline.stage import AsyncStage, StageContext, StageResult


@dataclass
class _AsyncFakeStage:
    name: str = "async_stage"

    async def run_async(self, ctx: StageContext) -> StageResult:
        await asyncio.sleep(0)
        return StageResult(stage=self.name, processed=2, advanced=2, rejected=0, errors=0)


def test_runner_supports_mixed_sync_and_async_stages() -> None:
    calls: list[str] = []

    @dataclass
    class _Sync:
        name: str = "sync_stage"

        def run(self, ctx: StageContext) -> StageResult:
            calls.append(self.name)
            return StageResult(stage=self.name, processed=1, advanced=1, rejected=0, errors=0)

    runner = PipelineRunner(stages=[_Sync(), _AsyncFakeStage()])
    summary = runner.run_once(StageContext(run_id=99))
    assert [r.stage for r in summary.results] == ["sync_stage", "async_stage"]
    assert summary.results[1].processed == 2
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_pipeline/test_runner.py::test_runner_supports_mixed_sync_and_async_stages -v
```

Expected: FAIL (no `AsyncStage` import).

- [ ] **Step 3: Extend stage protocol**

In `src/knockknock/pipeline/stage.py`, add `AsyncStage` alongside `Stage`:

```python
from typing import Protocol, runtime_checkable


@runtime_checkable
class Stage(Protocol):
    name: str

    def run(self, ctx: StageContext) -> StageResult: ...


@runtime_checkable
class AsyncStage(Protocol):
    name: str

    async def run_async(self, ctx: StageContext) -> StageResult: ...


AnyStage = Stage | AsyncStage
```

- [ ] **Step 4: Upgrade runner**

In `src/knockknock/pipeline/runner.py`:

```python
"""Pipeline orchestrator.

Stages run in declared order. The runner accepts both sync `Stage` and async
`AsyncStage` types and dispatches via `inspect`. When any stage is async, the
whole `run_once()` call resolves through `asyncio.run`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import structlog

from knockknock.pipeline.stage import AnyStage, AsyncStage, Stage, StageContext, StageResult

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PipelineSummary:
    run_id: int
    results: list[StageResult]


@dataclass
class PipelineRunner:
    stages: list[AnyStage] = field(default_factory=list)

    def run_once(self, ctx: StageContext) -> PipelineSummary:
        if any(isinstance(s, AsyncStage) and not isinstance(s, Stage) for s in self.stages):
            return asyncio.run(self._run_async(ctx))
        return self._run_sync(ctx)

    def _run_sync(self, ctx: StageContext) -> PipelineSummary:
        results: list[StageResult] = []
        for stage in self.stages:
            results.append(self._exec_sync(stage, ctx))
        return PipelineSummary(run_id=ctx.run_id, results=results)

    async def _run_async(self, ctx: StageContext) -> PipelineSummary:
        results: list[StageResult] = []
        for stage in self.stages:
            if isinstance(stage, AsyncStage) and not isinstance(stage, Stage):
                results.append(await self._exec_async(stage, ctx))
            else:
                results.append(self._exec_sync(stage, ctx))
        return PipelineSummary(run_id=ctx.run_id, results=results)

    def _exec_sync(self, stage: AnyStage, ctx: StageContext) -> StageResult:
        bound = log.bind(stage=stage.name, run_id=ctx.run_id)
        try:
            assert hasattr(stage, "run"), f"{stage.name} is async; cannot run synchronously"
            result = stage.run(ctx)  # type: ignore[union-attr]
        except Exception as exc:
            bound.exception("stage.failed", error=str(exc))
            result = StageResult(
                stage=stage.name, processed=0, advanced=0, rejected=0, errors=1
            )
        bound.info(
            "stage.completed",
            processed=result.processed,
            advanced=result.advanced,
            rejected=result.rejected,
            errors=result.errors,
        )
        return result

    async def _exec_async(self, stage: AsyncStage, ctx: StageContext) -> StageResult:
        bound = log.bind(stage=stage.name, run_id=ctx.run_id)
        try:
            result = await stage.run_async(ctx)
        except Exception as exc:
            bound.exception("stage.failed", error=str(exc))
            result = StageResult(
                stage=stage.name, processed=0, advanced=0, rejected=0, errors=1
            )
        bound.info(
            "stage.completed",
            processed=result.processed,
            advanced=result.advanced,
            rejected=result.rejected,
            errors=result.errors,
        )
        return result
```

- [ ] **Step 5: Run test to verify pass**

```bash
uv run pytest tests/test_pipeline/test_runner.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 6: Commit**

```bash
git add src/knockknock/pipeline/stage.py src/knockknock/pipeline/runner.py tests/test_pipeline/test_runner.py
git commit -m "feat(pipeline): support async stages via AsyncStage protocol"
```

### Task 11.2: `build_pipeline()` factory

**Files:**
- Create: `src/knockknock/pipeline/factory.py`
- Create: `tests/test_pipeline/test_factory.py`

Centralizes wiring so the CLI, the hourly job entrypoint, and tests all get the same stages, in the same order, with the same shared resources (`httpx.Client`, `GeminiClient`, `GeminiLimiter`, `GmailClient`, `TelegramClient`, `PlaywrightFetcher`).

- [ ] **Step 1: Write failing test**

Create `tests/test_pipeline/test_factory.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock

from knockknock.pipeline.factory import build_pipeline


def test_build_pipeline_returns_all_seven_stages_in_order() -> None:
    deps = MagicMock()  # any callable returns Mock; we only check stage names + order
    runner = build_pipeline(deps=deps)
    names = [s.name for s in runner.stages]
    assert names == ["discover", "pre_filter", "score", "enrich", "tailor", "draft", "notify"]
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_pipeline/test_factory.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement factory**

Create `src/knockknock/pipeline/factory.py`:

```python
"""Single source of truth for pipeline composition.

Every entrypoint (CLI, Cloud Run Job, tests) builds the pipeline through this
function so that stage order and shared resources stay consistent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from knockknock.pipeline.draft import DraftStage
from knockknock.pipeline.enrich import EnrichStage
from knockknock.pipeline.notify import NotifyStage
from knockknock.pipeline.pre_filter import PreFilterStage
from knockknock.pipeline.runner import PipelineRunner
from knockknock.pipeline.score import ScoreStage
from knockknock.pipeline.tailor import TailorStage
from knockknock.pipeline.discover import DiscoverStage

if TYPE_CHECKING:
    from knockknock.clients.gemini import GeminiClient
    from knockknock.clients.gmail import GmailClient
    from knockknock.clients.telegram import TelegramClient
    from knockknock.config.preferences import JobPreferences
    from knockknock.db.session import SessionFactory
    from knockknock.phonebook.lookup import PhonebookLookup
    from knockknock.rate_limit.gemini_limiter import GeminiLimiter
    from knockknock.resume.manifest import ResumeManifest
    from knockknock.scrapers.base import Scraper


@dataclass(slots=True)
class PipelineDeps:
    """All resources a fully-wired pipeline needs.

    Built once per process; reused across runs.
    """

    session_factory: "SessionFactory"
    preferences: "JobPreferences"
    scrapers: list["Scraper"]
    gemini: "GeminiClient"
    limiter: "GeminiLimiter"
    phonebook: "PhonebookLookup"
    resume_manifest: "ResumeManifest"
    gmail: "GmailClient"
    telegram: "TelegramClient"
    candidate_name: str
    candidate_email: str


def build_pipeline(*, deps: PipelineDeps) -> PipelineRunner:
    """Return a PipelineRunner with all seven stages wired in order."""
    stages = [
        DiscoverStage(
            scrapers=deps.scrapers,
            session_factory=deps.session_factory,
        ),
        PreFilterStage(
            session_factory=deps.session_factory,
            preferences=deps.preferences,
        ),
        ScoreStage(
            session_factory=deps.session_factory,
            gemini=deps.gemini,
            limiter=deps.limiter,
            preferences=deps.preferences,
        ),
        EnrichStage(
            session_factory=deps.session_factory,
            phonebook=deps.phonebook,
        ),
        TailorStage(
            session_factory=deps.session_factory,
            manifest=deps.resume_manifest,
        ),
        DraftStage(
            session_factory=deps.session_factory,
            gemini=deps.gemini,
            limiter=deps.limiter,
            gmail=deps.gmail,
            preferences=deps.preferences,
            candidate_name=deps.candidate_name,
            candidate_email=deps.candidate_email,
        ),
        NotifyStage(
            session_factory=deps.session_factory,
            telegram=deps.telegram,
        ),
    ]
    return PipelineRunner(stages=stages)
```

> **Note on stage constructor signatures:** stages defined in Phases 3–9 took whatever arguments suited that phase. If signatures don't match what's used here, adjust the **stages** to match this factory's calls — this is the canonical wiring. Plan-level commit message in step 6 explicitly flags any drift caught.

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/test_pipeline/test_factory.py -v
uv run mypy
```

Expected: PASS, mypy clean. If stage `__init__` mismatches surface, update the stage signatures (not the factory) and rerun.

- [ ] **Step 5: Commit**

```bash
git add src/knockknock/pipeline/factory.py tests/test_pipeline/test_factory.py
git commit -m "feat(pipeline): add build_pipeline() factory as single source of stage wiring"
```

### Task 11.3: `cli/_deps.py` — shared resource builder

**Files:**
- Create: `src/knockknock/cli/__init__.py`
- Create: `src/knockknock/cli/_deps.py`

A factory used by every CLI subcommand to build `PipelineDeps` from settings + secrets. Wraps the heavy startup (Gemini client, Gmail OAuth, Playwright, DB engine) into one call so subcommands stay tidy.

- [ ] **Step 1: Implement** (no test — this is glue code; integration is tested via subcommands)

Create `src/knockknock/cli/__init__.py` (empty).

Create `src/knockknock/cli/_deps.py`:

```python
"""Build `PipelineDeps` for the CLI from Settings + Secret Manager."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import httpx
import structlog

from knockknock.clients.apollo import ApolloClient
from knockknock.clients.gemini import build_gemini_client
from knockknock.clients.gmail import build_gmail_service, GmailClient
from knockknock.clients.hunter import HunterClient
from knockknock.clients.telegram import TelegramClient
from knockknock.config.preferences import load_preferences
from knockknock.config.secrets import build_secrets_client
from knockknock.config.settings import Settings
from knockknock.db.engine import make_sync_engine
from knockknock.db.session import SessionFactory
from knockknock.phonebook.lookup import PhonebookLookup
from knockknock.pipeline.factory import PipelineDeps
from knockknock.rate_limit.gemini_limiter import GeminiLimiter, LimiterConfig, ModelQuota
from knockknock.resume.manifest import load_manifest
from knockknock.scrapers._playwright import PlaywrightFetcher
from knockknock.scrapers.registry import build_scrapers

log = structlog.get_logger(__name__)


def _default_limiter_config() -> LimiterConfig:
    from knockknock.db.enums import GeminiModel

    return LimiterConfig(
        safety_ceiling_rpd=500,
        quotas={
            GeminiModel.FLASH_2_5: ModelQuota(rpm=15, tpm=1_000_000, rpd=1500),
            GeminiModel.PRO_2_5: ModelQuota(rpm=2, tpm=32_000, rpd=50),
        },
        pro_draft_soft_cap=40,
    )


@contextmanager
def cli_deps():
    """Yields a fully-wired `PipelineDeps` and tears down resources on exit."""
    settings = Settings()
    secrets = build_secrets_client(settings)
    engine = make_sync_engine(settings.database_url)
    session_factory = SessionFactory(engine)
    preferences = load_preferences(Path(settings.preferences_path))
    manifest = load_manifest(Path(settings.resume_manifest_path))

    http = httpx.Client(timeout=30)
    fetcher = PlaywrightFetcher()
    scrapers = build_scrapers(preferences, http_client=http, fetcher=fetcher)

    gemini_api_key = secrets.get("GEMINI_API_KEY")
    gemini = build_gemini_client(api_key=gemini_api_key)
    limiter = GeminiLimiter(session_factory=session_factory, config=_default_limiter_config())

    apollo = ApolloClient(api_key=secrets.get("APOLLO_API_KEY"), http=http)
    hunter = HunterClient(api_key=secrets.get("HUNTER_API_KEY"), http=http)
    phonebook = PhonebookLookup(apollo=apollo, hunter=hunter)

    gmail_service = build_gmail_service(
        client_id=secrets.get("GOOGLE_OAUTH_CLIENT_ID"),
        client_secret=secrets.get("GOOGLE_OAUTH_CLIENT_SECRET"),
        refresh_token=secrets.get("GMAIL_REFRESH_TOKEN"),
    )
    gmail = GmailClient(service=gmail_service)

    from telegram import Bot

    bot = Bot(token=secrets.get("TELEGRAM_BOT_TOKEN"))
    telegram = TelegramClient(
        bot=bot,
        admin_chat_id=int(secrets.get("TELEGRAM_ADMIN_CHAT_ID")),
        callback_secret=secrets.get("TELEGRAM_CALLBACK_SECRET"),
    )

    deps = PipelineDeps(
        session_factory=session_factory,
        preferences=preferences,
        scrapers=scrapers,
        gemini=gemini,
        limiter=limiter,
        phonebook=phonebook,
        resume_manifest=manifest,
        gmail=gmail,
        telegram=telegram,
        candidate_name=preferences.candidate.name,
        candidate_email=settings.candidate_email,
    )
    try:
        yield deps
    finally:
        http.close()
        engine.dispose()
```

> **Note on `Settings.preferences_path`, `Settings.resume_manifest_path`, `Settings.candidate_email`:** if Phase 2's Settings doesn't include these, add them now (defaults: `config/job_preferences.yaml`, `resumes/manifest.yaml`, mandatory env var).

- [ ] **Step 2: mypy passes**

```bash
uv run mypy src/knockknock/cli/_deps.py
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add src/knockknock/cli/__init__.py src/knockknock/cli/_deps.py
git commit -m "feat(cli): add cli_deps() context manager for shared resources"
```

### Task 11.4: `knockknock pipeline run` uses factory

**Files:**
- Modify: `src/knockknock/__main__.py`

- [ ] **Step 1: Rewrite the pipeline run command**

In `src/knockknock/__main__.py`, replace the existing `pipeline run` implementation with:

```python
@pipeline_app.command("run")
def pipeline_run(
    once: bool = typer.Option(False, "--once", help="Run a single pass and exit."),
    skip_after: str | None = typer.Option(
        None,
        "--skip-after",
        help="Run stages up to and including this one, then stop.",
    ),
) -> None:
    """Execute the hourly pipeline pass."""
    from knockknock.cli._deps import cli_deps
    from knockknock.db.models import PipelineRun
    from knockknock.pipeline.factory import build_pipeline
    from knockknock.pipeline.stage import StageContext

    with cli_deps() as deps:
        with deps.session_factory() as session:
            run_row = PipelineRun(triggered_by="cli", status="RUNNING")
            session.add(run_row)
            session.commit()
            session.refresh(run_row)
            run_id = run_row.id

        runner = build_pipeline(deps=deps)
        if skip_after:
            stages = []
            for s in runner.stages:
                stages.append(s)
                if s.name == skip_after:
                    break
            runner.stages = stages

        summary = runner.run_once(StageContext(run_id=run_id))

        with deps.session_factory() as session:
            row = session.get(PipelineRun, run_id)
            assert row is not None
            row.status = "COMPLETED"
            row.results_json = [r.__dict__ for r in summary.results]
            session.commit()

    typer.echo(f"Run {run_id} complete — {len(summary.results)} stages")
```

> **Note on PipelineRun fields:** `triggered_by`, `status`, `results_json` should exist from Phase 1 schema. If not, add via a small Alembic migration in this task.

- [ ] **Step 2: Smoke-test**

```bash
uv run knockknock pipeline run --once --skip-after pre_filter
```

Expected: completes; `pipeline_runs` row has `status='COMPLETED'`.

- [ ] **Step 3: Commit**

```bash
git add src/knockknock/__main__.py
git commit -m "feat(cli): wire pipeline run through build_pipeline factory"
```

### Task 11.5: `knockknock status`

**Files:**
- Create: `src/knockknock/cli/status.py`
- Modify: `src/knockknock/__main__.py`
- Create: `tests/test_cli/__init__.py`
- Create: `tests/test_cli/test_status.py`

Lists the last N pipeline runs with start/end timestamps and per-stage counts.

- [ ] **Step 1: Write failing test**

Create `tests/test_cli/__init__.py` (empty).

Create `tests/test_cli/test_status.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

from typer.testing import CliRunner

from knockknock.__main__ import app
from knockknock.db.models import PipelineRun


def test_status_lists_recent_runs(db_session, monkeypatch) -> None:
    db_session.add_all(
        [
            PipelineRun(
                id=1,
                triggered_by="cli",
                status="COMPLETED",
                started_at=datetime(2026, 5, 28, 10, 0, tzinfo=UTC),
                completed_at=datetime(2026, 5, 28, 10, 5, tzinfo=UTC),
                results_json=[
                    {"stage": "discover", "processed": 12, "advanced": 12, "rejected": 0, "errors": 0},
                    {"stage": "score", "processed": 12, "advanced": 4, "rejected": 8, "errors": 0},
                ],
            ),
            PipelineRun(
                id=2,
                triggered_by="scheduler",
                status="FAILED",
                started_at=datetime(2026, 5, 28, 11, 0, tzinfo=UTC),
                results_json=[],
            ),
        ]
    )
    db_session.commit()

    from knockknock.cli import _deps

    class _StubDeps:
        session_factory = db_session._session_factory  # provided by your conftest

    monkeypatch.setattr(_deps, "cli_deps", lambda: _StubCM(_StubDeps()))  # noqa: F821

    runner = CliRunner()
    result = runner.invoke(app, ["status", "--limit", "5"])
    assert result.exit_code == 0
    assert "1" in result.stdout and "COMPLETED" in result.stdout
    assert "discover" in result.stdout
    assert "FAILED" in result.stdout
```

(The `_StubCM` helper and `db_session._session_factory` are conventions that your existing test conftest should expose; if they don't, add minimal versions in `tests/conftest.py` matching the patterns from Phases 1 and 3.)

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_cli/test_status.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement command**

Create `src/knockknock/cli/status.py`:

```python
"""`knockknock status` — list recent pipeline runs."""

from __future__ import annotations

from sqlalchemy import desc, select
from rich.console import Console
from rich.table import Table

from knockknock.cli._deps import cli_deps
from knockknock.db.models import PipelineRun


def status_command(limit: int = 10) -> None:
    console = Console()
    with cli_deps() as deps:
        with deps.session_factory() as session:
            rows = (
                session.execute(
                    select(PipelineRun).order_by(desc(PipelineRun.started_at)).limit(limit)
                )
                .scalars()
                .all()
            )

    table = Table(title=f"Last {limit} pipeline runs")
    table.add_column("Run", justify="right")
    table.add_column("Started")
    table.add_column("Status")
    table.add_column("Stage counts (processed/advanced/rejected/errors)")

    for row in rows:
        counts = "  ".join(
            f"{r['stage']}={r['processed']}/{r['advanced']}/{r['rejected']}/{r['errors']}"
            for r in (row.results_json or [])
        )
        table.add_row(
            str(row.id),
            row.started_at.isoformat(timespec="seconds") if row.started_at else "—",
            row.status,
            counts or "—",
        )
    console.print(table)
```

In `src/knockknock/__main__.py`, register:

```python
from knockknock.cli.status import status_command


@app.command("status")
def status(limit: int = typer.Option(10, "--limit", "-n")) -> None:
    """Show recent pipeline runs."""
    status_command(limit=limit)
```

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/test_cli/test_status.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/knockknock/cli/status.py src/knockknock/__main__.py tests/test_cli/test_status.py tests/test_cli/__init__.py
git commit -m "feat(cli): add `knockknock status` command"
```

### Task 11.6: `knockknock show <job_id>`

**Files:**
- Create: `src/knockknock/cli/show.py`
- Modify: `src/knockknock/__main__.py`
- Create: `tests/test_cli/test_show.py`

Dumps a single job's `JobApplication`, its `Company`, all related `JobApplicationEvent` rows in chronological order, and the latest `EmailDraft` (if any).

- [ ] **Step 1: Write failing test**

Create `tests/test_cli/test_show.py`:

```python
from __future__ import annotations

from typer.testing import CliRunner


def test_show_renders_lifecycle(populated_job_with_events, monkeypatch) -> None:
    from knockknock.__main__ import app
    from knockknock.cli import _deps

    monkeypatch.setattr(
        _deps, "cli_deps", lambda: populated_job_with_events.cli_deps_cm()
    )
    runner = CliRunner()
    result = runner.invoke(app, ["show", str(populated_job_with_events.job_id)])
    assert result.exit_code == 0
    assert "Acme Labs" in result.stdout
    assert "TAILORED" in result.stdout
    assert "discover" in result.stdout  # event stage column
```

(`populated_job_with_events` fixture must be added to `tests/conftest.py`; it creates one Company + one JobApplication + several JobApplicationEvent rows and exposes a `cli_deps_cm()` returning a context manager wrapping `_StubDeps`.)

- [ ] **Step 2: Implement command**

Create `src/knockknock/cli/show.py`:

```python
"""`knockknock show <job_id>` — dump a job's full lifecycle."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from sqlalchemy import select

from knockknock.cli._deps import cli_deps
from knockknock.db.models import (
    Company,
    EmailDraft,
    JobApplication,
    JobApplicationEvent,
)


def show_command(job_id: int) -> None:
    console = Console()
    with cli_deps() as deps:
        with deps.session_factory() as session:
            job = session.get(JobApplication, job_id)
            if job is None:
                console.print(f"[red]Job {job_id} not found.[/]")
                raise typer.Exit(1)
            company = session.get(Company, job.company_id)
            events = (
                session.execute(
                    select(JobApplicationEvent)
                    .where(JobApplicationEvent.job_application_id == job_id)
                    .order_by(JobApplicationEvent.created_at)
                )
                .scalars()
                .all()
            )
            latest_draft = (
                session.execute(
                    select(EmailDraft)
                    .where(EmailDraft.job_application_id == job_id)
                    .order_by(EmailDraft.created_at.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )

    header = Panel.fit(
        f"[bold]{company.name if company else '?'}[/] — {job.title}\n"
        f"Status: [cyan]{job.status}[/]   Score: {job.score or '—'}   "
        f"Source: {job.source}   ID: {job.id}",
        title="Job",
    )
    console.print(header)

    events_table = Table(title="Lifecycle")
    events_table.add_column("When")
    events_table.add_column("Stage")
    events_table.add_column("Kind")
    events_table.add_column("Detail")
    for ev in events:
        events_table.add_row(
            ev.created_at.isoformat(timespec="seconds"),
            ev.stage,
            ev.kind,
            (ev.detail or "")[:80],
        )
    console.print(events_table)

    if latest_draft:
        draft_panel = Panel(
            f"To: {latest_draft.to_email}\nCC: {latest_draft.cc_email or '—'}\n"
            f"Subject: {latest_draft.subject}\n\n{latest_draft.body_text[:600]}…",
            title=f"Latest draft (state={latest_draft.state})",
        )
        console.print(draft_panel)
```

Register in `__main__.py`:

```python
from knockknock.cli.show import show_command


@app.command("show")
def show(job_id: int = typer.Argument(...)) -> None:
    """Print the full lifecycle of one job."""
    show_command(job_id=job_id)
```

- [ ] **Step 3: Run test to verify pass**

```bash
uv run pytest tests/test_cli/test_show.py -v
```

- [ ] **Step 4: Commit**

```bash
git add src/knockknock/cli/show.py src/knockknock/__main__.py tests/test_cli/test_show.py
git commit -m "feat(cli): add `knockknock show <job_id>` command"
```

### Task 11.7: `knockknock errors`

**Files:**
- Create: `src/knockknock/cli/errors.py`
- Modify: `src/knockknock/__main__.py`
- Create: `tests/test_cli/test_errors.py`

Lists all `JobApplication` rows currently in `ERROR` status with `last_error`, `retry_count`, last-modified timestamp.

- [ ] **Step 1: Write failing test**

Create `tests/test_cli/test_errors.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

from typer.testing import CliRunner

from knockknock.db.enums import JobStatus, JobSource
from knockknock.db.models import Company, JobApplication


def test_errors_lists_failed_jobs(db_session, monkeypatch) -> None:
    company = Company(id=1, name="X", domain="x.test")
    db_session.add(company)
    db_session.add_all(
        [
            JobApplication(
                id=10,
                company_id=1,
                source=JobSource.HN,
                source_job_id="x:1",
                title="Engineer",
                status=JobStatus.ERROR,
                last_error="boom",
                retry_count=2,
                updated_at=datetime(2026, 5, 28, tzinfo=UTC),
            ),
            JobApplication(
                id=11,
                company_id=1,
                source=JobSource.HN,
                source_job_id="x:2",
                title="Other",
                status=JobStatus.SCORED,
            ),
        ]
    )
    db_session.commit()

    from knockknock.__main__ import app
    from knockknock.cli import _deps

    monkeypatch.setattr(_deps, "cli_deps", lambda: db_session.cli_deps_cm())  # via conftest helper

    result = CliRunner().invoke(app, ["errors"])
    assert result.exit_code == 0
    assert "10" in result.stdout
    assert "boom" in result.stdout
    assert "11" not in result.stdout
```

- [ ] **Step 2: Implement command**

Create `src/knockknock/cli/errors.py`:

```python
"""`knockknock errors` — list jobs currently in ERROR status."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from knockknock.cli._deps import cli_deps
from knockknock.db.enums import JobStatus
from knockknock.db.models import Company, JobApplication


def errors_command(limit: int = 50) -> None:
    console = Console()
    with cli_deps() as deps:
        with deps.session_factory() as session:
            rows = (
                session.execute(
                    select(JobApplication, Company.name)
                    .join(Company, JobApplication.company_id == Company.id)
                    .where(JobApplication.status == JobStatus.ERROR)
                    .order_by(JobApplication.updated_at.desc())
                    .limit(limit)
                )
                .all()
            )

    table = Table(title=f"Jobs in ERROR (up to {limit})")
    table.add_column("ID", justify="right")
    table.add_column("Company")
    table.add_column("Title")
    table.add_column("Retries", justify="right")
    table.add_column("Last error")
    table.add_column("Updated")

    for job, company_name in rows:
        table.add_row(
            str(job.id),
            company_name,
            job.title,
            str(job.retry_count or 0),
            (job.last_error or "")[:60],
            job.updated_at.isoformat(timespec="seconds") if job.updated_at else "—",
        )
    console.print(table)
```

Register in `__main__.py`:

```python
from knockknock.cli.errors import errors_command


@app.command("errors")
def errors(limit: int = typer.Option(50, "--limit", "-n")) -> None:
    """List jobs in ERROR status with last_error."""
    errors_command(limit=limit)
```

- [ ] **Step 3: Run test to verify pass**

```bash
uv run pytest tests/test_cli/test_errors.py -v
```

- [ ] **Step 4: Commit**

```bash
git add src/knockknock/cli/errors.py src/knockknock/__main__.py tests/test_cli/test_errors.py
git commit -m "feat(cli): add `knockknock errors` command"
```

### Task 11.8: `knockknock retry <job_id>`

**Files:**
- Create: `src/knockknock/cli/retry.py`
- Modify: `src/knockknock/__main__.py`
- Create: `tests/test_cli/test_retry.py`

Moves a job back to a chosen status so it'll be re-picked up on the next pipeline run. Validates that the target status is one of the "stage-input" statuses (DISCOVERED, PRE_FILTERED, SCORED, ENRICHED, TAILORED).

- [ ] **Step 1: Write failing test**

Create `tests/test_cli/test_retry.py`:

```python
from __future__ import annotations

from typer.testing import CliRunner

from knockknock.db.enums import JobSource, JobStatus
from knockknock.db.models import Company, JobApplication


def test_retry_resets_status_and_clears_error(db_session, monkeypatch) -> None:
    db_session.add(Company(id=1, name="X", domain="x.test"))
    db_session.add(
        JobApplication(
            id=42,
            company_id=1,
            source=JobSource.HN,
            source_job_id="x:42",
            title="Eng",
            status=JobStatus.ERROR,
            last_error="boom",
            retry_count=3,
        )
    )
    db_session.commit()

    from knockknock.__main__ import app
    from knockknock.cli import _deps

    monkeypatch.setattr(_deps, "cli_deps", lambda: db_session.cli_deps_cm())

    result = CliRunner().invoke(app, ["retry", "42", "--to", "SCORED"])
    assert result.exit_code == 0

    job = db_session.get(JobApplication, 42)
    assert job.status == JobStatus.SCORED
    assert job.last_error is None
    assert job.retry_count == 3  # retry_count is informational; not reset


def test_retry_rejects_terminal_target(db_session, monkeypatch) -> None:
    db_session.add(Company(id=1, name="X", domain="x.test"))
    db_session.add(
        JobApplication(
            id=43,
            company_id=1,
            source=JobSource.HN,
            source_job_id="x:43",
            title="Eng",
            status=JobStatus.ERROR,
        )
    )
    db_session.commit()

    from knockknock.__main__ import app
    from knockknock.cli import _deps

    monkeypatch.setattr(_deps, "cli_deps", lambda: db_session.cli_deps_cm())

    result = CliRunner().invoke(app, ["retry", "43", "--to", "SENT"])
    assert result.exit_code != 0
    assert "not a retry target" in result.stdout
```

- [ ] **Step 2: Implement command**

Create `src/knockknock/cli/retry.py`:

```python
"""`knockknock retry <job_id> --to <status>` — re-queue a job."""

from __future__ import annotations

import typer
from rich.console import Console

from knockknock.cli._deps import cli_deps
from knockknock.db.enums import JobStatus
from knockknock.db.models import JobApplication, JobApplicationEvent

_RETRY_TARGETS = frozenset(
    {
        JobStatus.DISCOVERED,
        JobStatus.PRE_FILTERED,
        JobStatus.SCORED,
        JobStatus.ENRICHED,
        JobStatus.TAILORED,
    }
)


def retry_command(job_id: int, to: str) -> None:
    console = Console()
    try:
        target = JobStatus(to)
    except ValueError:
        console.print(f"Unknown status: {to}")
        raise typer.Exit(2)
    if target not in _RETRY_TARGETS:
        console.print(f"{target} is not a retry target. Allowed: {sorted(s.value for s in _RETRY_TARGETS)}")
        raise typer.Exit(2)

    with cli_deps() as deps:
        with deps.session_factory() as session:
            job = session.get(JobApplication, job_id)
            if job is None:
                console.print(f"Job {job_id} not found.")
                raise typer.Exit(1)
            previous = job.status
            job.status = target
            job.last_error = None
            session.add(
                JobApplicationEvent(
                    job_application_id=job_id,
                    stage="cli_retry",
                    kind="STATUS_RESET",
                    detail=f"{previous} -> {target}",
                )
            )
            session.commit()
    console.print(f"Job {job_id}: {previous} -> {target}")
```

Register in `__main__.py`:

```python
from knockknock.cli.retry import retry_command


@app.command("retry")
def retry(
    job_id: int = typer.Argument(...),
    to: str = typer.Option(..., "--to", help="Status to reset to."),
) -> None:
    """Reset a job's status so the pipeline re-processes it."""
    retry_command(job_id=job_id, to=to)
```

- [ ] **Step 3: Run tests to verify pass**

```bash
uv run pytest tests/test_cli/test_retry.py -v
```

- [ ] **Step 4: Commit**

```bash
git add src/knockknock/cli/retry.py src/knockknock/__main__.py tests/test_cli/test_retry.py
git commit -m "feat(cli): add `knockknock retry <job_id> --to <status>` command"
```

### Task 11.9: Daily digest

**Files:**
- Create: `src/knockknock/pipeline/digest.py`
- Modify: `src/knockknock/__main__.py`
- Create: `tests/test_pipeline/test_digest.py`

Aggregates the last 24h: # scraped, # scored ≥ threshold, # drafted, # approved/sent, # rejected, top 3 errors. Posts as a single Telegram message.

- [ ] **Step 1: Write failing test**

Create `tests/test_pipeline/test_digest.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from knockknock.db.enums import EmailDraftState, JobSource, JobStatus
from knockknock.db.models import Company, EmailDraft, JobApplication
from knockknock.pipeline.digest import build_digest_text


def test_build_digest_text_counts_last_24h(db_session) -> None:
    now = datetime.now(UTC)
    fresh = now - timedelta(hours=3)
    old = now - timedelta(hours=30)
    db_session.add(Company(id=1, name="X", domain="x.test"))
    db_session.add_all(
        [
            JobApplication(id=1, company_id=1, source=JobSource.HN, source_job_id="x:1",
                           title="A", status=JobStatus.SENT, discovered_at=fresh, updated_at=fresh),
            JobApplication(id=2, company_id=1, source=JobSource.HN, source_job_id="x:2",
                           title="B", status=JobStatus.SCORED, score=85,
                           discovered_at=fresh, updated_at=fresh),
            JobApplication(id=3, company_id=1, source=JobSource.HN, source_job_id="x:3",
                           title="C", status=JobStatus.ERROR, last_error="boom",
                           discovered_at=old, updated_at=old),  # outside window
        ]
    )
    db_session.add(
        EmailDraft(id=1, job_application_id=1, to_email="x@x.test", subject="x",
                   body_text="x", state=EmailDraftState.SENT, created_at=fresh)
    )
    db_session.commit()

    text = build_digest_text(db_session, window_hours=24)
    assert "Discovered: 2" in text  # jobs 1 + 2 in window
    assert "Sent: 1" in text
    assert "Errors: 0" in text  # job 3 is outside the window
```

- [ ] **Step 2: Implement**

Create `src/knockknock/pipeline/digest.py`:

```python
"""Daily digest: 24h summary posted to Telegram."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from knockknock.db.enums import EmailDraftState, JobStatus
from knockknock.db.models import EmailDraft, JobApplication


def build_digest_text(session: Session, *, window_hours: int = 24) -> str:
    """Return a Markdown summary of the last `window_hours`."""
    since = datetime.now(UTC) - timedelta(hours=window_hours)

    def _count(stmt) -> int:
        return int(session.execute(stmt).scalar_one() or 0)

    discovered = _count(
        select(func.count(JobApplication.id)).where(JobApplication.discovered_at >= since)
    )
    scored = _count(
        select(func.count(JobApplication.id))
        .where(JobApplication.discovered_at >= since)
        .where(JobApplication.status.in_([
            JobStatus.SCORED, JobStatus.ENRICHED, JobStatus.TAILORED,
            JobStatus.AWAITING_APPROVAL, JobStatus.SENT, JobStatus.USER_REJECTED,
        ]))
    )
    drafted = _count(
        select(func.count(EmailDraft.id))
        .where(EmailDraft.created_at >= since)
        .where(EmailDraft.state == EmailDraftState.DRAFT_CREATED)
    )
    sent = _count(
        select(func.count(EmailDraft.id))
        .where(EmailDraft.created_at >= since)
        .where(EmailDraft.state == EmailDraftState.SENT)
    )
    rejected = _count(
        select(func.count(JobApplication.id))
        .where(JobApplication.updated_at >= since)
        .where(JobApplication.status == JobStatus.USER_REJECTED)
    )
    errors = _count(
        select(func.count(JobApplication.id))
        .where(JobApplication.updated_at >= since)
        .where(JobApplication.status == JobStatus.ERROR)
    )

    top_errors = (
        session.execute(
            select(JobApplication.last_error, func.count(JobApplication.id))
            .where(JobApplication.updated_at >= since)
            .where(JobApplication.status == JobStatus.ERROR)
            .where(JobApplication.last_error.is_not(None))
            .group_by(JobApplication.last_error)
            .order_by(func.count(JobApplication.id).desc())
            .limit(3)
        )
        .all()
    )

    lines = [
        f"<b>Knockknock — last {window_hours}h</b>",
        f"Discovered: {discovered}",
        f"Scored ≥ threshold: {scored}",
        f"Drafts created: {drafted}",
        f"Sent: {sent}",
        f"Rejected: {rejected}",
        f"Errors: {errors}",
    ]
    if top_errors:
        lines.append("\n<b>Top errors</b>")
        for msg, count in top_errors:
            lines.append(f"• {count}× {msg[:80]}")
    return "\n".join(lines)
```

In `src/knockknock/__main__.py`:

```python
@app.command("digest")
def digest(hours: int = typer.Option(24, "--hours", "-h")) -> None:
    """Post a Markdown digest of the last N hours to the admin Telegram chat."""
    from knockknock.cli._deps import cli_deps
    from knockknock.pipeline.digest import build_digest_text

    with cli_deps() as deps:
        with deps.session_factory() as session:
            text = build_digest_text(session, window_hours=hours)
        deps.telegram.send_text(text)
    typer.echo("Digest sent.")
```

- [ ] **Step 3: Run test to verify pass**

```bash
uv run pytest tests/test_pipeline/test_digest.py -v
uv run mypy
```

- [ ] **Step 4: Commit**

```bash
git add src/knockknock/pipeline/digest.py src/knockknock/__main__.py tests/test_pipeline/test_digest.py
git commit -m "feat(cli): add daily digest command (knockknock digest --hours N)"
```

### Task 11.10: Structlog polish — per-run + per-stage binding

**Files:**
- Modify: `src/knockknock/logging.py`
- Modify: `src/knockknock/pipeline/runner.py` (verify binding)

The runner already binds `stage=` and `run_id=`. This task adds the global processor chain and a `bind_request_id` helper for the Telegram webhook.

- [ ] **Step 1: Update logging config**

In `src/knockknock/logging.py`:

```python
"""Structlog configuration. Called once at process start."""

from __future__ import annotations

import logging
import sys

import structlog

from knockknock.config.settings import Settings


def configure_logging(settings: Settings) -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            (
                structlog.dev.ConsoleRenderer()
                if settings.log_format == "console"
                else structlog.processors.JSONRenderer()
            ),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )


def bind_run_id(run_id: int) -> None:
    structlog.contextvars.bind_contextvars(run_id=run_id)


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()
```

- [ ] **Step 2: Verify Settings has `log_level` and `log_format`**

In `src/knockknock/config/settings.py`, ensure:

```python
log_level: str = "INFO"
log_format: Literal["console", "json"] = "json"
```

- [ ] **Step 3: Smoke**

```bash
uv run knockknock --help   # any subcommand that runs configure_logging
```

Expected: JSON or console output depending on `LOG_FORMAT` env var.

- [ ] **Step 4: Commit**

```bash
git add src/knockknock/logging.py src/knockknock/config/settings.py
git commit -m "feat(logging): add contextvars-based run/stage binding"
```

### Task 11.11: Per-source `scraper_states` updates

**Files:**
- Modify: `src/knockknock/pipeline/discover.py`
- Create: `tests/test_pipeline/test_discover_scraper_state.py`

When the discover stage runs, it should update `scraper_states.last_success_at`, `last_error`, and `consecutive_failure_count`. After 3 consecutive failures, set `is_disabled=true` and log a critical alert.

- [ ] **Step 1: Write failing test**

Create `tests/test_pipeline/test_discover_scraper_state.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

from knockknock.db.enums import JobSource
from knockknock.db.models import ScraperState
from knockknock.pipeline.discover import DiscoverStage
from knockknock.pipeline.stage import StageContext


class _BoomScraper:
    source = JobSource.WELLFOUND
    name = "wellfound"

    def scrape(self):
        raise RuntimeError("rendered nothing")


def test_discover_increments_consecutive_failures_and_disables(db_session) -> None:
    db_session.add(
        ScraperState(source=JobSource.WELLFOUND, consecutive_failure_count=2, is_disabled=False)
    )
    db_session.commit()

    stage = DiscoverStage(
        scrapers=[_BoomScraper()],
        session_factory=db_session._session_factory,
    )
    stage.run(StageContext(run_id=1))

    state = db_session.get(ScraperState, JobSource.WELLFOUND)
    assert state.consecutive_failure_count == 3
    assert state.is_disabled is True
    assert state.last_error is not None


def test_discover_resets_failure_count_on_success(db_session) -> None:
    db_session.add(
        ScraperState(
            source=JobSource.WELLFOUND, consecutive_failure_count=2, is_disabled=False,
        )
    )
    db_session.commit()

    class _GoodScraper:
        source = JobSource.WELLFOUND
        name = "wellfound"

        def scrape(self):
            return iter([])

    stage = DiscoverStage(
        scrapers=[_GoodScraper()],
        session_factory=db_session._session_factory,
    )
    stage.run(StageContext(run_id=2))

    state = db_session.get(ScraperState, JobSource.WELLFOUND)
    assert state.consecutive_failure_count == 0
    assert state.last_success_at is not None
```

- [ ] **Step 2: Implement**

In `src/knockknock/pipeline/discover.py`, wrap each `scraper.scrape()` call in a try/except and update `ScraperState`:

```python
from datetime import UTC, datetime
from knockknock.db.models import ScraperState


def _ensure_state(session, source) -> ScraperState:
    state = session.get(ScraperState, source)
    if state is None:
        state = ScraperState(source=source)
        session.add(state)
        session.flush()
    return state


def _record_failure(state: ScraperState, exc: Exception) -> None:
    state.consecutive_failure_count = (state.consecutive_failure_count or 0) + 1
    state.last_error = f"{type(exc).__name__}: {exc}"[:500]
    if state.consecutive_failure_count >= 3:
        state.is_disabled = True


def _record_success(state: ScraperState) -> None:
    state.consecutive_failure_count = 0
    state.last_error = None
    state.last_success_at = datetime.now(UTC)
```

Then in `DiscoverStage.run`, around the scrape loop:

```python
for scraper in self._scrapers:
    with self._session_factory() as session:
        state = _ensure_state(session, scraper.source)
        if state.is_disabled:
            log.warning("scraper.skipped_disabled", source=scraper.source.value)
            session.commit()
            continue
        try:
            jobs = list(scraper.scrape())
        except Exception as exc:
            log.exception("scraper.failed", source=scraper.source.value)
            _record_failure(state, exc)
            session.commit()
            errors += 1
            continue
        _record_success(state)
        session.commit()
    # ... existing upsert logic for `jobs` ...
```

- [ ] **Step 3: Run test to verify pass**

```bash
uv run pytest tests/test_pipeline/test_discover_scraper_state.py -v
```

- [ ] **Step 4: Commit**

```bash
git add src/knockknock/pipeline/discover.py tests/test_pipeline/test_discover_scraper_state.py
git commit -m "feat(pipeline): track scraper success/failure in scraper_states, auto-disable after 3 fails"
```

### Task 11.12: Optional admin commands — `phonebook show`, `blacklist add`

**Files:**
- Create: `src/knockknock/cli/admin.py`
- Modify: `src/knockknock/__main__.py`

Quality-of-life:
- `knockknock phonebook show <domain>` — print cached PhonebookEntry rows.
- `knockknock blacklist add <domain> --reason "..."` — append to `config/blacklist.yaml` and force re-sync.

- [ ] **Step 1: Implement**

Create `src/knockknock/cli/admin.py`:

```python
"""Admin sub-commands: phonebook show, blacklist add."""

from __future__ import annotations

import typer
from pathlib import Path
from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from knockknock.cli._deps import cli_deps
from knockknock.db.models import PhonebookEntry
from knockknock.filter.blacklist import sync_blacklist_yaml

phonebook_app = typer.Typer(help="Inspect phonebook cache.")
blacklist_app = typer.Typer(help="Maintain blacklist.")


@phonebook_app.command("show")
def phonebook_show(domain: str) -> None:
    console = Console()
    with cli_deps() as deps:
        with deps.session_factory() as session:
            rows = (
                session.execute(
                    select(PhonebookEntry).where(PhonebookEntry.domain == domain)
                )
                .scalars()
                .all()
            )
    table = Table(title=f"phonebook[{domain}]")
    for col in ("ID", "Name", "Email", "Source", "Verified"):
        table.add_column(col)
    for row in rows:
        table.add_row(
            str(row.id),
            row.founder_name or "—",
            row.founder_email or row.careers_email or "—",
            row.source.value if row.source else "—",
            "yes" if row.verified else "no",
        )
    console.print(table)


@blacklist_app.command("add")
def blacklist_add(
    domain: str,
    reason: str = typer.Option(..., "--reason", help="Why this domain is blacklisted."),
    path: Path = typer.Option(Path("config/blacklist.yaml"), "--path"),
) -> None:
    """Append a domain to the blacklist YAML and re-sync into DB."""
    import yaml

    data = yaml.safe_load(path.read_text()) or {"domains": []}
    if any(d.get("domain") == domain for d in data["domains"]):
        typer.echo(f"{domain} already blacklisted.")
        raise typer.Exit(0)
    data["domains"].append({"domain": domain, "reason": reason})
    path.write_text(yaml.safe_dump(data, sort_keys=False))

    with cli_deps() as deps:
        with deps.session_factory() as session:
            sync_blacklist_yaml(session, path)
    typer.echo(f"Added {domain} to blacklist.")
```

In `__main__.py`:

```python
from knockknock.cli.admin import blacklist_app, phonebook_app

app.add_typer(phonebook_app, name="phonebook")
app.add_typer(blacklist_app, name="blacklist")
```

- [ ] **Step 2: Smoke-test**

```bash
uv run knockknock phonebook show stripe.com
uv run knockknock blacklist add example.com --reason "test"
```

- [ ] **Step 3: Commit**

```bash
git add src/knockknock/cli/admin.py src/knockknock/__main__.py
git commit -m "feat(cli): add phonebook show + blacklist add admin commands"
```

### Task 11.13: README operator quickstart

**Files:**
- Modify: `README.md`

- [ ] Add a "## Operator commands" section listing:
  - `knockknock pipeline run [--once]`
  - `knockknock status [-n N]`
  - `knockknock show <job_id>`
  - `knockknock errors [-n N]`
  - `knockknock retry <job_id> --to <STATUS>`
  - `knockknock digest [--hours N]`
  - `knockknock phonebook show <domain>`
  - `knockknock blacklist add <domain> --reason "..."`

- [ ] Commit:

```bash
git add README.md
git commit -m "docs: add operator commands quickstart"
```

---

### Phase 11 Wrap-up

After this phase:
- Async-capable `Stage` protocol; runner handles mixed sync/async stages.
- `build_pipeline()` is the canonical wiring; CLI + future Cloud Run Job entrypoint use it.
- Operator commands cover daily ops: status, show, errors, retry, digest, phonebook, blacklist.
- Scraper auto-disable kicks in after 3 consecutive failures, surfaced via `knockknock errors` and the daily digest.
- structlog carries `run_id` + `stage` on every line via contextvars; JSON output is grep-able in Cloud Logging.

**Next:** Phase 12 — Cloud deployment (Dockerfiles, Cloud Run Job for pipeline, Cloud Run Service for telegram-bot, Cloud Scheduler hourly trigger, Secret Manager wiring, GitHub Actions with Workload Identity Federation).

← [Index](00-index.md) · [Prev: phase-10-scrapers.md](phase-10-scrapers.md) · [Next: phase-12-deploy.md](phase-12-deploy.md)
