← [Index](00-index.md) · [Prev: phase-04-prefilter.md](phase-04-prefilter.md) · [Next: phase-06-phonebook-enrich.md](phase-06-phonebook-enrich.md)

## Phase 5: Gemini Client, Rate Limiter, and Score Stage

**Outcome:** Every PRE_FILTERED job is scored 1–10 by Gemini 2.5 Flash. Each call goes through a single rate-limiter that enforces RPM/TPM/RPD against the `gemini_call_logs` table, with a hard 500 RPD safety ceiling. Jobs above the threshold advance to SCORED; below threshold go to SCORE_REJECTED with `score_low` rejection reason. Pipeline run counts `gemini_calls_made` and `jobs_scored`.

### Task 5.1: Gemini limiter — quota math and exceptions

**Files:**
- Create: `src/knockknock/rate_limit/__init__.py`
- Create: `src/knockknock/rate_limit/gemini_limiter.py`
- Create: `tests/test_rate_limit/__init__.py`
- Create: `tests/test_rate_limit/test_gemini_limiter.py`

- [ ] **Step 1: Add limiter-specific exceptions to `exceptions.py`**

Edit `src/knockknock/exceptions.py` and append:

```python
class RpdExhaustedError(RateLimitedError):
    """Raised when the daily request budget for a Gemini model is exhausted."""


class SafetyCeilingError(RateLimitedError):
    """Raised when the cross-model daily safety ceiling has been hit."""
```

- [ ] **Step 2: Write failing limiter tests**

Create `tests/test_rate_limit/__init__.py` as empty file.

Create `tests/test_rate_limit/test_gemini_limiter.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from freezegun import freeze_time
from sqlmodel import Session

from knockknock.db.enums import GeminiModel, GeminiPurpose
from knockknock.db.models import GeminiCallLog
from knockknock.exceptions import RpdExhaustedError, SafetyCeilingError
from knockknock.rate_limit.gemini_limiter import (
    GeminiLimiter,
    LimiterConfig,
    ModelQuota,
)


def _log(
    session: Session,
    *,
    model: GeminiModel,
    when: datetime,
    success: bool = True,
) -> None:
    session.add(
        GeminiCallLog(
            model=model,
            purpose=GeminiPurpose.SCORE,
            tokens_input=10,
            tokens_output=20,
            latency_ms=100,
            success=success,
            created_at=when,
        )
    )
    session.flush()


def _config() -> LimiterConfig:
    return LimiterConfig(
        safety_ceiling_rpd=500,
        quotas={
            GeminiModel.FLASH_2_5: ModelQuota(rpm=15, tpm=1_000_000, rpd=1500),
            GeminiModel.PRO_2_5: ModelQuota(rpm=2, tpm=32_000, rpd=50),
        },
        pro_draft_soft_cap=40,
    )


@freeze_time("2026-05-28T12:00:00Z")
def test_check_passes_when_no_usage(db_session: Session) -> None:
    limiter = GeminiLimiter(db_session, _config())
    # Should not raise.
    limiter.check(GeminiModel.FLASH_2_5, GeminiPurpose.SCORE)


@freeze_time("2026-05-28T12:00:00Z")
def test_check_raises_rpd_when_model_rpd_exceeded(db_session: Session) -> None:
    now = datetime.now(timezone.utc)
    # 50 successful Pro calls today exhausts pro RPD.
    for i in range(50):
        _log(db_session, model=GeminiModel.PRO_2_5, when=now - timedelta(minutes=i))
    limiter = GeminiLimiter(db_session, _config())
    with pytest.raises(RpdExhaustedError):
        limiter.check(GeminiModel.PRO_2_5, GeminiPurpose.DRAFT_EMAIL)


@freeze_time("2026-05-28T12:00:00Z")
def test_check_raises_pro_soft_cap_for_drafts(db_session: Session) -> None:
    now = datetime.now(timezone.utc)
    for i in range(40):
        db_session.add(
            GeminiCallLog(
                model=GeminiModel.PRO_2_5,
                purpose=GeminiPurpose.DRAFT_EMAIL,
                tokens_input=10,
                tokens_output=20,
                latency_ms=100,
                success=True,
                created_at=now - timedelta(minutes=i),
            )
        )
    db_session.flush()
    limiter = GeminiLimiter(db_session, _config())
    with pytest.raises(RpdExhaustedError):
        limiter.check(GeminiModel.PRO_2_5, GeminiPurpose.DRAFT_EMAIL)
    # Regenerate purpose should still be permitted (uses the 10-call buffer).
    limiter.check(GeminiModel.PRO_2_5, GeminiPurpose.REGENERATE)


@freeze_time("2026-05-28T12:00:00Z")
def test_check_raises_safety_ceiling(db_session: Session) -> None:
    now = datetime.now(timezone.utc)
    # 500 calls (any mix) today hits the safety ceiling.
    for i in range(500):
        _log(db_session, model=GeminiModel.FLASH_2_5, when=now - timedelta(seconds=i))
    limiter = GeminiLimiter(db_session, _config())
    with pytest.raises(SafetyCeilingError):
        limiter.check(GeminiModel.FLASH_2_5, GeminiPurpose.SCORE)


@freeze_time("2026-05-28T12:00:00Z")
def test_check_only_counts_successful_calls_against_rpd(db_session: Session) -> None:
    now = datetime.now(timezone.utc)
    # 60 *failed* Pro calls should NOT exhaust the 50 RPD ceiling.
    for i in range(60):
        _log(db_session, model=GeminiModel.PRO_2_5, when=now - timedelta(minutes=i), success=False)
    limiter = GeminiLimiter(db_session, _config())
    limiter.check(GeminiModel.PRO_2_5, GeminiPurpose.DRAFT_EMAIL)


@freeze_time("2026-05-28T12:00:00Z")
def test_compute_rpm_wait_returns_seconds_when_window_full(db_session: Session) -> None:
    now = datetime.now(timezone.utc)
    # 15 Flash calls in the last 60s → next call must wait.
    for i in range(15):
        _log(db_session, model=GeminiModel.FLASH_2_5, when=now - timedelta(seconds=i * 2))
    limiter = GeminiLimiter(db_session, _config())
    wait = limiter.compute_rpm_wait_seconds(GeminiModel.FLASH_2_5)
    assert wait is not None
    assert 0 < wait <= 60


@freeze_time("2026-05-28T12:00:00Z")
def test_compute_rpm_wait_returns_none_when_window_clear(db_session: Session) -> None:
    limiter = GeminiLimiter(db_session, _config())
    assert limiter.compute_rpm_wait_seconds(GeminiModel.FLASH_2_5) is None
```

