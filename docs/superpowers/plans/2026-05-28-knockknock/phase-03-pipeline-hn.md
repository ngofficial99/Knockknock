← [Index](00-index.md) · [Prev: phase-02-config.md](phase-02-config.md) · [Next: phase-04-prefilter.md](phase-04-prefilter.md)

## Phase 3: Pipeline Skeleton + HN Scraper + Discover Stage

**Outcome:** Running `knockknock pipeline run --once` discovers HN "Who is hiring" comments via the Algolia HN API, parses them into `ScrapedJob` records, upserts the `Company` (canonical by domain) and `JobApplication` (idempotent on `source + source_job_id`), records a `PipelineRun` row, and exits cleanly. Subsequent runs skip duplicates.

### Task 3.1: Stage protocol + pipeline runner skeleton

**Files:**
- Create: `src/knockknock/pipeline/__init__.py`
- Create: `src/knockknock/pipeline/stage.py`
- Create: `src/knockknock/pipeline/runner.py`
- Create: `tests/test_pipeline/__init__.py`
- Create: `tests/test_pipeline/test_runner.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_pipeline/__init__.py` (empty).

Create `tests/test_pipeline/test_runner.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from knockknock.pipeline.runner import PipelineRunner
from knockknock.pipeline.stage import Stage, StageContext, StageResult


@dataclass
class _FakeStage:
    name: str
    counter: list[str]

    def run(self, ctx: StageContext) -> StageResult:
        self.counter.append(self.name)
        return StageResult(stage=self.name, processed=1, advanced=1, rejected=0, errors=0)


def test_runner_executes_stages_in_order() -> None:
    calls: list[str] = []
    stages: list[Stage] = [_FakeStage("discover", calls), _FakeStage("pre_filter", calls)]
    runner = PipelineRunner(stages=stages)
    summary = runner.run_once(StageContext(run_id=1))
    assert calls == ["discover", "pre_filter"]
    assert len(summary.results) == 2
    assert summary.results[0].processed == 1


def test_runner_continues_on_stage_error() -> None:
    @dataclass
    class _BoomStage:
        name: str = "boom"

        def run(self, ctx: StageContext) -> StageResult:
            raise RuntimeError("kaboom")

    calls: list[str] = []
    stages: list[Stage] = [_BoomStage(), _FakeStage("after", calls)]
    runner = PipelineRunner(stages=stages)
    summary = runner.run_once(StageContext(run_id=2))
    assert summary.results[0].errors == 1
    assert "after" in calls
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_pipeline/test_runner.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement stage protocol**

Create `src/knockknock/pipeline/__init__.py` (empty).

Create `src/knockknock/pipeline/stage.py`:

```python
"""Stage protocol and shared context types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class StageContext:
    """Per-run context shared across stages."""

    run_id: int


@dataclass(frozen=True, slots=True)
class StageResult:
    stage: str
    processed: int
    advanced: int
    rejected: int
    errors: int


class Stage(Protocol):
    """A unit of work in the pipeline."""

    name: str

    def run(self, ctx: StageContext) -> StageResult: ...
```

- [ ] **Step 4: Implement runner**

Create `src/knockknock/pipeline/runner.py`:

```python
"""Pipeline orchestrator. Stages run in order, errors are caught per-stage."""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from knockknock.pipeline.stage import Stage, StageContext, StageResult

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PipelineSummary:
    run_id: int
    results: list[StageResult]


@dataclass
class PipelineRunner:
    stages: list[Stage] = field(default_factory=list)

    def run_once(self, ctx: StageContext) -> PipelineSummary:
        """Execute every stage; if one errors, log and continue to the next."""
        results: list[StageResult] = []
        for stage in self.stages:
            try:
                result = stage.run(ctx)
            except Exception as exc:
                log.exception("stage.failed", stage=stage.name, run_id=ctx.run_id, error=str(exc))
                result = StageResult(
                    stage=stage.name, processed=0, advanced=0, rejected=0, errors=1
                )
            results.append(result)
            log.info(
                "stage.completed",
                stage=stage.name,
                processed=result.processed,
                advanced=result.advanced,
                rejected=result.rejected,
                errors=result.errors,
            )
        return PipelineSummary(run_id=ctx.run_id, results=results)
```

- [ ] **Step 5: Run test to verify pass**

```bash
uv run pytest tests/test_pipeline/test_runner.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 6: Commit**

```bash
git add src/knockknock/pipeline tests/test_pipeline
git commit -m "feat(pipeline): add Stage protocol and PipelineRunner"
```

### Task 3.2: Scraper base + ScrapedJob

**Files:**
- Create: `src/knockknock/scrapers/__init__.py`
- Create: `src/knockknock/scrapers/base.py`
- Create: `tests/test_scrapers/__init__.py`
- Create: `tests/test_scrapers/test_base.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_scrapers/__init__.py` (empty).

Create `tests/test_scrapers/test_base.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from knockknock.db.enums import CompanySizeBucket, JobSource
from knockknock.scrapers.base import ScrapedJob


def test_scraped_job_normalises_domain() -> None:
    job = ScrapedJob(
        source=JobSource.HN,
        source_job_id="hn-1",
        company_name="Acme Labs",
        company_domain="HTTPS://Acme.Test/path",
        company_size_bucket=CompanySizeBucket.SERIES_A,
        title="Backend Engineer",
        location="Bengaluru",
        apply_url="https://acme.test/jobs/1",
        description="Build things",
        posted_at=datetime(2026, 5, 28, tzinfo=UTC),
    )
    assert job.company_domain == "acme.test"


def test_scraped_job_requires_apply_url() -> None:
    with pytest.raises(ValueError):
        ScrapedJob(
            source=JobSource.HN,
            source_job_id="x",
            company_name="x",
            company_domain="x.test",
            company_size_bucket=CompanySizeBucket.UNKNOWN,
            title="x",
            location="x",
            apply_url="",
            description="x",
            posted_at=None,
        )
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_scrapers/test_base.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement scraper base**

Create `src/knockknock/scrapers/__init__.py` (empty).

Create `src/knockknock/scrapers/base.py`:

```python
"""Scraper protocol and the dataclass it must produce."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from urllib.parse import urlparse

