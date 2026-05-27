← [Index](00-index.md) · [Prev: phase-09-telegram.md](phase-09-telegram.md) · [Next: phase-11-cli-digest.md](phase-11-cli-digest.md)

## Phase 10: Remaining Scrapers (Wellfound, YC WaaS, Greenhouse, Lever, Ashby)

**Outcome:** All six sources in the `job_source` enum produce real `ScrapedJob` records. The three ATS scrapers (Greenhouse, Lever, Ashby) run from JSON REST APIs seeded by per-source YAML company lists. The two Playwright scrapers (Wellfound, YC WaaS) run headless against recorded fixtures in CI and against the live sites under `pytest -m live` only. `build_scrapers()` returns the full set when preferences enable them. Running `knockknock pipeline run --once --skip-after discover` ingests jobs from every enabled source.

### Prerequisites

- Phase 3 complete (`Scraper` Protocol, `ScrapedJob`, `build_scrapers()`, HN scraper, discover stage).
- Phase 2 `JobPreferences` already declares `wellfound`, `yc_waas`, `greenhouse`, `lever`, `ashby` sub-models.
- `playwright` is in the deps from Phase 0; `uv run playwright install chromium` must succeed locally before live tests.

> **Note on `JobPreferences` source sub-models:** Phase 3 used minimal stubs (e.g. `greenhouse: {enabled, boards: []}`). This phase expands them: ATS scrapers read a **company seed list YAML path** (per the design spec, §8) rather than an inline `boards` list. If Phase 3's `GreenhouseSource` doesn't already expose a `company_seed_list_path: Path | None`, extend it in Task 10.1.

> **Note on seed YAMLs vs preferences:** the design spec lists `company_seed_list_path` as the wiring; the YAMLs live under `config/seed_companies_<source>.yaml`. They're committed to the repo (no secrets) and loaded fresh on each pipeline run.

### Task 10.1: Expand `JobPreferences` source sub-models

**Files:**
- Modify: `src/knockknock/config/preferences.py`
- Modify: `tests/test_config/test_preferences.py`
- Create: `config/seed_companies_greenhouse.yaml`
- Create: `config/seed_companies_lever.yaml`
- Create: `config/seed_companies_ashby.yaml`

- [ ] **Step 1: Write failing test**

Append to `tests/test_config/test_preferences.py`:

```python
def test_ats_sources_carry_seed_paths(tmp_path) -> None:
    seed = tmp_path / "gh.yaml"
    seed.write_text("companies: [{slug: stripe}]\n")
    prefs_text = f"""
candidate: {{name: x, current_role: x, years_experience: 1, location: x}}
target:
  locations: [x]
  titles_allow: [x]
  titles_deny: []
  seniority_allow: [x]
  company_size_allow: [SEED]
skills: {{must_have_any: [x], nice_to_have: []}}
scoring:
  min_score_to_draft: 70
  weight_skill_match: 40
  weight_seniority_match: 25
  weight_location_match: 20
  weight_company_stage: 15
limits: {{daily_drafts_cap: 1, hourly_discover_cap: 1, gemini_pro_rpd_ceiling: 1}}
sources:
  hn: {{enabled: false, months_lookback: 1}}
  wellfound: {{enabled: true, location: Bangalore, role_types: [engineering], remote: true}}
  yc_waas: {{enabled: true, location: India, role: engineer}}
  greenhouse: {{enabled: true, company_seed_list_path: "{seed}"}}
  lever: {{enabled: false, company_seed_list_path: null}}
  ashby: {{enabled: false, company_seed_list_path: null}}
"""
    f = tmp_path / "p.yaml"
    f.write_text(prefs_text)
    from knockknock.config.preferences import load_preferences

    prefs = load_preferences(f)
    assert prefs.sources.greenhouse.enabled is True
    assert prefs.sources.greenhouse.company_seed_list_path == seed
    assert prefs.sources.wellfound.location == "Bangalore"
    assert prefs.sources.wellfound.role_types == ["engineering"]
    assert prefs.sources.yc_waas.role == "engineer"
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_config/test_preferences.py::test_ats_sources_carry_seed_paths -v
```

