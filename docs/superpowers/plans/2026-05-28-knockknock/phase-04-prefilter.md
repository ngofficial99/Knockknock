← [Index](00-index.md) · [Prev: phase-03-pipeline-hn.md](phase-03-pipeline-hn.md) · [Next: phase-05-gemini-score.md](phase-05-gemini-score.md)

## Phase 4: Pre-Filter Rules + Blacklist Sync

**Outcome:** A second pipeline stage reads every `JobApplication` in status `DISCOVERED`, applies hard rules from `job_preferences.yaml` plus a `companies_blacklist` lookup, and advances each row to either `PRE_FILTERED` or `PRE_FILTER_REJECTED` with a typed `rejection_reason`. A separate `BlacklistSync` step reads `config/blacklist.yaml` and reconciles `companies_blacklist` rows. Both are deterministic and exhaustively unit-tested.

### Task 4.1: Blacklist YAML schema + DB sync

**Files:**
- Create: `config/blacklist.yaml`
- Create: `src/knockknock/filter/__init__.py`
- Create: `src/knockknock/filter/blacklist.py`
- Create: `tests/test_filter/__init__.py`
- Create: `tests/test_filter/test_blacklist_sync.py`

- [ ] **Step 1: Write seed blacklist YAML**

Create `config/blacklist.yaml`:

```yaml
# Companies and patterns to never apply to.
# `pattern` is matched against the lowercased company name OR domain via SQL ILIKE.
# Use % for wildcards (e.g. "%consulting%" rejects anything containing "consulting").
entries:
  - pattern: "zeotap"
    reason: "current employer"
  - pattern: "%consulting%"
    reason: "consultancies, not product startups"
  - pattern: "%staffing%"
    reason: "staffing agencies"
  - pattern: "%recruiting%"
    reason: "recruiter spam"
```

- [ ] **Step 2: Write failing test**

Create `tests/test_filter/__init__.py` (empty).

Create `tests/test_filter/test_blacklist_sync.py`:

```python
from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

from knockknock.db.models import CompanyBlacklist
from knockknock.filter.blacklist import BlacklistMatcher, sync_blacklist_from_yaml


def _write_yaml(tmp_path: Path, body: str) -> Path:
    f = tmp_path / "bl.yaml"
    f.write_text(body)
    return f


def test_sync_inserts_new_entries(db_session: Session, tmp_path: Path) -> None:
    f = _write_yaml(
        tmp_path,
        """
entries:
  - {pattern: "acme", reason: "test"}
  - {pattern: "%spam%", reason: "spam"}
""",
    )
    inserted, removed = sync_blacklist_from_yaml(db_session, f)
    assert inserted == 2
    assert removed == 0
    rows = db_session.exec(select(CompanyBlacklist)).all()
    assert {r.pattern for r in rows} == {"acme", "%spam%"}


def test_sync_is_idempotent(db_session: Session, tmp_path: Path) -> None:
    f = _write_yaml(tmp_path, 'entries:\n  - {pattern: "acme", reason: "x"}\n')
    sync_blacklist_from_yaml(db_session, f)
    inserted, removed = sync_blacklist_from_yaml(db_session, f)
    assert inserted == 0
    assert removed == 0


def test_sync_removes_entries_dropped_from_yaml(db_session: Session, tmp_path: Path) -> None:
    f1 = _write_yaml(
        tmp_path,
        'entries:\n  - {pattern: "a", reason: "x"}\n  - {pattern: "b", reason: "y"}\n',
    )
    sync_blacklist_from_yaml(db_session, f1)
    f2 = _write_yaml(tmp_path, 'entries:\n  - {pattern: "a", reason: "x"}\n')
    inserted, removed = sync_blacklist_from_yaml(db_session, f2)
    assert inserted == 0
    assert removed == 1


def test_matcher_returns_pattern_on_hit(db_session: Session, tmp_path: Path) -> None:
    f = _write_yaml(
        tmp_path,
        'entries:\n  - {pattern: "%consulting%", reason: "x"}\n  - {pattern: "zeotap", reason: "y"}\n',
    )
    sync_blacklist_from_yaml(db_session, f)
    matcher = BlacklistMatcher.load(db_session)
    assert matcher.match("Big Consulting Co", "bigconsulting.com") == "%consulting%"
    assert matcher.match("Zeotap", "zeotap.com") == "zeotap"
    assert matcher.match("Acme Labs", "acme.test") is None
```