from knockknock.db.enums import CompanySizeBucket, JobSource


def _normalise_domain(value: str) -> str:
    """Reduce a URL or hostname to a bare lowercase domain."""
    cleaned = value.strip().lower()
    if "://" in cleaned:
        cleaned = urlparse(cleaned).netloc or cleaned
    cleaned = cleaned.split("/")[0]
    if cleaned.startswith("www."):
        cleaned = cleaned[4:]
    return cleaned


@dataclass(frozen=True, slots=True)
class ScrapedJob:
    """Normalised payload that every scraper yields."""

    source: JobSource
    source_job_id: str
    company_name: str
    company_domain: str
    company_size_bucket: CompanySizeBucket
    title: str
    location: str
    apply_url: str
    description: str
    posted_at: datetime | None
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.apply_url:
            raise ValueError("apply_url is required")
        if not self.source_job_id:
            raise ValueError("source_job_id is required")
        object.__setattr__(self, "company_domain", _normalise_domain(self.company_domain))


class Scraper(Protocol):
    """A source-specific scraper."""

    source: JobSource

    def fetch(self) -> Iterator[ScrapedJob]: ...
```

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/test_scrapers/test_base.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/knockknock/scrapers tests/test_scrapers
git commit -m "feat(scrapers): add Scraper protocol and ScrapedJob dataclass"
```

### Task 3.3: HN scraper via Algolia HN Search API

**Files:**
- Create: `src/knockknock/scrapers/hn.py`
- Create: `tests/test_scrapers/test_hn.py`
- Create: `tests/fixtures/hn_thread.json`
- Create: `tests/fixtures/hn_comments.json`