- [ ] **Step 3: Run tests to see them fail**

```bash
uv run pytest tests/test_rate_limit -v
```

Expected: FAIL (`ModuleNotFoundError: knockknock.rate_limit.gemini_limiter`).

- [ ] **Step 4: Implement the limiter**

Create `src/knockknock/rate_limit/__init__.py` as empty file.

Create `src/knockknock/rate_limit/gemini_limiter.py`:

```python
"""Single-threaded Gemini quota enforcer backed by `gemini_call_logs`."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import structlog
from sqlmodel import Session, select
from sqlalchemy import func

from knockknock.db.enums import GeminiModel, GeminiPurpose
from knockknock.db.models import GeminiCallLog
from knockknock.exceptions import RpdExhaustedError, SafetyCeilingError

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ModelQuota:
    rpm: int
    tpm: int
    rpd: int


@dataclass(frozen=True, slots=True)
class LimiterConfig:
    safety_ceiling_rpd: int
    quotas: dict[GeminiModel, ModelQuota]
    pro_draft_soft_cap: int = 40  # Drafts only; leaves headroom for regenerations.


@dataclass(slots=True)
class GeminiLimiter:
    """Pre-call gatekeeper for Gemini API budget.

    All checks are point-in-time queries against `gemini_call_logs` for the
    current process's DB session. Safe for single-process pipeline runs;
    multi-process callers need an external mutex (out of scope).
    """

    session: Session
    config: LimiterConfig
    _now_fn: "callable[[], datetime]" = field(default=lambda: datetime.now(timezone.utc))

    def check(self, model: GeminiModel, purpose: GeminiPurpose) -> None:
        """Raise if this call would breach any limit.

        Raises:
            SafetyCeilingError: cross-model daily call ceiling hit.
            RpdExhaustedError: per-model daily ceiling or pro draft soft cap hit.
        """
        now = self._now_fn()
        day_start = _day_start(now)

        total_today = self._count(day_start, model=None, success_only=True)
        if total_today >= self.config.safety_ceiling_rpd:
            log.error(
                "gemini.safety_ceiling_hit",
                total_today=total_today,
                ceiling=self.config.safety_ceiling_rpd,
            )
            raise SafetyCeilingError(
                f"Safety ceiling {self.config.safety_ceiling_rpd} hit ({total_today} calls today)."
            )

        quota = self.config.quotas[model]
        model_today = self._count(day_start, model=model, success_only=True)
        if model_today >= quota.rpd:
            raise RpdExhaustedError(
                f"{model.value} RPD {quota.rpd} exhausted ({model_today} calls today)."
            )

        # Pro draft soft cap protects against draft loop eating regenerate budget.
        if model is GeminiModel.PRO_2_5 and purpose is GeminiPurpose.DRAFT_EMAIL:
            draft_today = self._count_purpose(
                day_start, model=GeminiModel.PRO_2_5, purpose=GeminiPurpose.DRAFT_EMAIL
            )
            if draft_today >= self.config.pro_draft_soft_cap:
                raise RpdExhaustedError(
                    f"Pro draft soft cap {self.config.pro_draft_soft_cap} hit "
                    f"({draft_today} drafts today)."
                )

    def compute_rpm_wait_seconds(self, model: GeminiModel) -> float | None:
        """Return seconds to sleep so the next call doesn't exceed RPM, or None.

        Looks at the oldest call inside the trailing 60s window. If we're at
        capacity, the next call must wait until that call's timestamp + 60s.
        """
        now = self._now_fn()
        window_start = now - timedelta(seconds=60)
        quota = self.config.quotas[model]
        stmt = (
            select(GeminiCallLog.created_at)
            .where(GeminiCallLog.model == model)
            .where(GeminiCallLog.created_at >= window_start)
            .where(GeminiCallLog.success.is_(True))  # type: ignore[union-attr]
            .order_by(GeminiCallLog.created_at.asc())
        )
        rows = list(self.session.exec(stmt))
        if len(rows) < quota.rpm:
            return None
        # The oldest call inside the window dictates when slot frees up.
        oldest = rows[0]
        # Defensive: timestamps may be naive in some test paths; normalise.
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=timezone.utc)
        free_at = oldest + timedelta(seconds=60)
        delta = (free_at - now).total_seconds()
        return max(delta, 0.0)

    def _count(
        self, since: datetime, *, model: GeminiModel | None, success_only: bool
    ) -> int:
        stmt = select(func.count()).select_from(GeminiCallLog).where(
            GeminiCallLog.created_at >= since
        )
        if model is not None:
            stmt = stmt.where(GeminiCallLog.model == model)
        if success_only:
            stmt = stmt.where(GeminiCallLog.success.is_(True))  # type: ignore[union-attr]
        return int(self.session.exec(stmt).one())

    def _count_purpose(
        self, since: datetime, *, model: GeminiModel, purpose: GeminiPurpose
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(GeminiCallLog)
            .where(GeminiCallLog.created_at >= since)
            .where(GeminiCallLog.model == model)
            .where(GeminiCallLog.purpose == purpose)
            .where(GeminiCallLog.success.is_(True))  # type: ignore[union-attr]
        )
        return int(self.session.exec(stmt).one())


def _day_start(now: datetime) -> datetime:
    """UTC midnight floor. All Gemini quotas reset on UTC day."""
    return now.replace(hour=0, minute=0, second=0, microsecond=0)
```