- [ ] **Step 3: Run test to verify failure**

```bash
uv run pytest tests/test_filter/test_blacklist_sync.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Implement blacklist module**

Create `src/knockknock/filter/__init__.py` (empty).

Create `src/knockknock/filter/blacklist.py`:

```python
"""Blacklist YAML → DB sync and in-memory matcher."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlmodel import Session, select

from knockknock.db.models import CompanyBlacklist
from knockknock.exceptions import ConfigError


def _load_yaml(path: Path) -> list[tuple[str, str | None]]:
    if not path.exists():
        raise FileNotFoundError(f"blacklist file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    entries = raw.get("entries") or []
    if not isinstance(entries, list):
        raise ConfigError("blacklist 'entries' must be a list")
    parsed: list[tuple[str, str | None]] = []
    for item in entries:
        if not isinstance(item, dict) or "pattern" not in item:
            raise ConfigError(f"invalid blacklist entry: {item!r}")
        pat = str(item["pattern"]).strip().lower()
        if not pat:
            raise ConfigError("empty pattern in blacklist")
        parsed.append((pat, item.get("reason")))
    return parsed


def sync_blacklist_from_yaml(session: Session, path: Path) -> tuple[int, int]:
    """Reconcile DB rows to YAML. Returns (inserted, removed)."""
    desired = _load_yaml(path)
    desired_patterns = {p for p, _ in desired}

    existing_rows = session.exec(select(CompanyBlacklist)).all()
    existing_patterns = {r.pattern for r in existing_rows}

    to_insert = desired_patterns - existing_patterns
    to_remove = existing_patterns - desired_patterns

    by_pattern = {p: reason for p, reason in desired}
    for pattern in to_insert:
        session.add(CompanyBlacklist(pattern=pattern, reason=by_pattern.get(pattern)))

    for row in existing_rows:
        if row.pattern in to_remove:
            session.delete(row)

    session.flush()
    return len(to_insert), len(to_remove)


def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    escaped = re.escape(pattern.lower())
    # Restore SQL LIKE wildcards
    escaped = escaped.replace(r"\%", ".*").replace(r"\_", ".")
    return re.compile(f"^{escaped}$")


@dataclass(frozen=True, slots=True)
class BlacklistMatcher:
    patterns: tuple[tuple[str, re.Pattern[str]], ...]

    @classmethod
    def load(cls, session: Session) -> "BlacklistMatcher":
        rows = session.exec(select(CompanyBlacklist)).all()
        compiled = tuple((r.pattern, _pattern_to_regex(r.pattern)) for r in rows)
        return cls(patterns=compiled)

    def match(self, company_name: str, company_domain: str) -> str | None:
        """Return the matching pattern (canonical form) or None."""
        haystacks = (company_name.lower(), company_domain.lower())
        for raw_pattern, regex in self.patterns:
            for hay in haystacks:
                if regex.match(hay):
                    return raw_pattern
        return None
```

- [ ] **Step 5: Run test to verify pass**

```bash
uv run pytest tests/test_filter/test_blacklist_sync.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 6: Commit**

```bash
git add config/blacklist.yaml src/knockknock/filter/__init__.py src/knockknock/filter/blacklist.py tests/test_filter
git commit -m "feat(filter): add blacklist YAML sync and matcher"
```

### Task 4.2: Hard-rule pre-filter engine

**Files:**
- Create: `src/knockknock/filter/rules.py`
- Create: `tests/test_filter/test_rules.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_filter/test_rules.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from knockknock.config.preferences import JobPreferences
from knockknock.db.enums import CompanySizeBucket, JobSource, RejectionReason
from knockknock.db.models import Company, JobApplication
from knockknock.filter.rules import RuleEngine, RuleVerdict


def _prefs() -> JobPreferences:
    return JobPreferences.model_validate(
        {
            "candidate": {"name": "x", "current_role": "x", "years_experience": 4, "location": "Bengaluru"},
            "target": {
                "locations": ["Bengaluru", "Bangalore", "Remote (India)"],
                "titles_allow": ["Backend Engineer", "Software Engineer"],
                "titles_deny": ["Manager", "Intern", "Frontend Engineer"],
                "seniority_allow": ["Mid", "Senior", "SDE 2", "SDE 3"],
                "company_size_allow": ["SEED", "SERIES_A", "SERIES_B"],
            },
            "skills": {"must_have_any": ["Python", "Java"], "nice_to_have": []},
            "scoring": {
                "min_score_to_draft": 70, "weight_skill_match": 40,
                "weight_seniority_match": 25, "weight_location_match": 20,
                "weight_company_stage": 15,
            },
            "limits": {"daily_drafts_cap": 1, "hourly_discover_cap": 1, "gemini_pro_rpd_ceiling": 1},
            "sources": {
                "hn": {"enabled": True, "months_lookback": 1},
                "wellfound": {"enabled": False, "query": ""},
                "yc_waas": {"enabled": False, "query": ""},
                "greenhouse": {"enabled": False, "boards": []},
                "lever": {"enabled": False, "boards": []},
                "ashby": {"enabled": False, "boards": []},
            },
        }
    )


def _job(**overrides: object) -> JobApplication:
    defaults: dict[str, object] = {
        "company_id": 1,
        "source": JobSource.HN,
        "source_job_id": "hn-1",
        "title": "Backend Engineer",
        "location": "Bengaluru",
        "apply_url": "https://x.test/a",
        "description": "Backend role using Python and Postgres",
        "posted_at": datetime(2026, 5, 28, tzinfo=UTC),
    }
    defaults.update(overrides)
    return JobApplication(**defaults)


def _company(size: CompanySizeBucket = CompanySizeBucket.SERIES_A) -> Company:
    return Company(id=1, name="Acme", domain="acme.test", size_bucket=size)


@pytest.fixture
def engine() -> RuleEngine:
    return RuleEngine(prefs=_prefs(), blacklist_pattern=lambda name, domain: None)


def test_passes_when_all_rules_match(engine: RuleEngine) -> None:
    verdict = engine.evaluate(_job(), _company())
    assert verdict.passed
    assert verdict.reason is None


def test_rejects_when_title_is_denied(engine: RuleEngine) -> None:
    verdict = engine.evaluate(_job(title="Engineering Manager"), _company())
    assert not verdict.passed
    assert verdict.reason == RejectionReason.ROLE_MISMATCH


def test_rejects_when_title_not_in_allow_list(engine: RuleEngine) -> None:
    verdict = engine.evaluate(_job(title="Data Scientist"), _company())
    assert not verdict.passed
    assert verdict.reason == RejectionReason.ROLE_MISMATCH


def test_rejects_when_location_is_wrong(engine: RuleEngine) -> None:
    verdict = engine.evaluate(_job(location="San Francisco, US only"), _company())
    assert not verdict.passed
    assert verdict.reason == RejectionReason.LOCATION_MISMATCH


def test_rejects_when_no_required_skill_in_description(engine: RuleEngine) -> None:
    verdict = engine.evaluate(
        _job(description="A pure Rust shop with no Python or Java"),
        _company(),
    )
    assert not verdict.passed
    assert verdict.reason == RejectionReason.ROLE_MISMATCH


def test_rejects_when_company_size_not_allowed(engine: RuleEngine) -> None:
    verdict = engine.evaluate(_job(), _company(size=CompanySizeBucket.SERIES_C_PLUS))
    assert not verdict.passed
    assert verdict.reason == RejectionReason.OTHER


def test_rejects_when_blacklisted() -> None:
    engine = RuleEngine(prefs=_prefs(), blacklist_pattern=lambda name, domain: "zeotap")
    verdict = engine.evaluate(_job(), _company())
    assert not verdict.passed
    assert verdict.reason == RejectionReason.BLACKLISTED
    assert "zeotap" in (verdict.detail or "")
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_filter/test_rules.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement rule engine**

Create `src/knockknock/filter/rules.py`:

```python
"""Deterministic hard-rule pre-filter."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from knockknock.config.preferences import JobPreferences
from knockknock.db.enums import RejectionReason
from knockknock.db.models import Company, JobApplication