The HN strategy: query Algolia HN Search API
`https://hn.algolia.com/api/v1/search?query=Ask+HN+Who+is+hiring&tags=story&hitsPerPage=10` to find the monthly thread; then page comments via `/api/v1/items/<thread_id>` which returns nested `children`.

- [ ] **Step 1: Write fixtures**

Create `tests/fixtures/hn_thread.json`:

```json
{
  "hits": [
    {
      "objectID": "9000001",
      "title": "Ask HN: Who is hiring? (May 2026)",
      "created_at_i": 1746086400
    },
    {
      "objectID": "8999000",
      "title": "Ask HN: Who is hiring? (April 2026)",
      "created_at_i": 1743408000
    }
  ]
}
```

Create `tests/fixtures/hn_comments.json`:

```json
{
  "id": 9000001,
  "title": "Ask HN: Who is hiring? (May 2026)",
  "children": [
    {
      "id": 9000010,
      "author": "founder1",
      "created_at_i": 1746090000,
      "text": "Acme Labs (acme.test) | Backend Engineer | Bengaluru, ONSITE | Full-time<p>We are hiring backend engineers in Bangalore. Stack: Python, Postgres, Kafka. Apply at <a href=\"https://acme.test/jobs/be\">https://acme.test/jobs/be</a></p>",
      "children": []
    },
    {
      "id": 9000011,
      "author": "founder2",
      "created_at_i": 1746090500,
      "text": "BetaCo | Frontend Engineer | REMOTE (US only)<p>Sorry, US-only. https://beta.test/jobs/fe</p>",
      "children": []
    },
    {
      "id": 9000012,
      "author": "founder3",
      "created_at_i": 1746090700,
      "text": "Just a comment, not a job posting",
      "children": []
    }
  ]
}
```

- [ ] **Step 2: Write failing test**

Create `tests/test_scrapers/test_hn.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from knockknock.db.enums import JobSource
from knockknock.scrapers.hn import HNScraper

FIX = Path(__file__).parent.parent / "fixtures"


@respx.mock
def test_hn_scraper_finds_latest_thread_and_parses_jobs() -> None:
    thread_payload = json.loads((FIX / "hn_thread.json").read_text())
    comments_payload = json.loads((FIX / "hn_comments.json").read_text())

    respx.get("https://hn.algolia.com/api/v1/search").mock(
        return_value=httpx.Response(200, json=thread_payload)
    )
    respx.get("https://hn.algolia.com/api/v1/items/9000001").mock(
        return_value=httpx.Response(200, json=comments_payload)
    )

    scraper = HNScraper(months_lookback=1)
    jobs = list(scraper.fetch())

    sources = {j.source for j in jobs}
    assert sources == {JobSource.HN}
    titles = {j.title for j in jobs}
    assert "Backend Engineer" in titles
    assert any("acme.test" in j.company_domain for j in jobs)


@respx.mock
def test_hn_scraper_skips_non_job_comments() -> None:
    thread_payload = json.loads((FIX / "hn_thread.json").read_text())
    comments_payload = json.loads((FIX / "hn_comments.json").read_text())

    respx.get("https://hn.algolia.com/api/v1/search").mock(
        return_value=httpx.Response(200, json=thread_payload)
    )
    respx.get("https://hn.algolia.com/api/v1/items/9000001").mock(
        return_value=httpx.Response(200, json=comments_payload)
    )

    scraper = HNScraper(months_lookback=1)
    jobs = list(scraper.fetch())
    # Comment 9000012 is a non-job line and should be skipped
    job_ids = {j.source_job_id for j in jobs}
    assert "9000012" not in job_ids


@respx.mock
def test_hn_scraper_handles_5xx_with_retry_then_fails() -> None:
    respx.get("https://hn.algolia.com/api/v1/search").mock(
        return_value=httpx.Response(500)
    )
    scraper = HNScraper(months_lookback=1)
    with pytest.raises(Exception):
        list(scraper.fetch())
```

- [ ] **Step 3: Run test to verify failure**