Expected: FAIL (fields don't exist yet).

- [ ] **Step 3: Implement model expansion**

In `src/knockknock/config/preferences.py`, replace the source sub-models with:

```python
class WellfoundSource(BaseModel):
    enabled: bool = False
    location: str = "Bangalore"
    role_types: list[str] = Field(default_factory=lambda: ["engineering"])
    remote: bool = True


class YcWaasSource(BaseModel):
    enabled: bool = False
    location: str = "India"
    role: str = "engineer"


class HnSource(BaseModel):
    enabled: bool = False
    months_lookback: int = 1


class _AtsSource(BaseModel):
    enabled: bool = False
    company_seed_list_path: Path | None = None

    @model_validator(mode="after")
    def _require_seed_when_enabled(self) -> "_AtsSource":
        if self.enabled and self.company_seed_list_path is None:
            raise ValueError("company_seed_list_path is required when ATS source is enabled")
        return self


class GreenhouseSource(_AtsSource): ...
class LeverSource(_AtsSource): ...
class AshbySource(_AtsSource): ...


class Sources(BaseModel):
    hn: HnSource = HnSource()
    wellfound: WellfoundSource = WellfoundSource()
    yc_waas: YcWaasSource = YcWaasSource()
    greenhouse: GreenhouseSource = GreenhouseSource()
    lever: LeverSource = LeverSource()
    ashby: AshbySource = AshbySource()
```

Import `Path` from `pathlib` and `model_validator` from `pydantic` if not already present.

- [ ] **Step 4: Add seed YAMLs**

Create `config/seed_companies_greenhouse.yaml`:

```yaml
# Greenhouse-hosted boards. `slug` is the segment in
# https://boards-api.greenhouse.io/v1/boards/<slug>/jobs
companies:
  - slug: razorpay
    name: Razorpay
  - slug: cred
    name: CRED
  - slug: meesho
    name: Meesho
```

Create `config/seed_companies_lever.yaml`:

```yaml
# Lever-hosted boards. `slug` is the segment in
# https://api.lever.co/v0/postings/<slug>?mode=json
companies:
  - slug: groww
    name: Groww
  - slug: spinny
    name: Spinny
```

Create `config/seed_companies_ashby.yaml`:

```yaml
# Ashby-hosted boards. `slug` is the org-token from
# https://api.ashbyhq.com/posting-api/job-board/<slug>
companies:
  - slug: postman
    name: Postman
  - slug: hasura
    name: Hasura
```

- [ ] **Step 5: Run test to verify pass**

```bash
uv run pytest tests/test_config/test_preferences.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 6: Commit**

```bash
git add src/knockknock/config/preferences.py tests/test_config/test_preferences.py config/seed_companies_*.yaml
git commit -m "feat(config): expand source sub-models for ATS + Playwright scrapers, add seed YAMLs"
```

### Task 10.2: Shared ATS seed loader

**Files:**
- Create: `src/knockknock/scrapers/ats_seed.py`
- Create: `tests/test_scrapers/test_ats_seed.py`

A tiny utility shared by all three ATS scrapers. Reads the YAML, validates structure, returns a list of (`slug`, `name`) tuples.

- [ ] **Step 1: Write failing test**

Create `tests/test_scrapers/test_ats_seed.py`:

```python
from __future__ import annotations

import pytest

from knockknock.scrapers.ats_seed import AtsSeedCompany, load_ats_seed


def test_load_ats_seed_returns_companies(tmp_path) -> None:
    p = tmp_path / "seed.yaml"
    p.write_text(
        """
companies:
  - slug: razorpay
    name: Razorpay
  - slug: cred
    name: CRED
"""
    )
    seeds = load_ats_seed(p)
    assert seeds == [
        AtsSeedCompany(slug="razorpay", name="Razorpay"),
        AtsSeedCompany(slug="cred", name="CRED"),
    ]


def test_load_ats_seed_rejects_empty(tmp_path) -> None:
    p = tmp_path / "seed.yaml"
    p.write_text("companies: []\n")
    with pytest.raises(ValueError, match="at least one company"):
        load_ats_seed(p)


def test_load_ats_seed_rejects_duplicate_slug(tmp_path) -> None:
    p = tmp_path / "seed.yaml"
    p.write_text(
        """
companies:
  - {slug: a, name: A}
  - {slug: a, name: A2}
"""
    )
    with pytest.raises(ValueError, match="duplicate slug"):
        load_ats_seed(p)
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_scrapers/test_ats_seed.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement loader**

Create `src/knockknock/scrapers/ats_seed.py`:

```python
"""Loader for ATS seed-company YAML files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class AtsSeedCompany:
    slug: str
    name: str


def load_ats_seed(path: Path) -> list[AtsSeedCompany]:
    """Parse a `config/seed_companies_*.yaml` file and validate it."""
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict) or "companies" not in raw:
        raise ValueError(f"{path}: missing top-level 'companies' key")
    companies_raw = raw["companies"]
    if not isinstance(companies_raw, list) or not companies_raw:
        raise ValueError(f"{path}: 'companies' must list at least one company")
    seen: set[str] = set()
    out: list[AtsSeedCompany] = []
    for entry in companies_raw:
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: company entries must be mappings, got {entry!r}")
        slug = str(entry.get("slug", "")).strip()
        name = str(entry.get("name", "")).strip()
        if not slug or not name:
            raise ValueError(f"{path}: each company needs slug + name, got {entry!r}")
        if slug in seen:
            raise ValueError(f"{path}: duplicate slug {slug!r}")
        seen.add(slug)
        out.append(AtsSeedCompany(slug=slug, name=name))
    return out
```

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/test_scrapers/test_ats_seed.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/knockknock/scrapers/ats_seed.py tests/test_scrapers/test_ats_seed.py
git commit -m "feat(scrapers): add shared ATS seed-company YAML loader"
```

### Task 10.3: Greenhouse scraper

**Files:**
- Create: `src/knockknock/scrapers/greenhouse.py`
- Create: `tests/test_scrapers/test_greenhouse.py`
- Create: `tests/fixtures/greenhouse_razorpay_jobs.json`

Greenhouse's public board API is unauthenticated: `GET https://boards-api.greenhouse.io/v1/boards/<slug>/jobs?content=true` returns a list with `title`, `location.name`, `absolute_url`, `content` (HTML), and `updated_at` for each job. The scraper iterates seed companies, calls the endpoint, parses the HTML body to text via selectolax, and yields one `ScrapedJob` per posting that passes a Bangalore/Bengaluru/Remote-India location filter.

- [ ] **Step 1: Save real-shape fixture**

Create `tests/fixtures/greenhouse_razorpay_jobs.json`:

```json
{
  "jobs": [
    {
      "id": 4567890,
      "title": "Senior Backend Engineer",
      "updated_at": "2026-05-25T11:30:00.000Z",
      "location": {"name": "Bengaluru, India"},
      "absolute_url": "https://boards.greenhouse.io/razorpay/jobs/4567890",
      "content": "&lt;p&gt;Build the &lt;b&gt;payment&lt;/b&gt; rails for India.&lt;/p&gt;&lt;p&gt;Stack: Go, Postgres, Kafka.&lt;/p&gt;",
      "metadata": [],
      "departments": [{"name": "Engineering"}]
    },
    {
      "id": 4567891,
      "title": "Frontend Engineer",
      "updated_at": "2026-05-26T08:00:00.000Z",
      "location": {"name": "San Francisco, CA"},
      "absolute_url": "https://boards.greenhouse.io/razorpay/jobs/4567891",
      "content": "&lt;p&gt;React + TS in SF.&lt;/p&gt;",
      "metadata": []
    },
    {
      "id": 4567892,
      "title": "Site Reliability Engineer",
      "updated_at": "2026-05-26T09:00:00.000Z",
      "location": {"name": "Remote — India"},
      "absolute_url": "https://boards.greenhouse.io/razorpay/jobs/4567892",
      "content": "&lt;p&gt;Run our k8s clusters from home.&lt;/p&gt;",
      "metadata": []
    }
  ]
}
```

- [ ] **Step 2: Write failing test**

Create `tests/test_scrapers/test_greenhouse.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from knockknock.db.enums import CompanySizeBucket, JobSource
from knockknock.scrapers.ats_seed import AtsSeedCompany
from knockknock.scrapers.greenhouse import GreenhouseScraper


FIXTURE = Path(__file__).parent.parent / "fixtures" / "greenhouse_razorpay_jobs.json"


@respx.mock
def test_greenhouse_scraper_filters_to_india_and_remote() -> None:
    payload = json.loads(FIXTURE.read_text())
    respx.get(
        "https://boards-api.greenhouse.io/v1/boards/razorpay/jobs?content=true"
    ).mock(return_value=httpx.Response(200, json=payload))

    client = httpx.Client(timeout=10)
    scraper = GreenhouseScraper(
        seeds=[AtsSeedCompany(slug="razorpay", name="Razorpay")],
        http=client,
    )
    jobs = list(scraper.scrape())

    assert len(jobs) == 2
    bangalore = next(j for j in jobs if "Bengaluru" in j.location)
    assert bangalore.source == JobSource.GREENHOUSE
    assert bangalore.source_job_id == "razorpay:4567890"
    assert bangalore.company_name == "Razorpay"
    assert bangalore.company_domain == "razorpay.com"
    assert bangalore.title == "Senior Backend Engineer"
    assert "payment rails for India" in bangalore.description
    assert bangalore.apply_url == "https://boards.greenhouse.io/razorpay/jobs/4567890"
    assert bangalore.company_size_bucket == CompanySizeBucket.UNKNOWN


@respx.mock
def test_greenhouse_scraper_skips_company_on_http_error() -> None:
    respx.get(
        "https://boards-api.greenhouse.io/v1/boards/broken/jobs?content=true"
    ).mock(return_value=httpx.Response(503))
    respx.get(
        "https://boards-api.greenhouse.io/v1/boards/razorpay/jobs?content=true"
    ).mock(return_value=httpx.Response(200, json={"jobs": []}))

    client = httpx.Client(timeout=5)
    scraper = GreenhouseScraper(
        seeds=[
            AtsSeedCompany(slug="broken", name="Broken"),
            AtsSeedCompany(slug="razorpay", name="Razorpay"),
        ],
        http=client,
    )

    jobs = list(scraper.scrape())  # 5xx is retried then swallowed; pipeline continues
    assert jobs == []
```

- [ ] **Step 3: Run test to verify failure**

```bash
uv run pytest tests/test_scrapers/test_greenhouse.py -v
```

Expected: FAIL.

- [ ] **Step 4: Implement scraper**

Create `src/knockknock/scrapers/greenhouse.py`:

```python
"""Greenhouse public-board scraper.

Endpoint: GET https://boards-api.greenhouse.io/v1/boards/<slug>/jobs?content=true
Auth: none.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime
from typing import Final

import httpx
import structlog
from selectolax.parser import HTMLParser
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from knockknock.db.enums import CompanySizeBucket, JobSource
from knockknock.scrapers.ats_seed import AtsSeedCompany
from knockknock.scrapers.base import ScrapedJob

log = structlog.get_logger(__name__)

_BASE: Final = "https://boards-api.greenhouse.io/v1/boards"
_INDIA_RE: Final = re.compile(r"(bangalore|bengaluru|india|remote)", re.IGNORECASE)


def _html_to_text(html: str) -> str:
    """Render the job's HTML description to plain text."""
    if not html:
        return ""
    parser = HTMLParser(html)
    text = parser.text(separator="\n", strip=True)
    # Collapse runs of blank lines.
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _parse_updated_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # Greenhouse returns trailing 'Z'; fromisoformat handles offsets in 3.11+
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class GreenhouseScraper:
    """Yields one ScrapedJob per India/Remote posting per seed company."""

    source = JobSource.GREENHOUSE
    name = "greenhouse"

    def __init__(
        self,
        seeds: list[AtsSeedCompany],
        http: httpx.Client,
        *,
        location_filter: re.Pattern[str] = _INDIA_RE,
    ) -> None:
        self._seeds = seeds
        self._http = http
        self._location_filter = location_filter

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    def _fetch(self, slug: str) -> dict:
        url = f"{_BASE}/{slug}/jobs?content=true"
        resp = self._http.get(url)
        resp.raise_for_status()
        return resp.json()

    def scrape(self) -> Iterator[ScrapedJob]:
        for seed in self._seeds:
            try:
                payload = self._fetch(seed.slug)
            except httpx.HTTPError as exc:
                log.warning("greenhouse.fetch_failed", slug=seed.slug, error=str(exc))
                continue
            jobs_raw = payload.get("jobs", []) if isinstance(payload, dict) else []
            for raw in jobs_raw:
                yielded = self._maybe_build(seed, raw)
                if yielded is not None:
                    yield yielded

    def _maybe_build(self, seed: AtsSeedCompany, raw: dict) -> ScrapedJob | None:
        location = (raw.get("location") or {}).get("name") or ""
        if not self._location_filter.search(location):
            return None
        title = (raw.get("title") or "").strip()
        if not title:
            return None
        absolute_url = raw.get("absolute_url") or ""
        if not absolute_url:
            return None
        job_id = raw.get("id")
        if job_id is None:
            return None
        try:
            return ScrapedJob(
                source=JobSource.GREENHOUSE,
                source_job_id=f"{seed.slug}:{job_id}",
                company_name=seed.name,
                company_domain=f"{seed.slug}.com",
                company_size_bucket=CompanySizeBucket.UNKNOWN,
                title=title,
                location=location,
                apply_url=absolute_url,
                description=_html_to_text(raw.get("content") or ""),
                posted_at=_parse_updated_at(raw.get("updated_at")),
            )
        except ValueError as exc:
            log.warning(
                "greenhouse.invalid_job", slug=seed.slug, job_id=job_id, error=str(exc)
            )
            return None
```

> **Note on `company_domain`:** Greenhouse doesn't return the company's marketing domain, so we use `<slug>.com` as a best-effort placeholder. Phonebook resolution (Phase 6) will replace this with an authoritative domain via Apollo/Hunter when possible; the `companies` row's `domain` column is the canonical store.

- [ ] **Step 5: Run test to verify pass**

```bash
uv run pytest tests/test_scrapers/test_greenhouse.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 6: Commit**

```bash
git add src/knockknock/scrapers/greenhouse.py tests/test_scrapers/test_greenhouse.py tests/fixtures/greenhouse_razorpay_jobs.json
git commit -m "feat(scrapers): add Greenhouse public-board scraper"
```

### Task 10.4: Lever scraper

**Files:**
- Create: `src/knockknock/scrapers/lever.py`
- Create: `tests/test_scrapers/test_lever.py`
- Create: `tests/fixtures/lever_groww_postings.json`

Lever's public postings API: `GET https://api.lever.co/v0/postings/<slug>?mode=json` returns a flat list. Each posting has `id`, `text` (title), `categories.location`, `categories.commitment`, `categories.team`, `hostedUrl`, `descriptionPlain`, `createdAt` (ms epoch).

- [ ] **Step 1: Save real-shape fixture**

Create `tests/fixtures/lever_groww_postings.json`:

```json
[
  {
    "id": "abc-123",
    "text": "Backend Engineer (Distributed Systems)",
    "categories": {
      "location": "Bengaluru",
      "commitment": "Full-time",
      "team": "Engineering"
    },
    "hostedUrl": "https://jobs.lever.co/groww/abc-123",
    "descriptionPlain": "Own our trading engine. Go + Postgres.",
    "createdAt": 1748390400000
  },
  {
    "id": "def-456",
    "text": "Frontend Engineer",
    "categories": {
      "location": "New York",
      "commitment": "Full-time",
      "team": "Engineering"
    },
    "hostedUrl": "https://jobs.lever.co/groww/def-456",
    "descriptionPlain": "React.",
    "createdAt": 1748476800000
  }
]
```

- [ ] **Step 2: Write failing test**

Create `tests/test_scrapers/test_lever.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from knockknock.db.enums import JobSource
from knockknock.scrapers.ats_seed import AtsSeedCompany
from knockknock.scrapers.lever import LeverScraper


FIXTURE = Path(__file__).parent.parent / "fixtures" / "lever_groww_postings.json"


@respx.mock
def test_lever_scraper_yields_only_india_postings() -> None:
    payload = json.loads(FIXTURE.read_text())
    respx.get("https://api.lever.co/v0/postings/groww?mode=json").mock(
        return_value=httpx.Response(200, json=payload)
    )

    scraper = LeverScraper(
        seeds=[AtsSeedCompany(slug="groww", name="Groww")],
        http=httpx.Client(timeout=10),
    )
    jobs = list(scraper.scrape())

    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == JobSource.LEVER
    assert job.source_job_id == "groww:abc-123"
    assert job.title == "Backend Engineer (Distributed Systems)"
    assert job.location == "Bengaluru"
    assert job.apply_url == "https://jobs.lever.co/groww/abc-123"
    assert "trading engine" in job.description
    assert job.posted_at is not None
    assert job.posted_at.year == 2025  # 1748390400000 ms = 2025-05-28 UTC
```

- [ ] **Step 3: Run test to verify failure**

```bash
uv run pytest tests/test_scrapers/test_lever.py -v
```

Expected: FAIL.

- [ ] **Step 4: Implement scraper**

Create `src/knockknock/scrapers/lever.py`:

```python
"""Lever public-postings scraper.

Endpoint: GET https://api.lever.co/v0/postings/<slug>?mode=json
Auth: none.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Final

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from knockknock.db.enums import CompanySizeBucket, JobSource
from knockknock.scrapers.ats_seed import AtsSeedCompany
from knockknock.scrapers.base import ScrapedJob

log = structlog.get_logger(__name__)

_BASE: Final = "https://api.lever.co/v0/postings"
_INDIA_RE: Final = re.compile(r"(bangalore|bengaluru|india|remote)", re.IGNORECASE)


def _ms_to_datetime(ms: int | None) -> datetime | None:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=UTC)
    except (OSError, ValueError, OverflowError):
        return None


class LeverScraper:
    source = JobSource.LEVER
    name = "lever"

    def __init__(
        self,
        seeds: list[AtsSeedCompany],
        http: httpx.Client,
        *,
        location_filter: re.Pattern[str] = _INDIA_RE,
    ) -> None:
        self._seeds = seeds
        self._http = http
        self._location_filter = location_filter

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    def _fetch(self, slug: str) -> list[dict]:
        resp = self._http.get(f"{_BASE}/{slug}?mode=json")
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    def scrape(self) -> Iterator[ScrapedJob]:
        for seed in self._seeds:
            try:
                postings = self._fetch(seed.slug)
            except httpx.HTTPError as exc:
                log.warning("lever.fetch_failed", slug=seed.slug, error=str(exc))
                continue
            for raw in postings:
                job = self._maybe_build(seed, raw)
                if job is not None:
                    yield job

    def _maybe_build(self, seed: AtsSeedCompany, raw: dict) -> ScrapedJob | None:
        categories = raw.get("categories") or {}
        location = (categories.get("location") or "").strip()
        if not self._location_filter.search(location):
            return None
        title = (raw.get("text") or "").strip()
        hosted_url = raw.get("hostedUrl") or ""
        job_id = raw.get("id") or ""
        if not (title and hosted_url and job_id):
            return None
        try:
            return ScrapedJob(
                source=JobSource.LEVER,
                source_job_id=f"{seed.slug}:{job_id}",
                company_name=seed.name,
                company_domain=f"{seed.slug}.com",
                company_size_bucket=CompanySizeBucket.UNKNOWN,
                title=title,
                location=location,
                apply_url=hosted_url,
                description=(raw.get("descriptionPlain") or "").strip(),
                posted_at=_ms_to_datetime(raw.get("createdAt")),
            )
        except ValueError as exc:
            log.warning("lever.invalid_job", slug=seed.slug, job_id=job_id, error=str(exc))
            return None
```

- [ ] **Step 5: Run test to verify pass**

```bash
uv run pytest tests/test_scrapers/test_lever.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 6: Commit**

```bash
git add src/knockknock/scrapers/lever.py tests/test_scrapers/test_lever.py tests/fixtures/lever_groww_postings.json
git commit -m "feat(scrapers): add Lever public-postings scraper"
```

### Task 10.5: Ashby scraper

**Files:**
- Create: `src/knockknock/scrapers/ashby.py`
- Create: `tests/test_scrapers/test_ashby.py`
- Create: `tests/fixtures/ashby_postman_jobs.json`

Ashby's public job-board API: `GET https://api.ashbyhq.com/posting-api/job-board/<slug>?includeCompensation=false` returns `{"jobs": [...]}`. Each job has `id`, `title`, `locationName`, `employmentType`, `jobUrl`, `descriptionPlain`, `publishedAt` (ISO 8601).

- [ ] **Step 1: Save real-shape fixture**

Create `tests/fixtures/ashby_postman_jobs.json`:

```json
{
  "jobs": [
    {
      "id": "ash-001",
      "title": "Staff Backend Engineer",
      "locationName": "Bangalore",
      "employmentType": "FullTime",
      "jobUrl": "https://jobs.ashbyhq.com/postman/ash-001",
      "descriptionPlain": "Scale our API platform. Stack: Node, TypeScript, Postgres.",
      "publishedAt": "2026-05-20T12:00:00.000Z"
    },
    {
      "id": "ash-002",
      "title": "Marketing Lead",
      "locationName": "Bangalore",
      "employmentType": "FullTime",
      "jobUrl": "https://jobs.ashbyhq.com/postman/ash-002",
      "descriptionPlain": "Run growth.",
      "publishedAt": "2026-05-21T10:00:00.000Z"
    },
    {
      "id": "ash-003",
      "title": "Backend Engineer",
      "locationName": "London",
      "employmentType": "FullTime",
      "jobUrl": "https://jobs.ashbyhq.com/postman/ash-003",
      "descriptionPlain": "UK only.",
      "publishedAt": "2026-05-22T10:00:00.000Z"
    }
  ]
}
```

- [ ] **Step 2: Write failing test**

Create `tests/test_scrapers/test_ashby.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from knockknock.db.enums import JobSource
from knockknock.scrapers.ashby import AshbyScraper
from knockknock.scrapers.ats_seed import AtsSeedCompany


FIXTURE = Path(__file__).parent.parent / "fixtures" / "ashby_postman_jobs.json"


@respx.mock
def test_ashby_scraper_filters_by_location() -> None:
    payload = json.loads(FIXTURE.read_text())
    respx.get(
        "https://api.ashbyhq.com/posting-api/job-board/postman?includeCompensation=false"
    ).mock(return_value=httpx.Response(200, json=payload))

    scraper = AshbyScraper(
        seeds=[AtsSeedCompany(slug="postman", name="Postman")],
        http=httpx.Client(timeout=10),
    )
    jobs = list(scraper.scrape())

    # London is filtered out; Marketing keeps in (we keep all roles — Phase 4 handles title filtering).
    assert {j.source_job_id for j in jobs} == {"postman:ash-001", "postman:ash-002"}
    backend = next(j for j in jobs if j.source_job_id == "postman:ash-001")
    assert backend.source == JobSource.ASHBY
    assert backend.title == "Staff Backend Engineer"
    assert backend.location == "Bangalore"
    assert "API platform" in backend.description
```

- [ ] **Step 3: Run test to verify failure**

```bash
uv run pytest tests/test_scrapers/test_ashby.py -v
```

Expected: FAIL.

- [ ] **Step 4: Implement scraper**

Create `src/knockknock/scrapers/ashby.py`:

```python
"""Ashby public job-board scraper.

Endpoint: GET https://api.ashbyhq.com/posting-api/job-board/<slug>?includeCompensation=false
Auth: none.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime
from typing import Final

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from knockknock.db.enums import CompanySizeBucket, JobSource
from knockknock.scrapers.ats_seed import AtsSeedCompany
from knockknock.scrapers.base import ScrapedJob

log = structlog.get_logger(__name__)

_BASE: Final = "https://api.ashbyhq.com/posting-api/job-board"
_INDIA_RE: Final = re.compile(r"(bangalore|bengaluru|india|remote)", re.IGNORECASE)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class AshbyScraper:
    source = JobSource.ASHBY
    name = "ashby"

    def __init__(
        self,
        seeds: list[AtsSeedCompany],
        http: httpx.Client,
        *,
        location_filter: re.Pattern[str] = _INDIA_RE,
    ) -> None:
        self._seeds = seeds
        self._http = http
        self._location_filter = location_filter

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    def _fetch(self, slug: str) -> dict:
        url = f"{_BASE}/{slug}?includeCompensation=false"
        resp = self._http.get(url)
        resp.raise_for_status()
        return resp.json() if isinstance(resp.json(), dict) else {}

    def scrape(self) -> Iterator[ScrapedJob]:
        for seed in self._seeds:
            try:
                payload = self._fetch(seed.slug)
            except httpx.HTTPError as exc:
                log.warning("ashby.fetch_failed", slug=seed.slug, error=str(exc))
                continue
            for raw in payload.get("jobs", []):
                job = self._maybe_build(seed, raw)
                if job is not None:
                    yield job

    def _maybe_build(self, seed: AtsSeedCompany, raw: dict) -> ScrapedJob | None:
        location = (raw.get("locationName") or "").strip()
        if not self._location_filter.search(location):
            return None
        title = (raw.get("title") or "").strip()
        job_url = raw.get("jobUrl") or ""
        job_id = raw.get("id") or ""
        if not (title and job_url and job_id):
            return None
        try:
            return ScrapedJob(
                source=JobSource.ASHBY,
                source_job_id=f"{seed.slug}:{job_id}",
                company_name=seed.name,
                company_domain=f"{seed.slug}.com",
                company_size_bucket=CompanySizeBucket.UNKNOWN,
                title=title,
                location=location,
                apply_url=job_url,
                description=(raw.get("descriptionPlain") or "").strip(),
                posted_at=_parse_iso(raw.get("publishedAt")),
            )
        except ValueError as exc:
            log.warning("ashby.invalid_job", slug=seed.slug, job_id=job_id, error=str(exc))
            return None
```

- [ ] **Step 5: Run test to verify pass**

```bash
uv run pytest tests/test_scrapers/test_ashby.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 6: Commit**

```bash
git add src/knockknock/scrapers/ashby.py tests/test_scrapers/test_ashby.py tests/fixtures/ashby_postman_jobs.json
git commit -m "feat(scrapers): add Ashby public job-board scraper"
```

### Task 10.6: Playwright fetch helper

**Files:**
- Create: `src/knockknock/scrapers/_playwright.py`
- Create: `tests/test_scrapers/test_playwright_helper.py`

A small wrapper that owns Chromium lifecycle. Two reasons to centralize: (1) tests can monkeypatch a single `fetch_html` function instead of standing up Playwright; (2) Wellfound and YC WaaS share the same auth/cookies plumbing.

- [ ] **Step 1: Write failing test**

Create `tests/test_scrapers/test_playwright_helper.py`:

```python
from __future__ import annotations

from knockknock.scrapers import _playwright


def test_render_url_uses_injected_renderer() -> None:
    captured: dict[str, str] = {}

    def fake_render(url: str, *, wait_selector: str | None = None) -> str:
        captured["url"] = url
        captured["wait"] = wait_selector or ""
        return f"<html><body>{url}</body></html>"

    helper = _playwright.PlaywrightFetcher(_renderer=fake_render)
    html = helper.render("https://example.test/jobs", wait_selector="div.job-card")
    assert "https://example.test/jobs" in html
    assert captured == {"url": "https://example.test/jobs", "wait": "div.job-card"}
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_scrapers/test_playwright_helper.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement helper**

Create `src/knockknock/scrapers/_playwright.py`:

```python
"""Thin Playwright wrapper.

Used by scrapers that need a JavaScript-rendered DOM (Wellfound, YC WaaS).
The real Chromium-driving function is imported lazily so unit tests can skip
the heavy dependency.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    Renderer = Callable[..., str]


def _render_with_playwright(url: str, *, wait_selector: str | None = None) -> str:
    """Real implementation. Import is lazy so tests don't pay the cost."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 1024},
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            if wait_selector:
                page.wait_for_selector(wait_selector, timeout=20_000)
            html: str = page.content()
            return html
        finally:
            browser.close()


@dataclass(slots=True)
class PlaywrightFetcher:
    """Inject `_renderer` in tests; default uses real Chromium."""

    _renderer: "Renderer" = _render_with_playwright

    def render(self, url: str, *, wait_selector: str | None = None) -> str:
        return self._renderer(url, wait_selector=wait_selector)
```

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/test_scrapers/test_playwright_helper.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/knockknock/scrapers/_playwright.py tests/test_scrapers/test_playwright_helper.py
git commit -m "feat(scrapers): add Playwright fetch helper with injectable renderer"
```

### Task 10.7: Wellfound scraper (fixture-driven)

**Files:**
- Create: `src/knockknock/scrapers/wellfound.py`
- Create: `tests/test_scrapers/test_wellfound.py`
- Create: `tests/fixtures/wellfound_listings.html`

Wellfound (formerly AngelList Talent) does not publish a stable public API; the job listings page is rendered server-side with hydrated React. The scraper renders the search URL via `PlaywrightFetcher`, then parses with selectolax. Cards have a stable `[data-test=JobSearchResults]` container in May 2026 — but layout drift is inevitable, so the scraper is **defensive**: missing fields cause that single card to be skipped, never a crash.

> **Note on live scraping:** This scraper does not bypass any login or paywall — Wellfound's job-search pages are publicly indexable. Tests run against a saved HTML fixture; live runs are gated behind `pytest -m live` and `--scrape-live`.

- [ ] **Step 1: Save a realistic HTML fixture**

Create `tests/fixtures/wellfound_listings.html`. This is a hand-crafted minimal-but-representative DOM (matches the selectors we'll use; keep it tiny):

```html
<!doctype html>
<html><body>
<div data-test="JobSearchResults">
  <div data-test="StartupResult">
    <a data-test="startup-link" href="/company/acme-labs">Acme Labs</a>
    <div data-test="startup-size">11-50 employees</div>
    <div data-test="JobListingSearchResult">
      <a data-test="job-link" href="/jobs/12345-backend-engineer">Backend Engineer</a>
      <div data-test="job-location">Bangalore · Remote</div>
      <div data-test="job-description">
        Build infra. Stack: Go, Postgres, Kafka. Open to remote within India.
      </div>
    </div>
    <div data-test="JobListingSearchResult">
      <a data-test="job-link" href="/jobs/12346-designer">Senior Designer</a>
      <div data-test="job-location">San Francisco</div>
      <div data-test="job-description">Figma all day.</div>
    </div>
  </div>
  <div data-test="StartupResult">
    <a data-test="startup-link" href="/company/zeta-fintech">Zeta Fintech</a>
    <div data-test="startup-size">51-200 employees</div>
    <div data-test="JobListingSearchResult">
      <a data-test="job-link" href="/jobs/22222-platform-engineer">Platform Engineer</a>
      <div data-test="job-location">Bengaluru</div>
      <div data-test="job-description">Run our k8s clusters.</div>
    </div>
  </div>
</div>
</body></html>
```

- [ ] **Step 2: Write failing test**

Create `tests/test_scrapers/test_wellfound.py`:

```python
from __future__ import annotations

from pathlib import Path

from knockknock.db.enums import CompanySizeBucket, JobSource
from knockknock.scrapers._playwright import PlaywrightFetcher
from knockknock.scrapers.wellfound import WellfoundScraper


FIXTURE = Path(__file__).parent.parent / "fixtures" / "wellfound_listings.html"


def _stub_fetcher() -> PlaywrightFetcher:
    html = FIXTURE.read_text()

    def renderer(url: str, *, wait_selector: str | None = None) -> str:
        return html

    return PlaywrightFetcher(_renderer=renderer)


def test_wellfound_scraper_emits_india_jobs_only() -> None:
    scraper = WellfoundScraper(
        location="Bangalore",
        role_types=["engineering"],
        remote=True,
        fetcher=_stub_fetcher(),
    )
    jobs = list(scraper.scrape())

    assert {j.source_job_id for j in jobs} == {
        "12345-backend-engineer",
        "22222-platform-engineer",
    }
    backend = next(j for j in jobs if j.source_job_id == "12345-backend-engineer")
    assert backend.source == JobSource.WELLFOUND
    assert backend.company_name == "Acme Labs"
    assert backend.company_size_bucket == CompanySizeBucket.SERIES_A
    assert backend.location == "Bangalore · Remote"
    assert "Go, Postgres, Kafka" in backend.description
    assert backend.apply_url == "https://wellfound.com/jobs/12345-backend-engineer"


def test_wellfound_scraper_skips_malformed_cards() -> None:
    def renderer(url: str, *, wait_selector: str | None = None) -> str:
        return (
            '<div data-test="JobSearchResults">'
            '<div data-test="StartupResult">'
            '<a data-test="startup-link" href="/company/x">X</a>'
            # No size, no job-link → card is silently skipped, no crash.
            '<div data-test="JobListingSearchResult">'
            '<div data-test="job-location">Bangalore</div>'
            "</div></div></div>"
        )

    fetcher = PlaywrightFetcher(_renderer=renderer)
    scraper = WellfoundScraper(
        location="Bangalore",
        role_types=["engineering"],
        remote=True,
        fetcher=fetcher,
    )
    assert list(scraper.scrape()) == []
```

- [ ] **Step 3: Run test to verify failure**

```bash
uv run pytest tests/test_scrapers/test_wellfound.py -v
```

Expected: FAIL.

- [ ] **Step 4: Implement scraper**

Create `src/knockknock/scrapers/wellfound.py`:

```python
"""Wellfound (ex-AngelList Talent) job-listings scraper.

Loads the public listings page via Playwright, parses cards with selectolax.
Selectors target stable `data-test` attributes. Cards with missing fields are
skipped (drift-tolerant).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Final
from urllib.parse import quote

import structlog
from selectolax.parser import HTMLParser, Node

from knockknock.db.enums import CompanySizeBucket, JobSource
from knockknock.scrapers._playwright import PlaywrightFetcher
from knockknock.scrapers.base import ScrapedJob

log = structlog.get_logger(__name__)

_BASE: Final = "https://wellfound.com"
_INDIA_RE: Final = re.compile(r"(bangalore|bengaluru|india|remote)", re.IGNORECASE)
_SIZE_BUCKETS: Final[dict[str, CompanySizeBucket]] = {
    "1-10": CompanySizeBucket.SEED,
    "11-50": CompanySizeBucket.SERIES_A,
    "51-200": CompanySizeBucket.SERIES_B,
    "201-500": CompanySizeBucket.SERIES_C,
    "501-1000": CompanySizeBucket.LATE_STAGE,
}


def _size_bucket(label: str) -> CompanySizeBucket:
    """Map '11-50 employees' to the closest stage bucket."""
    cleaned = label.strip().lower().replace("employees", "").strip()
    for prefix, bucket in _SIZE_BUCKETS.items():
        if cleaned.startswith(prefix):
            return bucket
    return CompanySizeBucket.UNKNOWN


def _text_or_empty(node: Node | None) -> str:
    return node.text(strip=True) if node is not None else ""


def _attr_or_empty(node: Node | None, name: str) -> str:
    if node is None:
        return ""
    value = node.attributes.get(name)
    return value or ""


class WellfoundScraper:
    source = JobSource.WELLFOUND
    name = "wellfound"

    def __init__(
        self,
        *,
        location: str,
        role_types: list[str],
        remote: bool,
        fetcher: PlaywrightFetcher,
    ) -> None:
        self._location = location
        self._role_types = role_types
        self._remote = remote
        self._fetcher = fetcher

    def _build_url(self) -> str:
        params = [f"location={quote(self._location)}"]
        if self._role_types:
            params.append("roles=" + ",".join(quote(r) for r in self._role_types))
        if self._remote:
            params.append("remote=true")
        return f"{_BASE}/jobs?{'&'.join(params)}"

    def scrape(self) -> Iterator[ScrapedJob]:
        url = self._build_url()
        try:
            html = self._fetcher.render(url, wait_selector='[data-test="JobSearchResults"]')
        except Exception as exc:
            log.warning("wellfound.render_failed", url=url, error=str(exc))
            return
        tree = HTMLParser(html)
        for startup in tree.css('[data-test="StartupResult"]'):
            yield from self._iter_startup_jobs(startup)

    def _iter_startup_jobs(self, startup: Node) -> Iterator[ScrapedJob]:
        startup_link = startup.css_first('[data-test="startup-link"]')
        company_name = _text_or_empty(startup_link)
        if not company_name:
            return
        company_href = _attr_or_empty(startup_link, "href")
        company_slug = company_href.removeprefix("/company/").strip("/")
        if not company_slug:
            return
        size_label = _text_or_empty(startup.css_first('[data-test="startup-size"]'))
        size_bucket = _size_bucket(size_label)

        for card in startup.css('[data-test="JobListingSearchResult"]'):
            location = _text_or_empty(card.css_first('[data-test="job-location"]'))
            if not _INDIA_RE.search(location):
                continue
            link = card.css_first('[data-test="job-link"]')
            href = _attr_or_empty(link, "href")
            title = _text_or_empty(link)
            description = _text_or_empty(card.css_first('[data-test="job-description"]'))
            if not (href and title):
                continue
            source_job_id = href.removeprefix("/jobs/").strip("/")
            try:
                yield ScrapedJob(
                    source=JobSource.WELLFOUND,
                    source_job_id=source_job_id,
                    company_name=company_name,
                    company_domain=f"{company_slug}.com",
                    company_size_bucket=size_bucket,
                    title=title,
                    location=location,
                    apply_url=f"{_BASE}{href}",
                    description=description,
                    posted_at=None,  # Wellfound lists relative time only; skip
                )
            except ValueError as exc:
                log.warning(
                    "wellfound.invalid_job",
                    company=company_name,
                    job_href=href,
                    error=str(exc),
                )
                continue
```

> **Note on `company_domain`:** Wellfound's startup page exposes the marketing domain, but we'd need a second navigation per company to read it. We use `<slug>.com` as a placeholder and rely on Phase 6 phonebook resolution to correct it.

- [ ] **Step 5: Run test to verify pass**

```bash
uv run pytest tests/test_scrapers/test_wellfound.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 6: Commit**

```bash
git add src/knockknock/scrapers/wellfound.py tests/test_scrapers/test_wellfound.py tests/fixtures/wellfound_listings.html
git commit -m "feat(scrapers): add Wellfound listings scraper (Playwright + selectolax)"
```

### Task 10.8: YC Work-at-a-Startup scraper (fixture-driven)

**Files:**
- Create: `src/knockknock/scrapers/yc_waas.py`
- Create: `tests/test_scrapers/test_yc_waas.py`
- Create: `tests/fixtures/yc_waas_listings.html`

Y Combinator's `workatastartup.com` requires login to see job descriptions but the **listing page** (titles + companies + locations) is public. We scrape the public listing and let the LLM scoring stage work from titles alone for YC-WaaS jobs — descriptions remain short. This is acceptable because YC-WaaS jobs are usually high-signal anyway.

- [ ] **Step 1: Save HTML fixture**

Create `tests/fixtures/yc_waas_listings.html`:

```html
<!doctype html>
<html><body>
<div class="directory-list">
  <div class="company-card">
    <a class="company-name" href="/companies/acme">Acme (YC S25)</a>
    <div class="company-batch">S25</div>
    <div class="job-row">
      <a class="job-link" href="/companies/acme/jobs/9001-backend-engineer">Backend Engineer</a>
      <div class="job-location">Bengaluru, India</div>
    </div>
    <div class="job-row">
      <a class="job-link" href="/companies/acme/jobs/9002-marketing">Marketing</a>
      <div class="job-location">Remote (US)</div>
    </div>
  </div>
  <div class="company-card">
    <a class="company-name" href="/companies/zeta">Zeta (YC W26)</a>
    <div class="company-batch">W26</div>
    <div class="job-row">
      <a class="job-link" href="/companies/zeta/jobs/9100-fullstack">Fullstack Engineer</a>
      <div class="job-location">India · Remote</div>
    </div>
  </div>
</div>
</body></html>
```

- [ ] **Step 2: Write failing test**

Create `tests/test_scrapers/test_yc_waas.py`:

```python
from __future__ import annotations

from pathlib import Path

from knockknock.db.enums import JobSource
from knockknock.scrapers._playwright import PlaywrightFetcher
from knockknock.scrapers.yc_waas import YcWaasScraper


FIXTURE = Path(__file__).parent.parent / "fixtures" / "yc_waas_listings.html"


def _stub_fetcher() -> PlaywrightFetcher:
    html = FIXTURE.read_text()
    return PlaywrightFetcher(_renderer=lambda url, *, wait_selector=None: html)


def test_yc_waas_scraper_emits_india_jobs() -> None:
    scraper = YcWaasScraper(location="India", role="engineer", fetcher=_stub_fetcher())
    jobs = list(scraper.scrape())

    assert {j.source_job_id for j in jobs} == {
        "acme:9001-backend-engineer",
        "zeta:9100-fullstack",
    }
    backend = next(j for j in jobs if j.source_job_id == "acme:9001-backend-engineer")
    assert backend.source == JobSource.YC_WAAS
    assert backend.company_name == "Acme"
    assert backend.location == "Bengaluru, India"
    assert backend.apply_url == "https://www.workatastartup.com/companies/acme/jobs/9001-backend-engineer"
    # Description is empty for YC WaaS (gated behind login); score stage gets title only.
    assert backend.description == "Backend Engineer (Acme — YC S25)"
```

- [ ] **Step 3: Run test to verify failure**

```bash
uv run pytest tests/test_scrapers/test_yc_waas.py -v
```

Expected: FAIL.

- [ ] **Step 4: Implement scraper**

Create `src/knockknock/scrapers/yc_waas.py`:

```python
"""YC Work-at-a-Startup scraper (public listing page only).

Job descriptions on workatastartup.com are gated behind login, so we synthesize
a short description from `title + company + batch` and let the scoring stage
work with what's available.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Final
from urllib.parse import quote

import structlog
from selectolax.parser import HTMLParser, Node

from knockknock.db.enums import CompanySizeBucket, JobSource
from knockknock.scrapers._playwright import PlaywrightFetcher
from knockknock.scrapers.base import ScrapedJob

log = structlog.get_logger(__name__)

_BASE: Final = "https://www.workatastartup.com"
_INDIA_RE: Final = re.compile(r"(bangalore|bengaluru|india|remote)", re.IGNORECASE)
_COMPANY_NAME_RE: Final = re.compile(r"^(?P<name>.+?)\s*\(YC\s*[SWFX]\d{2}\)\s*$")


def _text_or_empty(node: Node | None) -> str:
    return node.text(strip=True) if node is not None else ""


def _attr_or_empty(node: Node | None, name: str) -> str:
    if node is None:
        return ""
    return node.attributes.get(name) or ""


class YcWaasScraper:
    source = JobSource.YC_WAAS
    name = "yc_waas"

    def __init__(
        self,
        *,
        location: str,
        role: str,
        fetcher: PlaywrightFetcher,
    ) -> None:
        self._location = location
        self._role = role
        self._fetcher = fetcher

    def _build_url(self) -> str:
        return f"{_BASE}/companies?location={quote(self._location)}&role={quote(self._role)}"

    def scrape(self) -> Iterator[ScrapedJob]:
        url = self._build_url()
        try:
            html = self._fetcher.render(url, wait_selector=".company-card")
        except Exception as exc:
            log.warning("yc_waas.render_failed", url=url, error=str(exc))
            return
        tree = HTMLParser(html)
        for card in tree.css(".company-card"):
            yield from self._iter_company_jobs(card)

    def _iter_company_jobs(self, card: Node) -> Iterator[ScrapedJob]:
        name_link = card.css_first(".company-name")
        raw_name = _text_or_empty(name_link)
        if not raw_name:
            return
        match = _COMPANY_NAME_RE.match(raw_name)
        clean_name = match.group("name").strip() if match else raw_name
        company_href = _attr_or_empty(name_link, "href")
        company_slug = company_href.removeprefix("/companies/").strip("/")
        if not company_slug:
            return
        batch = _text_or_empty(card.css_first(".company-batch"))

        for row in card.css(".job-row"):
            location = _text_or_empty(row.css_first(".job-location"))
            if not _INDIA_RE.search(location):
                continue
            link = row.css_first(".job-link")
            href = _attr_or_empty(link, "href")
            title = _text_or_empty(link)
            if not (href and title):
                continue
            source_job_id = (
                href.removeprefix(f"/companies/{company_slug}/jobs/")
                .removeprefix("/jobs/")
                .strip("/")
            )
            if not source_job_id:
                continue
            description = f"{title} ({clean_name} — YC {batch})"
            try:
                yield ScrapedJob(
                    source=JobSource.YC_WAAS,
                    source_job_id=f"{company_slug}:{source_job_id}",
                    company_name=clean_name,
                    company_domain=f"{company_slug}.com",
                    company_size_bucket=CompanySizeBucket.SEED,  # YC startups default to SEED
                    title=title,
                    location=location,
                    apply_url=f"{_BASE}{href}",
                    description=description,
                    posted_at=None,
                )
            except ValueError as exc:
                log.warning(
                    "yc_waas.invalid_job",
                    company=clean_name,
                    job_href=href,
                    error=str(exc),
                )
                continue
```

> **Note on `CompanySizeBucket.SEED`:** YC-WaaS companies are dominated by SEED/SERIES_A; lacking real headcount data, defaulting to SEED produces the correct hard-filter behavior. Apollo enrichment in Phase 6 may upgrade `companies.size_bucket` later but the original ScrapedJob value is preserved on the JobApplication row.

- [ ] **Step 5: Run test to verify pass**

```bash
uv run pytest tests/test_scrapers/test_yc_waas.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 6: Commit**

```bash
git add src/knockknock/scrapers/yc_waas.py tests/test_scrapers/test_yc_waas.py tests/fixtures/yc_waas_listings.html
git commit -m "feat(scrapers): add YC Work-at-a-Startup public-listings scraper"
```

### Task 10.9: Expand `build_scrapers()` registry

**Files:**
- Modify: `src/knockknock/scrapers/registry.py`
- Modify: `tests/test_scrapers/test_registry.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_scrapers/test_registry.py`:

```python
def test_build_scrapers_enables_full_set(tmp_path) -> None:
    gh = tmp_path / "gh.yaml"
    gh.write_text("companies: [{slug: razorpay, name: Razorpay}]\n")
    lv = tmp_path / "lv.yaml"
    lv.write_text("companies: [{slug: groww, name: Groww}]\n")
    ay = tmp_path / "ay.yaml"
    ay.write_text("companies: [{slug: postman, name: Postman}]\n")

    prefs_text = f"""
candidate: {{name: x, current_role: x, years_experience: 1, location: x}}
target:
  locations: [x]
  titles_allow: [x]
  titles_deny: []
  seniority_allow: [x]
  company_size_allow: [SEED]
skills: {{must_have_any: [x], nice_to_have: []}}
scoring:
  min_score_to_draft: 70
  weight_skill_match: 40
  weight_seniority_match: 25
  weight_location_match: 20
  weight_company_stage: 15
limits: {{daily_drafts_cap: 1, hourly_discover_cap: 1, gemini_pro_rpd_ceiling: 1}}
sources:
  hn: {{enabled: true, months_lookback: 1}}
  wellfound: {{enabled: true, location: Bangalore, role_types: [engineering], remote: true}}
  yc_waas: {{enabled: true, location: India, role: engineer}}
  greenhouse: {{enabled: true, company_seed_list_path: "{gh}"}}
  lever: {{enabled: true, company_seed_list_path: "{lv}"}}
  ashby: {{enabled: true, company_seed_list_path: "{ay}"}}
"""
    f = tmp_path / "p.yaml"
    f.write_text(prefs_text)

    from knockknock.config.preferences import load_preferences
    from knockknock.db.enums import JobSource
    from knockknock.scrapers.registry import build_scrapers

    scrapers = build_scrapers(prefs)  # noqa: F821 (prefs assigned below)
    prefs = load_preferences(f)
    scrapers = build_scrapers(prefs)

    sources = {s.source for s in scrapers}
    assert sources == {
        JobSource.HN,
        JobSource.WELLFOUND,
        JobSource.YC_WAAS,
        JobSource.GREENHOUSE,
        JobSource.LEVER,
        JobSource.ASHBY,
    }
```

(Yes, the duplicate `scrapers = build_scrapers(prefs)` is intentional — the first call is meant to fail until the registry is expanded. Remove it once the file compiles.) Practically: replace the duplicate with the single-line correct version below before running.

Cleaned form:

```python
    prefs = load_preferences(f)
    scrapers = build_scrapers(prefs)
    sources = {s.source for s in scrapers}
    assert sources == {
        JobSource.HN,
        JobSource.WELLFOUND,
        JobSource.YC_WAAS,
        JobSource.GREENHOUSE,
        JobSource.LEVER,
        JobSource.ASHBY,
    }
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_scrapers/test_registry.py::test_build_scrapers_enables_full_set -v
```

Expected: FAIL.

- [ ] **Step 3: Expand registry**

Replace `src/knockknock/scrapers/registry.py` body with:

```python
"""Map enabled sources from preferences to scraper instances."""

from __future__ import annotations

import httpx

from knockknock.config.preferences import JobPreferences
from knockknock.scrapers._playwright import PlaywrightFetcher
from knockknock.scrapers.ashby import AshbyScraper
from knockknock.scrapers.ats_seed import load_ats_seed
from knockknock.scrapers.base import Scraper
from knockknock.scrapers.greenhouse import GreenhouseScraper
from knockknock.scrapers.hn import HNScraper
from knockknock.scrapers.lever import LeverScraper
from knockknock.scrapers.wellfound import WellfoundScraper
from knockknock.scrapers.yc_waas import YcWaasScraper


def build_scrapers(
    prefs: JobPreferences,
    *,
    http_client: httpx.Client | None = None,
    fetcher: PlaywrightFetcher | None = None,
) -> list[Scraper]:
    """Build the list of enabled scrapers, sharing HTTP + Playwright resources."""
    http = http_client if http_client is not None else httpx.Client(timeout=30)
    pw = fetcher if fetcher is not None else PlaywrightFetcher()

    out: list[Scraper] = []
    s = prefs.sources

    if s.hn.enabled:
        out.append(HNScraper(months_lookback=s.hn.months_lookback))

    if s.wellfound.enabled:
        out.append(
            WellfoundScraper(
                location=s.wellfound.location,
                role_types=s.wellfound.role_types,
                remote=s.wellfound.remote,
                fetcher=pw,
            )
        )

    if s.yc_waas.enabled:
        out.append(
            YcWaasScraper(
                location=s.yc_waas.location,
                role=s.yc_waas.role,
                fetcher=pw,
            )
        )

    if s.greenhouse.enabled and s.greenhouse.company_seed_list_path is not None:
        out.append(
            GreenhouseScraper(
                seeds=load_ats_seed(s.greenhouse.company_seed_list_path),
                http=http,
            )
        )

    if s.lever.enabled and s.lever.company_seed_list_path is not None:
        out.append(
            LeverScraper(
                seeds=load_ats_seed(s.lever.company_seed_list_path),
                http=http,
            )
        )

    if s.ashby.enabled and s.ashby.company_seed_list_path is not None:
        out.append(
            AshbyScraper(
                seeds=load_ats_seed(s.ashby.company_seed_list_path),
                http=http,
            )
        )

    return out
```

> **Note on shared resources:** `http_client` and `fetcher` are injectable so the CLI (Phase 11) can manage their lifetime — Playwright in particular is expensive to start. Both default to fresh instances for backwards compat with Phase 3's test that called `build_scrapers(prefs)` with no kwargs.

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/test_scrapers/test_registry.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/knockknock/scrapers/registry.py tests/test_scrapers/test_registry.py
git commit -m "feat(scrapers): wire all six sources into build_scrapers() registry"
```

### Task 10.10: Update example `job_preferences.yaml`

**Files:**
- Modify: `config/job_preferences.yaml`

- [ ] **Step 1: Update example**

In `config/job_preferences.yaml`, update the `sources:` section to:

```yaml
sources:
  hn:
    enabled: true
    months_lookback: 1
  wellfound:
    enabled: true
    location: Bangalore
    role_types: [engineering]
    remote: true
  yc_waas:
    enabled: true
    location: India
    role: engineer
  greenhouse:
    enabled: true
    company_seed_list_path: config/seed_companies_greenhouse.yaml
  lever:
    enabled: true
    company_seed_list_path: config/seed_companies_lever.yaml
  ashby:
    enabled: true
    company_seed_list_path: config/seed_companies_ashby.yaml
```

- [ ] **Step 2: Verify it loads**

```bash
uv run python -c "from knockknock.config.preferences import load_preferences; from pathlib import Path; print(load_preferences(Path('config/job_preferences.yaml')).sources.greenhouse)"
```

Expected: prints `GreenhouseSource(enabled=True, company_seed_list_path=PosixPath('config/seed_companies_greenhouse.yaml'))`.

- [ ] **Step 3: Commit**

```bash
git add config/job_preferences.yaml
git commit -m "config: enable all six job sources in example preferences"
```

### Task 10.11: Manual smoke run

**Files:** (none — manual verification only)

- [ ] **Step 1: One-off live discover**

```bash
uv run knockknock pipeline run --once --skip-after discover
```

Expected:
- `stage.completed stage=discover processed=N` where N > 0
- Pipeline run row appears in `pipeline_runs`
- Spot-check: `companies` table has at least one row from each enabled source
- `job_applications` rows show `source` ∈ {WELLFOUND, YC_WAAS, HN, GREENHOUSE, LEVER, ASHBY}

If a Playwright scraper fails to start, run `uv run playwright install chromium` and retry.

- [ ] **Step 2: Re-run to confirm idempotency**

```bash
uv run knockknock pipeline run --once --skip-after discover
```

Expected: `processed=N`, `advanced=0` for every scraper (duplicates skipped on `(source, source_job_id)` unique constraint).

- [ ] **Step 3: No commit needed** — this is a verification step.

---

### Phase 10 Wrap-up

After this phase:
- All six job sources from the spec produce real `ScrapedJob` rows.
- ATS scrapers are pure JSON-over-httpx with respx-mockable tests.
- Playwright is fully isolated to two scrapers and a single helper module; tests run without Chromium thanks to the injectable `_renderer`.
- Seed YAMLs make it trivial to add new Greenhouse/Lever/Ashby boards without code changes.
- Live runs are gated behind `pytest -m live`; CI stays fast.

**Schema notes flagged for Phase 1 follow-up (none new):** all `ScrapedJob` fields used here already exist on `job_applications`; no schema changes required.

**Next:** Phase 11 — Typer CLI subcommands (`status`, `show`, `errors`, `retry`), daily digest job, observability polish (build_pipeline factory, async Stage protocol, structured-log polish).

← [Index](00-index.md) · [Prev: phase-09-telegram.md](phase-09-telegram.md) · [Next: phase-11-cli-digest.md](phase-11-cli-digest.md)