@dataclass(frozen=True, slots=True)
class RuleVerdict:
    passed: bool
    reason: RejectionReason | None = None
    detail: str | None = None


BlacklistFn = Callable[[str, str], str | None]


@dataclass(frozen=True, slots=True)
class RuleEngine:
    """Applies pure-Python hard rules. No DB writes; caller mutates state."""

    prefs: JobPreferences
    blacklist_pattern: BlacklistFn

    def evaluate(self, job: JobApplication, company: Company) -> RuleVerdict:
        bl_hit = self.blacklist_pattern(company.name, company.domain)
        if bl_hit is not None:
            return RuleVerdict(False, RejectionReason.BLACKLISTED, f"matched pattern: {bl_hit}")

        title_lower = job.title.lower()
        for deny in self.prefs.target.titles_deny:
            if deny.lower() in title_lower:
                return RuleVerdict(False, RejectionReason.ROLE_MISMATCH, f"deny title: {deny}")

        if not any(allow.lower() in title_lower for allow in self.prefs.target.titles_allow):
            return RuleVerdict(False, RejectionReason.ROLE_MISMATCH, "title not in allow list")

        location_lower = job.location.lower()
        if not any(loc.lower() in location_lower for loc in self.prefs.target.locations):
            return RuleVerdict(False, RejectionReason.LOCATION_MISMATCH, job.location)

        description_lower = job.description.lower()
        title_plus_desc = f"{title_lower}\n{description_lower}"
        if not any(skill.lower() in title_plus_desc for skill in self.prefs.skills.must_have_any):
            return RuleVerdict(False, RejectionReason.ROLE_MISMATCH, "no required skill found")

        if company.size_bucket not in self.prefs.target.company_size_allow:
            return RuleVerdict(False, RejectionReason.OTHER, f"size_bucket={company.size_bucket}")

        return RuleVerdict(True)