```bash
uv run pytest tests/test_scrapers/test_hn.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Implement HN scraper**

Create `src/knockknock/scrapers/hn.py`:

```python
"""HN 'Ask HN: Who is hiring?' scraper using Algolia HN Search API."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from selectolax.parser import HTMLParser
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from knockknock.db.enums import CompanySizeBucket, JobSource
from knockknock.exceptions import ExternalServiceError
from knockknock.scrapers.base import ScrapedJob

ALGOLIA = "https://hn.algolia.com/api/v1"
HEADER_SEPARATOR = re.compile(r"\s*\|\s*")
DOMAIN_HINT = re.compile(r"\(([^()\s]+\.[a-z]{2,})\)", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


@dataclass
class HNScraper:
    source: JobSource = JobSource.HN
    months_lookback: int = 1
    _client: httpx.Client | None = None

    def fetch(self) -> Iterator[ScrapedJob]:
        client = self._client or httpx.Client(timeout=20.0, headers={"User-Agent": "knockknock/0.1"})
        try:
            thread_ids = self._find_threads(client)
            for thread_id in thread_ids[: self.months_lookback]:
                yield from self._fetch_thread(client, thread_id)
        finally:
            if self._client is None:
                client.close()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=8),
        reraise=True,
    )
    def _find_threads(self, client: httpx.Client) -> list[str]:
        params = {
            "query": "Ask HN Who is hiring",
            "tags": "story,author_whoishiring",
            "hitsPerPage": 10,
        }
        resp = client.get(f"{ALGOLIA}/search", params=params)
        if resp.status_code >= 500:
            raise httpx.HTTPStatusError("server", request=resp.request, response=resp)
        if resp.status_code >= 400:
            raise ExternalServiceError(f"HN search failed: {resp.status_code}")
        hits = resp.json().get("hits", [])
        return [h["objectID"] for h in hits if "Who is hiring" in (h.get("title") or "")]

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=8),
        reraise=True,
    )
    def _fetch_thread(self, client: httpx.Client, thread_id: str) -> Iterator[ScrapedJob]:
        resp = client.get(f"{ALGOLIA}/items/{thread_id}")
        if resp.status_code >= 400:
            raise ExternalServiceError(f"HN thread {thread_id} failed: {resp.status_code}")
        for comment in resp.json().get("children", []) or []:
            parsed = self._parse_comment(comment)
            if parsed is not None:
                yield parsed

    def _parse_comment(self, comment: dict) -> ScrapedJob | None:
        raw_html = comment.get("text") or ""
        if not raw_html:
            return None
        tree = HTMLParser(raw_html)
        header = tree.text(separator="\n").splitlines()[0] if tree.text() else ""
        parts = [p.strip() for p in HEADER_SEPARATOR.split(header) if p.strip()]
        # A job header conventionally has at least: Company | Role | Location
        if len(parts) < 3:
            return None

        company_part = parts[0]
        domain_match = DOMAIN_HINT.search(company_part)
        if not domain_match:
            return None
        company_name = DOMAIN_HINT.sub("", company_part).strip()
        company_domain = domain_match.group(1)

        title = parts[1]
        location = parts[2]

        body_text = tree.text(separator=" ").strip()
        url_match = URL_RE.search(raw_html)
        if not url_match:
            return None
        apply_url = url_match.group(0)
        posted_at = datetime.fromtimestamp(comment.get("created_at_i", 0), tz=UTC)

        return ScrapedJob(
            source=JobSource.HN,
            source_job_id=str(comment["id"]),
            company_name=company_name or company_domain,
            company_domain=company_domain,
            company_size_bucket=CompanySizeBucket.UNKNOWN,
            title=title,
            location=location,
            apply_url=apply_url,
            description=body_text,
            posted_at=posted_at,
        )
```

- [ ] **Step 5: Run test to verify pass**

```bash
uv run pytest tests/test_scrapers/test_hn.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 6: Commit**

```bash
git add src/knockknock/scrapers/hn.py tests/test_scrapers/test_hn.py tests/fixtures/hn_thread.json tests/fixtures/hn_comments.json
git commit -m "feat(scrapers): add HN scraper via Algolia API"
```

### Task 3.4: Scraper registry

**Files:**
- Create: `src/knockknock/scrapers/registry.py`
- Create: `tests/test_scrapers/test_registry.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_scrapers/test_registry.py`:

```python
from __future__ import annotations

from knockknock.config.preferences import load_preferences
from knockknock.db.enums import JobSource
from knockknock.scrapers.hn import HNScraper
from knockknock.scrapers.registry import build_scrapers


def test_build_scrapers_returns_only_enabled(tmp_path):
    prefs_text = """