- [ ] **Step 5: Run tests until green**

```bash
uv run pytest tests/test_rate_limit -v
```

Expected: PASS for all 7 tests.

- [ ] **Step 6: Type-check + lint**

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
```

- [ ] **Step 7: Commit**

```bash
git add src/knockknock/rate_limit src/knockknock/exceptions.py tests/test_rate_limit
git commit -m "feat(rate-limit): gemini RPM/TPM/RPD limiter with safety ceiling"
```

### Task 5.2: Gemini client wrapper

**Files:**
- Create: `src/knockknock/clients/__init__.py`
- Create: `src/knockknock/clients/gemini.py`
- Create: `tests/test_clients/__init__.py`
- Create: `tests/test_clients/test_gemini.py`

- [ ] **Step 1: Write failing client tests**

Create `tests/test_clients/__init__.py` as empty file.

Create `tests/test_clients/test_gemini.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

import pytest

from knockknock.clients.gemini import (
    GeminiClient,
    GeminiResponse,
    _UsageMetadata,
)
from knockknock.db.enums import GeminiModel


@dataclass
class _StubResponse:
    text: str
    usage_metadata: _UsageMetadata


class _StubGenAIModel:
    def __init__(self, response: _StubResponse) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def generate_content(self, *, contents: list[str], config: object) -> _StubResponse:
        self.calls.append({"contents": contents, "config": config})
        return self._response


class _StubGenAI:
    def __init__(self, response: _StubResponse) -> None:
        self.models = _StubModelsApi(response)


class _StubModelsApi:
    def __init__(self, response: _StubResponse) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def generate_content(self, *, model: str, contents: list[str], config: object):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self._response


def test_generate_returns_text_and_usage() -> None:
    response = _StubResponse(
        text=" {\"score\": 8} ",
        usage_metadata=_UsageMetadata(prompt_token_count=120, candidates_token_count=15),
    )
    stub = _StubGenAI(response)
    client = GeminiClient(stub, default_temperature=0.2)
    result = client.generate(
        model=GeminiModel.FLASH_2_5,
        system_instruction="be terse",
        user_prompt="score this job",
        response_mime_type="application/json",
    )
    assert isinstance(result, GeminiResponse)
    assert result.text == "{\"score\": 8}"
    assert result.tokens_input == 120
    assert result.tokens_output == 15
    assert stub.models.calls[0]["model"] == "gemini-2.5-flash"


def test_generate_strips_and_handles_missing_text() -> None:
    response = _StubResponse(
        text="",
        usage_metadata=_UsageMetadata(prompt_token_count=10, candidates_token_count=0),
    )
    stub = _StubGenAI(response)
    client = GeminiClient(stub)
    with pytest.raises(ValueError, match="empty"):
        client.generate(
            model=GeminiModel.FLASH_2_5,
            system_instruction="x",
            user_prompt="y",
            response_mime_type="application/json",
        )
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
uv run pytest tests/test_clients -v
```

Expected: FAIL (`ModuleNotFoundError: knockknock.clients.gemini`).

- [ ] **Step 3: Implement the client**

Create `src/knockknock/clients/__init__.py` as empty file.

Create `src/knockknock/clients/gemini.py`:

```python
"""Thin wrapper around `google-genai` SDK. No business logic; no logging of prompts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from knockknock.db.enums import GeminiModel
from knockknock.exceptions import ExternalServiceError

# Model-id mapping. Externalised so tests don't need to assert against SDK strings.
MODEL_IDS: dict[GeminiModel, str] = {
    GeminiModel.FLASH_2_5: "gemini-2.5-flash",
    GeminiModel.PRO_2_5: "gemini-2.5-pro",
}


@dataclass(frozen=True, slots=True)
class _UsageMetadata:
    prompt_token_count: int
    candidates_token_count: int


@dataclass(frozen=True, slots=True)
class GeminiResponse:
    text: str
    tokens_input: int
    tokens_output: int


class _GenAIClient(Protocol):
    """Subset of the `google.genai.Client` API we depend on.

    Kept minimal so tests can stub it without dragging in the SDK.
    """

    models: Any  # `.generate_content(model=..., contents=..., config=...)`


@dataclass(slots=True)
class GeminiClient:
    """Adapter over google-genai's `Client.models.generate_content`."""

    genai: _GenAIClient
    default_temperature: float = 0.2

    def generate(
        self,
        *,
        model: GeminiModel,
        system_instruction: str,
        user_prompt: str,
        response_mime_type: str,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> GeminiResponse:
        """Call Gemini; return text + token counts. Caller handles retries/limits."""
        # Late import so test stubs don't need google-genai installed.
        try:
            from google.genai import types  # type: ignore[import-not-found]
        except ImportError:  # pragma: no cover - exercised only when SDK absent
            types = None  # type: ignore[assignment]

        if types is None:
            config: Any = {
                "system_instruction": system_instruction,
                "temperature": temperature if temperature is not None else self.default_temperature,
                "response_mime_type": response_mime_type,
                "max_output_tokens": max_output_tokens,
            }
        else:
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=temperature if temperature is not None else self.default_temperature,
                response_mime_type=response_mime_type,
                max_output_tokens=max_output_tokens,
            )

        try:
            response = self.genai.models.generate_content(
                model=MODEL_IDS[model],
                contents=[user_prompt],
                config=config,
            )
        except Exception as exc:  # noqa: BLE001 - SDK exception classes vary.
            raise ExternalServiceError(f"Gemini API call failed: {exc}") from exc

        text = (response.text or "").strip()
        if not text:
            raise ValueError("Gemini returned empty response text.")

        usage = response.usage_metadata
        return GeminiResponse(
            text=text,
            tokens_input=int(getattr(usage, "prompt_token_count", 0) or 0),
            tokens_output=int(getattr(usage, "candidates_token_count", 0) or 0),
        )