```

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/test_filter/test_rules.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/knockknock/filter/rules.py tests/test_filter/test_rules.py
git commit -m "feat(filter): add deterministic hard-rule pre-filter engine"
```

### Task 4.3: Pre-filter pipeline stage

**Files:**
- Create: `src/knockknock/pipeline/pre_filter.py`
- Create: `tests/test_pipeline/test_pre_filter.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_pipeline/test_pre_filter.py`:

```python
from __future__ import annotations

from sqlmodel import Session, select

from knockknock.config.preferences import JobPreferences
from knockknock.db.enums import (
    CompanySizeBucket,
    JobSource,
    JobStatus,
    RejectionReason,
)
from knockknock.db.models import Company, JobApplication, JobApplicationEvent
from knockknock.filter.rules import RuleEngine
from knockknock.pipeline.pre_filter import PreFilterStage
from knockknock.pipeline.stage import StageContext


def _prefs() -> JobPreferences:
    return JobPreferences.model_validate(
        {
            "candidate": {"name": "x", "current_role": "x", "years_experience": 4, "location": "Bengaluru"},
            "target": {
                "locations": ["Bengaluru"],
                "titles_allow": ["Backend Engineer"],
                "titles_deny": ["Manager"],
                "seniority_allow": ["Mid"],
                "company_size_allow": ["SEED", "SERIES_A"],
            },
            "skills": {"must_have_any": ["Python"], "nice_to_have": []},
            "scoring": {
                "min_score_to_draft": 70, "weight_skill_match": 40,
                "weight_seniority_match": 25, "weight_location_match": 20,
                "weight_company_stage": 15,
            },
            "limits": {"daily_drafts_cap": 1, "hourly_discover_cap": 1, "gemini_pro_rpd_ceiling": 1},
            "sources": {
                "hn": {"enabled": True, "months_lookback": 1},
                "wellfound": {"enabled": False, "query": ""},
                "yc_waas": {"enabled": False, "query": ""},
                "greenhouse": {"enabled": False, "boards": []},
                "lever": {"enabled": False, "boards": []},
                "ashby": {"enabled": False, "boards": []},
            },
        }
    )


def _seed(session: Session) -> tuple[int, int]:
    co = Company(name="Acme", domain="acme.test", size_bucket=CompanySizeBucket.SERIES_A)
    session.add(co)
    session.flush()
    assert co.id
    good = JobApplication(
        company_id=co.id, source=JobSource.HN, source_job_id="hn-good",
        title="Backend Engineer", location="Bengaluru",
        apply_url="https://x.test/a", description="Python role",
        status=JobStatus.DISCOVERED,
    )
    bad = JobApplication(
        company_id=co.id, source=JobSource.HN, source_job_id="hn-bad",
        title="Engineering Manager", location="Bengaluru",
        apply_url="https://x.test/b", description="Python role",
        status=JobStatus.DISCOVERED,
    )
    session.add_all([good, bad])
    session.flush()
    assert good.id and bad.id
    return good.id, bad.id


def test_pre_filter_advances_and_rejects(db_session: Session) -> None:
    good_id, bad_id = _seed(db_session)
    engine = RuleEngine(prefs=_prefs(), blacklist_pattern=lambda n, d: None)
    stage = PreFilterStage(session=db_session, engine=engine)
    result = stage.run(StageContext(run_id=1))
    assert result.processed == 2
    assert result.advanced == 1
    assert result.rejected == 1

    good = db_session.get(JobApplication, good_id)
    bad = db_session.get(JobApplication, bad_id)
    assert good is not None and good.status == JobStatus.PRE_FILTERED
    assert bad is not None and bad.status == JobStatus.PRE_FILTER_REJECTED
    assert bad.rejection_reason == RejectionReason.ROLE_MISMATCH

    events = db_session.exec(select(JobApplicationEvent)).all()
    assert {e.to_status for e in events} == {JobStatus.PRE_FILTERED, JobStatus.PRE_FILTER_REJECTED}


def test_pre_filter_skips_already_processed_jobs(db_session: Session) -> None:
    good_id, _ = _seed(db_session)
    engine = RuleEngine(prefs=_prefs(), blacklist_pattern=lambda n, d: None)
    PreFilterStage(session=db_session, engine=engine).run(StageContext(run_id=1))
    # Second run should not re-process anything
    result = PreFilterStage(session=db_session, engine=engine).run(StageContext(run_id=2))
    assert result.processed == 0
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_pipeline/test_pre_filter.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement stage**

Create `src/knockknock/pipeline/pre_filter.py`:

```python
"""Pre-filter stage: hard rules + blacklist, mutate JobApplication.status."""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog
from sqlmodel import Session, select