candidate: {name: x, current_role: x, years_experience: 1, location: x}
target:
  locations: [x]
  titles_allow: [x]
  titles_deny: []
  seniority_allow: [x]
  company_size_allow: [SEED]
skills: {must_have_any: [x], nice_to_have: []}
scoring:
  min_score_to_draft: 70
  weight_skill_match: 40
  weight_seniority_match: 25
  weight_location_match: 20
  weight_company_stage: 15
limits: {daily_drafts_cap: 1, hourly_discover_cap: 1, gemini_pro_rpd_ceiling: 1}
sources:
  hn: {enabled: true, months_lookback: 1}
  wellfound: {enabled: false, query: ""}
  yc_waas: {enabled: false, query: ""}
  greenhouse: {enabled: false, boards: []}
  lever: {enabled: false, boards: []}
  ashby: {enabled: false, boards: []}
"""
    f = tmp_path / "p.yaml"
    f.write_text(prefs_text)
    prefs = load_preferences(f)
    scrapers = build_scrapers(prefs)
    assert len(scrapers) == 1
    assert scrapers[0].source == JobSource.HN
    assert isinstance(scrapers[0], HNScraper)
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_scrapers/test_registry.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement registry**

Create `src/knockknock/scrapers/registry.py`:

```python
"""Map enabled sources from preferences to scraper instances."""

from __future__ import annotations

from knockknock.config.preferences import JobPreferences
from knockknock.scrapers.base import Scraper
from knockknock.scrapers.hn import HNScraper


def build_scrapers(prefs: JobPreferences) -> list[Scraper]:
    """Return scrapers in the order Phase 3 supports them. Others added in Phase 10."""
    scrapers: list[Scraper] = []
    if prefs.sources.hn.enabled:
        scrapers.append(HNScraper(months_lookback=prefs.sources.hn.months_lookback))
    return scrapers
```

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/test_scrapers/test_registry.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/knockknock/scrapers/registry.py tests/test_scrapers/test_registry.py
git commit -m "feat(scrapers): add registry mapping enabled sources"
```

### Task 3.5: Discover stage — upsert Company + JobApplication

**Files:**
- Create: `src/knockknock/pipeline/discover.py`
- Create: `tests/test_pipeline/test_discover.py`

- [ ] **Step 1: Write failing test (integration, uses Neon)**

Create `tests/test_pipeline/test_discover.py`:

```python
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from sqlmodel import Session, select

from knockknock.db.enums import CompanySizeBucket, JobSource, JobStatus
from knockknock.db.models import Company, JobApplication
from knockknock.pipeline.discover import DiscoverStage
from knockknock.pipeline.stage import StageContext
from knockknock.scrapers.base import ScrapedJob


class _StaticScraper:
    source = JobSource.HN

    def __init__(self, jobs: list[ScrapedJob]) -> None:
        self._jobs = jobs

    def fetch(self) -> Iterator[ScrapedJob]:
        yield from self._jobs


def _make_job(source_id: str) -> ScrapedJob:
    return ScrapedJob(
        source=JobSource.HN,
        source_job_id=source_id,
        company_name="Acme",
        company_domain="acme.test",
        company_size_bucket=CompanySizeBucket.UNKNOWN,
        title="Backend Engineer",
        location="Bengaluru",
        apply_url=f"https://acme.test/jobs/{source_id}",
        description="Build things",
        posted_at=datetime(2026, 5, 28, tzinfo=UTC),
    )


def test_discover_upserts_company_once(db_session: Session) -> None:
    scrapers = [_StaticScraper([_make_job("hn-a"), _make_job("hn-b")])]
    stage = DiscoverStage(session=db_session, scrapers=scrapers, hourly_cap=50)
    result = stage.run(StageContext(run_id=1))
    assert result.processed == 2
    companies = db_session.exec(select(Company).where(Company.domain == "acme.test")).all()
    assert len(companies) == 1
    jobs = db_session.exec(select(JobApplication)).all()
    assert {j.source_job_id for j in jobs} == {"hn-a", "hn-b"}
    assert {j.status for j in jobs} == {JobStatus.DISCOVERED}


def test_discover_is_idempotent(db_session: Session) -> None:
    scrapers = [_StaticScraper([_make_job("hn-x")])]
    DiscoverStage(session=db_session, scrapers=scrapers, hourly_cap=50).run(StageContext(run_id=1))
    # Second run inserts no duplicates
    DiscoverStage(session=db_session, scrapers=scrapers, hourly_cap=50).run(StageContext(run_id=2))
    jobs = db_session.exec(select(JobApplication).where(JobApplication.source_job_id == "hn-x")).all()
    assert len(jobs) == 1


def test_discover_respects_hourly_cap(db_session: Session) -> None:
    jobs_in = [_make_job(f"hn-{i}") for i in range(10)]
    scrapers = [_StaticScraper(jobs_in)]
    stage = DiscoverStage(session=db_session, scrapers=scrapers, hourly_cap=3)
    result = stage.run(StageContext(run_id=1))
    assert result.processed == 3
```

- [ ] **Step 2: Run test to verify failure**

```bash
export KNOCKKNOCK_TEST_DATABASE_URL="$KNOCKKNOCK_DATABASE_URL"
uv run pytest tests/test_pipeline/test_discover.py -v
```

Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement discover stage**

Create `src/knockknock/pipeline/discover.py`:

```python
"""Discover stage: pull jobs from each enabled scraper and upsert into the DB."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