def build_gemini_client(api_key: str) -> GeminiClient:
    """Construct a real Gemini client. Imported lazily for offline tests."""
    from google import genai  # type: ignore[import-not-found]

    return GeminiClient(genai=genai.Client(api_key=api_key))
```

- [ ] **Step 4: Run tests until green**

```bash
uv run pytest tests/test_clients -v
```

Expected: PASS.

- [ ] **Step 5: Type-check + lint + commit**

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
git add src/knockknock/clients tests/test_clients
git commit -m "feat(clients): gemini api wrapper"
```

### Task 5.3: Scoring prompt + parser

**Files:**
- Create: `src/knockknock/scoring/__init__.py`
- Create: `src/knockknock/scoring/prompts.py`
- Create: `src/knockknock/scoring/parser.py`
- Create: `tests/test_scoring/__init__.py`
- Create: `tests/test_scoring/test_parser.py`
- Create: `tests/test_scoring/test_prompts.py`

The prompt builds context once from preferences (skills, target seniority, company stage) plus per-job description; the parser converts the JSON response into a typed `JobScore` value object.

- [ ] **Step 1: Write failing tests**

Create `tests/test_scoring/__init__.py` as empty file.

Create `tests/test_scoring/test_parser.py`:

```python
from __future__ import annotations

import pytest

from knockknock.scoring.parser import JobScore, parse_score


def test_parse_score_happy_path() -> None:
    raw = """{
        "score": 8,
        "rationale": "Strong python + distributed systems match.",
        "matched_primary": ["python","kafka"],
        "matched_secondary": ["fastapi"],
        "matched_exposure": [],
        "matched_avoid": [],
        "seniority_match": true,
        "company_stage_match": true
    }"""
    parsed = parse_score(raw)
    assert isinstance(parsed, JobScore)
    assert parsed.score == 8
    assert "python" in parsed.matched_primary
    assert parsed.seniority_match is True


def test_parse_score_clamps_out_of_range() -> None:
    raw = '{"score": 11, "rationale": "x", "matched_primary": [], "matched_secondary": [], "matched_exposure": [], "matched_avoid": [], "seniority_match": false, "company_stage_match": false}'
    parsed = parse_score(raw)
    assert parsed.score == 10  # clamped


def test_parse_score_rejects_non_json() -> None:
    with pytest.raises(ValueError, match="parse"):
        parse_score("not-json")


def test_parse_score_rejects_missing_score() -> None:
    with pytest.raises(ValueError, match="score"):
        parse_score('{"rationale": "x"}')


def test_parse_score_strips_code_fence() -> None:
    raw = """```json
    {"score": 5, "rationale": "ok", "matched_primary": [], "matched_secondary": [], "matched_exposure": [], "matched_avoid": [], "seniority_match": true, "company_stage_match": true}
    ```"""
    parsed = parse_score(raw)
    assert parsed.score == 5
```

Create `tests/test_scoring/test_prompts.py`:

```python
from __future__ import annotations

from pathlib import Path

import yaml

from knockknock.config.preferences import load_preferences
from knockknock.scoring.prompts import build_score_prompt


def _prefs(tmp_path: Path) -> object:
    src = Path("config/job_preferences.yaml").read_text()
    p = tmp_path / "prefs.yaml"
    p.write_text(src)
    return load_preferences(p)


def test_build_score_prompt_contains_skills_and_role(tmp_path: Path) -> None:
    prefs = _prefs(tmp_path)
    prompt = build_score_prompt(
        prefs,
        company_name="Acme",
        role_title="Senior Backend Engineer",
        location="Bangalore",
        description="Build distributed Python services on AWS.",
    )
    assert "Acme" in prompt
    assert "Senior Backend Engineer" in prompt
    assert "python" in prompt.lower()
    # Avoid skills must be quoted in the rubric.
    assert ".net" in prompt or "salesforce" in prompt


def test_build_score_prompt_truncates_long_description(tmp_path: Path) -> None:
    prefs = _prefs(tmp_path)
    description = "X" * 50_000
    prompt = build_score_prompt(
        prefs,
        company_name="Acme",
        role_title="SDE",
        location="Bangalore",
        description=description,
    )
    assert len(prompt) < 20_000  # well under context budget
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
uv run pytest tests/test_scoring -v
```

Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement the parser**

Create `src/knockknock/scoring/__init__.py` as empty file.

Create `src/knockknock/scoring/parser.py`:

```python
"""Strict JSON parser for the Gemini scoring response."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


@dataclass(frozen=True, slots=True)
class JobScore:
    score: int  # 1..10
    rationale: str
    matched_primary: tuple[str, ...]
    matched_secondary: tuple[str, ...]
    matched_exposure: tuple[str, ...]
    matched_avoid: tuple[str, ...]
    seniority_match: bool
    company_stage_match: bool


def parse_score(raw: str) -> JobScore:
    """Convert a JSON-mode Gemini response into a `JobScore`.

    Tolerates markdown code fences (some models still emit them) but otherwise
    requires strict JSON with all expected keys. Score is clamped to [1, 10].
    """
    stripped = raw.strip()
    m = _FENCE_RE.match(stripped)
    if m:
        stripped = m.group(1).strip()

    try:
        data: Any = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not parse Gemini score response as JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Score payload must be a JSON object.")
    if "score" not in data:
        raise ValueError("Score payload missing required 'score' field.")

    score_raw = data["score"]
    try:
        score_int = int(score_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"score field is not an integer: {score_raw!r}") from exc
    score_int = max(1, min(10, score_int))

    return JobScore(
        score=score_int,
        rationale=str(data.get("rationale", "")).strip(),
        matched_primary=tuple(map(str, data.get("matched_primary", ()) or ())),
        matched_secondary=tuple(map(str, data.get("matched_secondary", ()) or ())),
        matched_exposure=tuple(map(str, data.get("matched_exposure", ()) or ())),
        matched_avoid=tuple(map(str, data.get("matched_avoid", ()) or ())),
        seniority_match=bool(data.get("seniority_match", False)),
        company_stage_match=bool(data.get("company_stage_match", False)),
    )
```

- [ ] **Step 4: Implement the prompt builder**

Create `src/knockknock/scoring/prompts.py`:

```python
"""Score prompt template + builder for Gemini 2.5 Flash.

Prompt template version is captured in `PROMPT_VERSION` so we can audit-trail
scores across template changes (`gemini_call_logs.purpose=SCORE` + git history).
"""

from __future__ import annotations

from knockknock.config.preferences import JobPreferences

PROMPT_VERSION = "score-v1"

SYSTEM_INSTRUCTION = (
    "You are a senior engineering recruiter screening backend jobs for a single "
    "candidate. Be ruthlessly objective. Output strict JSON only."
)

# Keep description excerpt small to leave headroom under the 1M TPM budget.
_DESCRIPTION_CHAR_LIMIT = 12_000

_TEMPLATE = """\
Candidate profile:
- Seniority: {seniority}
- Target locations: {locations}
- Primary skills (weight 3): {primary}
- Secondary skills (weight 2): {secondary}
- Exposure skills (weight 1): {exposure}
- Avoid skills (weight -2): {avoid}
- Company stage preference: {stage_preference}

Scoring rubric (1..10):
- 9-10: strong match across primary skills, seniority, stage.
- 7-8: solid backend role with most primaries; stage acceptable.
- 5-6: marginal fit, weak primary coverage or seniority mismatch.
- 1-4: poor fit (avoid skills present, wrong seniority, or unrelated role).

Job:
- Company: {company}
- Title: {title}
- Location: {location}
- Description (truncated):
\"\"\"
{description}
\"\"\"

Return JSON with EXACT keys:
{{
  "score": <1..10>,
  "rationale": "<one-sentence reason>",
  "matched_primary": [<skill,...>],
  "matched_secondary": [<skill,...>],
  "matched_exposure": [<skill,...>],
  "matched_avoid": [<skill,...>],
  "seniority_match": <bool>,
  "company_stage_match": <bool>
}}
"""


def build_score_prompt(
    prefs: JobPreferences,
    *,
    company_name: str,
    role_title: str,
    location: str,
    description: str,
) -> str:
    """Render the score prompt for a single job."""
    desc = (description or "").strip()
    if len(desc) > _DESCRIPTION_CHAR_LIMIT:
        desc = desc[:_DESCRIPTION_CHAR_LIMIT] + "\n…[truncated]"

    return _TEMPLATE.format(
        seniority=", ".join(prefs.candidate.seniority_levels),
        locations=", ".join(prefs.target.location_patterns),
        primary=", ".join(prefs.skills.primary),
        secondary=", ".join(prefs.skills.secondary),
        exposure=", ".join(prefs.skills.exposure),
        avoid=", ".join(prefs.skills.avoid),
        stage_preference=", ".join(s.value for s in prefs.target.company_size_allow),
        company=company_name,
        title=role_title,
        location=location or "unspecified",
        description=desc or "(no description)",
    )
```

> **Note on preferences shape:** this template references `prefs.candidate.seniority_levels`, `prefs.target.location_patterns`, `prefs.target.company_size_allow`, `prefs.skills.{primary,secondary,exposure,avoid}`. These attribute names must match what Phase 2 declared. If the names differ, fix the prompt template (do not invent new prefs fields).

- [ ] **Step 5: Run tests until green**

```bash
uv run pytest tests/test_scoring -v
```

Expected: PASS for all 5 tests.

- [ ] **Step 6: Type-check + lint**

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
```

- [ ] **Step 7: Commit**

```bash
git add src/knockknock/scoring tests/test_scoring
git commit -m "feat(scoring): prompt template + JSON parser for Flash scoring"
```

### Task 5.4: Score stage — wire client + limiter + persistence

**Files:**
- Create: `src/knockknock/pipeline/score.py`
- Create: `tests/test_pipeline/test_score_stage.py`

The stage:
1. Selects PRE_FILTERED jobs ordered by `discovered_at ASC` (FIFO).
2. For each job: pre-check limiter; call Gemini Flash; parse response; write `GeminiCallLog` (success or fail); update job row (`score`, `score_rationale`, `status=SCORED` or `SCORE_REJECTED`); write `JobApplicationEvent`.
3. On `RpdExhaustedError`/`SafetyCeilingError`: stop processing, mark run PARTIAL.
4. On per-job parser/API failure: log error_class, leave job at PRE_FILTERED, continue.

- [ ] **Step 1: Write failing stage tests**

Create `tests/test_pipeline/test_score_stage.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
from sqlmodel import Session