from knockknock.db.enums import JobStatus
from knockknock.db.models import Company, JobApplication, JobApplicationEvent
from knockknock.filter.rules import RuleEngine
from knockknock.pipeline.stage import StageContext, StageResult

log = structlog.get_logger(__name__)


@dataclass
class PreFilterStage:
    session: Session
    engine: RuleEngine
    name: str = field(default="pre_filter", init=False)

    def run(self, ctx: StageContext) -> StageResult:
        rows = self.session.exec(
            select(JobApplication, Company)
            .join(Company, Company.id == JobApplication.company_id)
            .where(JobApplication.status == JobStatus.DISCOVERED)
        ).all()

        processed = advanced = rejected = errors = 0
        for job, company in rows:
            try:
                verdict = self.engine.evaluate(job, company)
            except Exception as exc:
                log.exception("pre_filter.eval_failed", job_id=job.id, error=str(exc))
                errors += 1
                continue
            processed += 1
            prev = job.status
            if verdict.passed:
                job.status = JobStatus.PRE_FILTERED
                advanced += 1
            else:
                job.status = JobStatus.PRE_FILTER_REJECTED
                job.rejection_reason = verdict.reason
                job.rejection_detail = verdict.detail
                rejected += 1
            self.session.add(job)
            self.session.add(
                JobApplicationEvent(
                    job_id=job.id, from_status=prev, to_status=job.status,
                    note=verdict.detail,
                )
            )
        self.session.flush()
        return StageResult(
            stage=self.name, processed=processed, advanced=advanced,
            rejected=rejected, errors=errors,
        )