import structlog
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, select

from knockknock.db.enums import JobStatus
from knockknock.db.models import Company, JobApplication
from knockknock.pipeline.stage import StageContext, StageResult
from knockknock.scrapers.base import Scraper, ScrapedJob

log = structlog.get_logger(__name__)


@dataclass
class DiscoverStage:
    """Pulls jobs from scrapers and upserts Company + JobApplication idempotently."""

    session: Session
    scrapers: list[Scraper]
    hourly_cap: int
    name: str = field(default="discover", init=False)

    def run(self, ctx: StageContext) -> StageResult:
        processed = 0
        rejected = 0
        errors = 0
        for scraper in self.scrapers:
            try:
                for job in scraper.fetch():
                    if processed >= self.hourly_cap:
                        log.info("discover.cap_reached", cap=self.hourly_cap)
                        break
                    if self._upsert(job):
                        processed += 1
                    else:
                        rejected += 1
                if processed >= self.hourly_cap:
                    break
            except Exception as exc:
                log.exception("discover.scraper_failed", source=scraper.source, error=str(exc))
                errors += 1
        self.session.commit()
        return StageResult(
            stage=self.name, processed=processed, advanced=processed,
            rejected=rejected, errors=errors,
        )

    def _upsert(self, job: ScrapedJob) -> bool:
        """Return True if a new JobApplication row was inserted."""
        company_id = self._get_or_create_company(job)
        stmt = (
            insert(JobApplication.__table__)
            .values(
                company_id=company_id,
                source=job.source,
                source_job_id=job.source_job_id,
                title=job.title,
                location=job.location,
                apply_url=job.apply_url,
                description=job.description,
                posted_at=job.posted_at,
                status=JobStatus.DISCOVERED,
            )
            .on_conflict_do_nothing(constraint="jobs_source_unique")
            .returning(JobApplication.__table__.c.id)
        )
        result = self.session.exec(stmt).first()  # type: ignore[arg-type]
        return result is not None

    def _get_or_create_company(self, job: ScrapedJob) -> int:
        existing = self.session.exec(
            select(Company).where(Company.domain == job.company_domain)
        ).first()
        if existing is not None and existing.id is not None:
            return existing.id
        row = Company(
            name=job.company_name,
            domain=job.company_domain,
            size_bucket=job.company_size_bucket,
        )
        self.session.add(row)
        self.session.flush()
        assert row.id is not None
        return row.id