from knockknock.clients.gemini import GeminiResponse
from knockknock.config.preferences import load_preferences
from knockknock.db.enums import (
    GeminiModel,
    GeminiPurpose,
    JobStatus,
    PipelineStage,
    RejectionReason,
)
from knockknock.db.models import (
    Company,
    GeminiCallLog,
    JobApplication,
    JobApplicationEvent,
)
from knockknock.exceptions import RpdExhaustedError
from knockknock.pipeline.score import ScoreStage
from knockknock.rate_limit.gemini_limiter import GeminiLimiter, LimiterConfig, ModelQuota


@dataclass
class _StubGemini:
    text: str
    tokens_in: int = 100
    tokens_out: int = 20
    calls: int = 0

    def generate(self, **_: object) -> GeminiResponse:  # type: ignore[no-untyped-def]
        self.calls += 1
        return GeminiResponse(text=self.text, tokens_input=self.tokens_in, tokens_output=self.tokens_out)


def _limiter(db_session: Session) -> GeminiLimiter:
    return GeminiLimiter(
        db_session,
        LimiterConfig(
            safety_ceiling_rpd=500,
            quotas={
                GeminiModel.FLASH_2_5: ModelQuota(rpm=999, tpm=10_000_000, rpd=1500),
                GeminiModel.PRO_2_5: ModelQuota(rpm=2, tpm=32_000, rpd=50),
            },
        ),
    )