```

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/test_pipeline/test_pre_filter.py -v
uv run mypy
```

Expected: PASS, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/knockknock/pipeline/pre_filter.py tests/test_pipeline/test_pre_filter.py
git commit -m "feat(pipeline): add pre_filter stage applying RuleEngine"
```

### Task 4.4: Wire pre-filter into CLI + sync blacklist on each run

**Files:**
- Modify: `src/knockknock/__main__.py`

- [ ] **Step 1: Modify CLI to sync blacklist and add the stage**

Replace the body of `pipeline_run` in `src/knockknock/__main__.py` with:

```python
@pipeline_app.command("run")
def pipeline_run(
    once: bool = typer.Option(False, "--once", help="Run a single pipeline pass and exit."),
) -> None:
    """Run the discovery → pre_filter pipeline (more stages added in later phases)."""
    from knockknock.config.preferences import load_preferences
    from knockknock.config.settings import Settings
    from knockknock.db.engine import make_sync_engine
    from knockknock.db.session import session_scope
    from knockknock.filter.blacklist import BlacklistMatcher, sync_blacklist_from_yaml
    from knockknock.filter.rules import RuleEngine
    from knockknock.logging import configure_logging
    from knockknock.observability.metrics import begin_run, finalize_run
    from knockknock.pipeline.discover import DiscoverStage
    from knockknock.pipeline.pre_filter import PreFilterStage
    from knockknock.pipeline.runner import PipelineRunner
    from knockknock.pipeline.stage import StageContext
    from knockknock.scrapers.registry import build_scrapers

    settings = Settings()
    configure_logging(level=settings.log_level, json=settings.runtime == "cloud")
    prefs = load_preferences(Path(settings.preferences_path))
    engine = make_sync_engine(settings.database_url)

    with session_scope(engine) as session:
        sync_blacklist_from_yaml(session, Path(settings.blacklist_path))
        matcher = BlacklistMatcher.load(session)
        rule_engine = RuleEngine(prefs=prefs, blacklist_pattern=matcher.match)

        run_id = begin_run(session)
        scrapers = build_scrapers(prefs)
        stages = [
            DiscoverStage(
                session=session, scrapers=scrapers,
                hourly_cap=prefs.limits.hourly_discover_cap,
            ),
            PreFilterStage(session=session, engine=rule_engine),
        ]
        summary = PipelineRunner(stages=stages).run_once(StageContext(run_id=run_id))
        finalize_run(session, run_id, summary.results)

    if not once:
        typer.echo("non-once mode not implemented yet; phase 12 will add scheduler integration")
```

- [ ] **Step 2: Verify lint + types**

```bash
uv run ruff check src tests
uv run mypy
uv run pytest -x
```

Expected: green.

- [ ] **Step 3: End-to-end smoke run against Neon**

```bash
export $(grep -v '^#' .env | xargs)
uv run knockknock pipeline run --once
```

Then in Neon SQL editor:

```sql
SELECT status, count(*) FROM job_applications GROUP BY status;
SELECT pattern, reason FROM companies_blacklist ORDER BY pattern;
SELECT to_status, count(*) FROM job_application_events GROUP BY to_status;
```

Expected:
- `job_applications` rows split between `PRE_FILTERED` and `PRE_FILTER_REJECTED` (zero remaining in `DISCOVERED` after pre_filter run).
- `companies_blacklist` shows the 4 seed entries from `config/blacklist.yaml`.
- `job_application_events` has rows for both terminal statuses.

- [ ] **Step 4: Commit**

```bash
git add src/knockknock/__main__.py
git commit -m "feat(cli): wire blacklist sync + pre_filter stage into pipeline run"
```

---

← [Index](00-index.md) · [Prev: phase-03-pipeline-hn.md](phase-03-pipeline-hn.md) · [Next: phase-05-gemini-score.md](phase-05-gemini-score.md)