```

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/test_pipeline/test_discover.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/knockknock/pipeline/discover.py tests/test_pipeline/test_discover.py
git commit -m "feat(pipeline): add idempotent discover stage with hourly cap"
```

### Task 3.6: PipelineRun bookkeeping

**Files:**
- Create: `src/knockknock/observability/__init__.py`
- Create: `src/knockknock/observability/metrics.py`
- Create: `tests/test_pipeline/test_metrics.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_pipeline/test_metrics.py`:

```python
from __future__ import annotations

from sqlmodel import Session, select

from knockknock.db.enums import PipelineRunStatus
from knockknock.db.models import PipelineRun
from knockknock.observability.metrics import begin_run, finalize_run
from knockknock.pipeline.stage import StageResult


def test_begin_and_finalize_run(db_session: Session) -> None:
    run_id = begin_run(db_session)
    results = [
        StageResult(stage="discover", processed=5, advanced=5, rejected=0, errors=0),
    ]
    finalize_run(db_session, run_id, results)
    db_session.flush()
    row = db_session.exec(select(PipelineRun).where(PipelineRun.id == run_id)).one()
    assert row.status == PipelineRunStatus.SUCCESS
    assert row.discovered_count == 5
    assert row.finished_at is not None


def test_finalize_run_marks_failed_when_all_errors(db_session: Session) -> None:
    run_id = begin_run(db_session)
    results = [StageResult(stage="discover", processed=0, advanced=0, rejected=0, errors=1)]
    finalize_run(db_session, run_id, results)
    db_session.flush()
    row = db_session.exec(select(PipelineRun).where(PipelineRun.id == run_id)).one()
    assert row.status == PipelineRunStatus.FAILED
    assert row.error_count == 1
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_pipeline/test_metrics.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement metrics**

Create `src/knockknock/observability/__init__.py` (empty).

Create `src/knockknock/observability/metrics.py`:

```python
"""PipelineRun bookkeeping: open a row at start, close it with counts at end."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session

from knockknock.db.enums import PipelineRunStatus
from knockknock.db.models import PipelineRun
from knockknock.pipeline.stage import StageResult


def begin_run(session: Session) -> int:
    row = PipelineRun(status=PipelineRunStatus.RUNNING)
    session.add(row)
    session.flush()
    assert row.id is not None
    return row.id


def finalize_run(session: Session, run_id: int, results: list[StageResult]) -> None:
    counts = {
        "discover": 0,
        "pre_filter": 0,
        "score": 0,
        "draft": 0,
    }
    errors = sum(r.errors for r in results)
    advanced_total = sum(r.advanced for r in results)
    for r in results:
        if r.stage in counts:
            counts[r.stage] = r.advanced

    if errors > 0 and advanced_total == 0:
        status = PipelineRunStatus.FAILED
    elif errors > 0:
        status = PipelineRunStatus.PARTIAL
    else:
        status = PipelineRunStatus.SUCCESS

    row = session.get(PipelineRun, run_id)
    if row is None:
        return
    row.finished_at = datetime.now(UTC)
    row.status = status
    row.discovered_count = counts["discover"]
    row.pre_filtered_count = counts["pre_filter"]
    row.scored_count = counts["score"]
    row.drafted_count = counts["draft"]
    row.error_count = errors
    row.summary = {r.stage: r.__dict__ for r in results}
    session.add(row)
    session.flush()
```

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/test_pipeline/test_metrics.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/knockknock/observability tests/test_pipeline/test_metrics.py
git commit -m "feat(observability): add begin_run/finalize_run for PipelineRun"
```

### Task 3.7: CLI command `pipeline run --once` (Phase-3 wiring)

**Files:**
- Modify: `src/knockknock/__main__.py`
- Create: `tests/test_pipeline/test_cli_run_once.py`

- [ ] **Step 1: Write failing CLI smoke test**

Create `tests/test_pipeline/test_cli_run_once.py`:

```python
from __future__ import annotations

from typer.testing import CliRunner

from knockknock.__main__ import app

runner = CliRunner()


def test_pipeline_run_once_help() -> None:
    result = runner.invoke(app, ["pipeline", "run", "--help"])
    assert result.exit_code == 0
    assert "--once" in result.stdout
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_pipeline/test_cli_run_once.py -v
```

Expected: FAIL (no `pipeline` command yet).

- [ ] **Step 3: Wire `pipeline run` into `__main__`**

Replace `src/knockknock/__main__.py`:

```python
"""CLI entrypoint."""

from __future__ import annotations

from pathlib import Path

import typer

from knockknock import __version__

app = typer.Typer(help="Knockknock CLI")
pipeline_app = typer.Typer(help="Pipeline commands")
app.add_typer(pipeline_app, name="pipeline")


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@pipeline_app.command("run")
def pipeline_run(
    once: bool = typer.Option(False, "--once", help="Run a single pipeline pass and exit."),
) -> None:
    """Run the discovery/score/draft pipeline."""
    from knockknock.config.preferences import load_preferences
    from knockknock.config.settings import Settings
    from knockknock.db.engine import make_sync_engine
    from knockknock.db.session import session_scope
    from knockknock.logging import configure_logging
    from knockknock.observability.metrics import begin_run, finalize_run
    from knockknock.pipeline.discover import DiscoverStage
    from knockknock.pipeline.runner import PipelineRunner
    from knockknock.pipeline.stage import StageContext
    from knockknock.scrapers.registry import build_scrapers

    settings = Settings()
    configure_logging(level=settings.log_level, json=settings.runtime == "cloud")
    prefs = load_preferences(Path(settings.preferences_path))
    engine = make_sync_engine(settings.database_url)

    with session_scope(engine) as session:
        run_id = begin_run(session)
        scrapers = build_scrapers(prefs)
        stages = [
            DiscoverStage(
                session=session,
                scrapers=scrapers,
                hourly_cap=prefs.limits.hourly_discover_cap,
            ),
        ]
        summary = PipelineRunner(stages=stages).run_once(StageContext(run_id=run_id))
        finalize_run(session, run_id, summary.results)

    if not once:
        typer.echo("non-once mode not implemented yet; phase 12 will add scheduler integration")


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/test_pipeline/test_cli_run_once.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 5: End-to-end manual smoke run against Neon**

```bash
export $(grep -v '^#' .env | xargs)
uv run knockknock pipeline run --once
```

Expected: completes without error; check Neon SQL editor:

```sql
SELECT id, source, source_job_id, title, status FROM job_applications ORDER BY id DESC LIMIT 10;
SELECT id, status, discovered_count, error_count, started_at, finished_at FROM pipeline_runs ORDER BY id DESC LIMIT 5;
```

Expected: at least one `job_applications` row with `status='DISCOVERED'` and one `pipeline_runs` row with `status='SUCCESS'`.

- [ ] **Step 6: Re-run to verify idempotency**

```bash
uv run knockknock pipeline run --once
```

Expected: no new `job_applications` rows added (same `source_job_id` rejected by unique constraint); a new `pipeline_runs` row appears.

- [ ] **Step 7: Commit**

```bash
git add src/knockknock/__main__.py tests/test_pipeline/test_cli_run_once.py
git commit -m "feat(cli): add 'pipeline run --once' command wiring Phase-3 stages"
```

---

← [Index](00-index.md) · [Prev: phase-02-config.md](phase-02-config.md) · [Next: phase-04-prefilter.md](phase-04-prefilter.md)