def _make_job(db_session: Session, *, title: str = "Backend Engineer") -> JobApplication:
    company = Company(name="Acme", domain="acme.com")
    db_session.add(company)
    db_session.flush()
    job = JobApplication(
        company_id=company.id,
        title=title,
        location="Bangalore",
        description="Build Python services on AWS with Kafka.",
        apply_url="https://acme.com/jobs/1",
        source="HN_WHO_IS_HIRING",
        source_job_id="hn-1",
        status=JobStatus.PRE_FILTERED,
        discovered_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.flush()
    return job


def test_score_stage_advances_high_score(db_session: Session, tmp_path) -> None:
    prefs = load_preferences("config/job_preferences.yaml")
    job = _make_job(db_session)
    gemini = _StubGemini(text='{"score": 8, "rationale": "great fit", "matched_primary": ["python"], "matched_secondary": [], "matched_exposure": [], "matched_avoid": [], "seniority_match": true, "company_stage_match": true}')
    stage = ScoreStage(prefs=prefs, gemini=gemini, limiter=_limiter(db_session))
    result = stage.run(session=db_session, run_id=1)
    db_session.refresh(job)
    assert job.status == JobStatus.SCORED
    assert job.score == 8
    assert "great fit" in (job.score_rationale or "")
    assert result.advanced == 1
    log = db_session.query(GeminiCallLog).first()
    assert log is not None
    assert log.purpose == GeminiPurpose.SCORE
    assert log.success is True


def test_score_stage_rejects_low_score(db_session: Session) -> None:
    prefs = load_preferences("config/job_preferences.yaml")
    job = _make_job(db_session)
    gemini = _StubGemini(text='{"score": 3, "rationale": "weak", "matched_primary": [], "matched_secondary": [], "matched_exposure": [], "matched_avoid": [".net"], "seniority_match": false, "company_stage_match": true}')
    stage = ScoreStage(prefs=prefs, gemini=gemini, limiter=_limiter(db_session))
    stage.run(session=db_session, run_id=1)
    db_session.refresh(job)
    assert job.status == JobStatus.SCORE_REJECTED
    assert job.score == 3
    event = db_session.query(JobApplicationEvent).filter_by(job_application_id=job.id).first()
    assert event is not None
    assert event.stage == PipelineStage.SCORE
    assert event.rejection_reason == RejectionReason.SCORE_LOW


def test_score_stage_handles_parser_failure(db_session: Session) -> None:
    prefs = load_preferences("config/job_preferences.yaml")
    job = _make_job(db_session)
    gemini = _StubGemini(text="not-json")
    stage = ScoreStage(prefs=prefs, gemini=gemini, limiter=_limiter(db_session))
    result = stage.run(session=db_session, run_id=1)
    db_session.refresh(job)
    # Job stays at PRE_FILTERED, will be retried next run.
    assert job.status == JobStatus.PRE_FILTERED
    assert result.errors == 1
    log = db_session.query(GeminiCallLog).first()
    assert log is not None
    assert log.success is False
    assert log.error_class == "ValueError"


def test_score_stage_halts_on_rpd_exhausted(db_session: Session) -> None:
    prefs = load_preferences("config/job_preferences.yaml")
    _make_job(db_session, title="Job A")
    _make_job(db_session, title="Job B")

    class _RpdLimiter:
        def check(self, *_a: object, **_kw: object) -> None:
            raise RpdExhaustedError("flash exhausted")

        def compute_rpm_wait_seconds(self, *_a: object, **_kw: object) -> float | None:
            return None

    gemini = _StubGemini(text="never-called")
    stage = ScoreStage(prefs=prefs, gemini=gemini, limiter=_RpdLimiter())  # type: ignore[arg-type]
    result = stage.run(session=db_session, run_id=1)
    assert gemini.calls == 0
    assert result.advanced == 0
    assert result.halted_reason == "rpd_exhausted"
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
uv run pytest tests/test_pipeline/test_score_stage.py -v
```

Expected: FAIL (`ModuleNotFoundError: knockknock.pipeline.score`).

- [ ] **Step 3: Implement the stage**

Create `src/knockknock/pipeline/score.py`:

```python
"""Score stage: PRE_FILTERED → SCORED / SCORE_REJECTED via Gemini Flash."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

import structlog
from sqlmodel import Session, select

from knockknock.clients.gemini import GeminiResponse
from knockknock.config.preferences import JobPreferences
from knockknock.db.enums import (
    GeminiModel,
    GeminiPurpose,
    JobStatus,
    PipelineStage,
    RejectionReason,
)
from knockknock.db.models import (
    Company,
    GeminiCallLog,
    JobApplication,
    JobApplicationEvent,
)
from knockknock.exceptions import RateLimitedError
from knockknock.pipeline.stage import StageResult
from knockknock.scoring.parser import JobScore, parse_score
from knockknock.scoring.prompts import PROMPT_VERSION, SYSTEM_INSTRUCTION, build_score_prompt

log = structlog.get_logger(__name__)


class _GeminiLike(Protocol):
    def generate(
        self,
        *,
        model: GeminiModel,
        system_instruction: str,
        user_prompt: str,
        response_mime_type: str,
    ) -> GeminiResponse: ...


class _LimiterLike(Protocol):
    def check(self, model: GeminiModel, purpose: GeminiPurpose) -> None: ...
    def compute_rpm_wait_seconds(self, model: GeminiModel) -> float | None: ...


@dataclass(slots=True)
class ScoreStage:
    """Score PRE_FILTERED jobs FIFO; halt cleanly on rate-limit exhaustion."""

    prefs: JobPreferences
    gemini: _GeminiLike
    limiter: _LimiterLike
    sleep_fn: "callable[[float], None]" = field(default=time.sleep)
    _model: GeminiModel = field(default=GeminiModel.FLASH_2_5)

    name = "score"

    def run(self, *, session: Session, run_id: int) -> StageResult:  # noqa: PLR0915
        threshold = self.prefs.scoring.threshold
        stmt = (
            select(JobApplication, Company)
            .join(Company, Company.id == JobApplication.company_id)
            .where(JobApplication.status == JobStatus.PRE_FILTERED)
            .order_by(JobApplication.discovered_at.asc())
        )
        advanced = 0
        rejected = 0
        errors = 0
        halted_reason: str | None = None

        for job, company in session.exec(stmt).all():
            # Pre-call: enforce daily/safety quotas BEFORE consuming a call.
            try:
                self.limiter.check(self._model, GeminiPurpose.SCORE)
            except RateLimitedError as exc:
                log.warning("score.halted", reason=str(exc))
                halted_reason = "rpd_exhausted"
                break

            # Soft RPM smoothing.
            wait = self.limiter.compute_rpm_wait_seconds(self._model)
            if wait and wait > 0:
                self.sleep_fn(min(wait + 0.1, 65.0))

            prompt = build_score_prompt(
                self.prefs,
                company_name=company.name,
                role_title=job.title,
                location=job.location or "",
                description=job.description or "",
            )

            started = time.monotonic()
            try:
                response = self.gemini.generate(
                    model=self._model,
                    system_instruction=SYSTEM_INSTRUCTION,
                    user_prompt=prompt,
                    response_mime_type="application/json",
                )
                parsed: JobScore = parse_score(response.text)
                latency_ms = int((time.monotonic() - started) * 1000)
                session.add(
                    GeminiCallLog(
                        job_application_id=job.id,
                        model=self._model,
                        purpose=GeminiPurpose.SCORE,
                        tokens_input=response.tokens_input,
                        tokens_output=response.tokens_output,
                        latency_ms=latency_ms,
                        success=True,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - we want all failures captured.
                latency_ms = int((time.monotonic() - started) * 1000)
                session.add(
                    GeminiCallLog(
                        job_application_id=job.id,
                        model=self._model,
                        purpose=GeminiPurpose.SCORE,
                        tokens_input=0,
                        tokens_output=0,
                        latency_ms=latency_ms,
                        success=False,
                        error_class=type(exc).__name__,
                    )
                )
                errors += 1
                log.warning(
                    "score.call_failed",
                    job_id=job.id,
                    error_class=type(exc).__name__,
                    error=str(exc),
                )
                session.flush()
                continue

            # Persist verdict.
            job.score = parsed.score
            job.score_rationale = parsed.rationale
            if parsed.score >= threshold:
                job.status = JobStatus.SCORED
                rejection_reason: RejectionReason | None = None
                advanced += 1
            else:
                job.status = JobStatus.SCORE_REJECTED
                rejection_reason = RejectionReason.SCORE_LOW
                rejected += 1

            session.add(
                JobApplicationEvent(
                    job_application_id=job.id,
                    pipeline_run_id=run_id,
                    stage=PipelineStage.SCORE,
                    from_status=JobStatus.PRE_FILTERED,
                    to_status=job.status,
                    rejection_reason=rejection_reason,
                    detail=f"score={parsed.score} prompt={PROMPT_VERSION}",
                )
            )
            session.flush()

        log.info(
            "score.summary",
            advanced=advanced,
            rejected=rejected,
            errors=errors,
            halted=halted_reason is not None,
        )
        return StageResult(
            stage=PipelineStage.SCORE,
            advanced=advanced,
            rejected=rejected,
            errors=errors,
            halted_reason=halted_reason,
        )
```

> **Note on `StageResult` shape:** Phase 3 introduced `StageResult` with fields `stage/advanced/rejected/errors`. This task adds a new optional `halted_reason: str | None = None` field. If you didn't include it in Phase 3, edit `pipeline/stage.py` now to add it (default `None`); update any earlier tests that construct `StageResult` to pass through `halted_reason=None` implicitly. Run all prior tests after the edit to confirm nothing breaks.

- [ ] **Step 4: Run stage tests until green**

```bash
uv run pytest tests/test_pipeline/test_score_stage.py -v
```

Expected: PASS for all 4 tests.

- [ ] **Step 5: Run the full suite to verify no regressions**

```bash
uv run pytest -v
```

Expected: PASS for all tests including pre-existing ones from Phases 0–4.

- [ ] **Step 6: Type-check + lint + commit**

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
git add src/knockknock/pipeline/score.py tests/test_pipeline/test_score_stage.py
# If you had to update pipeline/stage.py for halted_reason:
git add src/knockknock/pipeline/stage.py
git commit -m "feat(pipeline): score stage with gemini flash + rate limiter"
```

### Task 5.5: Wire score stage into CLI

**Files:**
- Modify: `src/knockknock/__main__.py`

- [ ] **Step 1: Update CLI to construct the limiter, client, and score stage**

Edit `src/knockknock/__main__.py`. Inside the `pipeline run --once` command (built in Phase 3 / 4), after the `PreFilterStage` is appended, add:

```python
from knockknock.clients.gemini import build_gemini_client
from knockknock.db.enums import GeminiModel
from knockknock.pipeline.score import ScoreStage
from knockknock.rate_limit.gemini_limiter import GeminiLimiter, LimiterConfig, ModelQuota


# ...inside the pipeline command, after PreFilterStage is added...
secrets = build_secrets_client(settings)
gemini_api_key = secrets.get("gemini-api-key")
gemini_client = build_gemini_client(gemini_api_key)
limiter_config = LimiterConfig(
    safety_ceiling_rpd=prefs.limits.daily_rpd_safety_cap,
    quotas={
        GeminiModel.FLASH_2_5: ModelQuota(rpm=15, tpm=1_000_000, rpd=1500),
        GeminiModel.PRO_2_5: ModelQuota(rpm=2, tpm=32_000, rpd=50),
    },
    pro_draft_soft_cap=prefs.limits.daily_pro_draft_cap,
)
with session_scope(engine) as score_session:
    limiter = GeminiLimiter(score_session, limiter_config)
    stages.append(ScoreStage(prefs=prefs, gemini=gemini_client, limiter=limiter))
```

> **Refactor note:** as more stages are added, the CLI is getting clumsy. We'll extract a `build_pipeline(settings, prefs, session)` factory in Phase 11. For now keep stages inline so flow is obvious.

- [ ] **Step 2: Smoke-run CLI against an empty DB (no API call expected, no PRE_FILTERED rows)**

Set `KNOCKKNOCK_SECRET_GEMINI_API_KEY=stub-not-used` in your local `.env` to satisfy the secrets fetch.

```bash
uv run knockknock pipeline run --once
```

Expected: no error; `score` stage reports `advanced=0 rejected=0 errors=0`.

- [ ] **Step 3: Commit**

```bash
git add src/knockknock/__main__.py
git commit -m "feat(cli): wire score stage with gemini client + limiter"
```

### Task 5.6: Integration smoke test (optional, manual)

This step is manual because it consumes real Gemini Flash quota (cheap but non-zero).

- [ ] **Step 1: Seed one fake PRE_FILTERED job**

```bash
uv run python -c "
from knockknock.config.settings import Settings
from knockknock.db.engine import make_sync_engine
from knockknock.db.session import session_scope
from knockknock.db.models import Company, JobApplication
from knockknock.db.enums import JobStatus
from datetime import datetime, timezone

engine = make_sync_engine(Settings().database_url)
with session_scope(engine) as s:
    c = Company(name='Acme', domain='acme.com')
    s.add(c); s.flush()
    s.add(JobApplication(
        company_id=c.id, title='Senior Backend Engineer',
        location='Bangalore', description='Python, Kafka, AWS, distributed systems.',
        apply_url='https://acme.com/jobs/1', source='HN_WHO_IS_HIRING',
        source_job_id='manual-1', status=JobStatus.PRE_FILTERED,
        discovered_at=datetime.now(timezone.utc),
    ))
"
```

- [ ] **Step 2: Run the pipeline**

```bash
uv run knockknock pipeline run --once
```

Expected: log lines show 1 Gemini call, job advances to `SCORED` (or `SCORE_REJECTED` for a low-scoring stub).

- [ ] **Step 3: Inspect `gemini_call_logs`**

```bash
uv run python -c "
from knockknock.config.settings import Settings
from knockknock.db.engine import make_sync_engine
from knockknock.db.session import session_scope
from knockknock.db.models import GeminiCallLog
engine = make_sync_engine(Settings().database_url)
with session_scope(engine) as s:
    for row in s.query(GeminiCallLog).all():
        print(row.model, row.purpose, row.tokens_input, row.tokens_output, row.success)
"
```

Expected: one row, `FLASH_2_5 SCORE <tokens>... True`.

- [ ] **Step 4: Clean up manually (do NOT commit DB state)**

```bash
uv run python -c "
from knockknock.config.settings import Settings
from knockknock.db.engine import make_sync_engine
from knockknock.db.session import session_scope
from knockknock.db.models import JobApplication, Company, GeminiCallLog, JobApplicationEvent
engine = make_sync_engine(Settings().database_url)
with session_scope(engine) as s:
    s.query(JobApplicationEvent).delete()
    s.query(GeminiCallLog).delete()
    s.query(JobApplication).delete()
    s.query(Company).delete()
"
```

---

← [Index](00-index.md) · [Prev: phase-04-prefilter.md](phase-04-prefilter.md) · [Next: phase-06-phonebook-enrich.md](phase-06-phonebook-enrich.md)
