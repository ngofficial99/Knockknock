← [Index](00-index.md) · [Prev: phase-06-phonebook-enrich.md](phase-06-phonebook-enrich.md) · [Next: phase-08-email-draft.md](phase-08-email-draft.md)

## Phase 7: Resume Manifest, Selector, and Tailor Stage

**Outcome:** Every ENRICHED job picks the best-matching resume variant from `resumes/manifest.yaml` based on **tag overlap** with the job (title + description tokens). No LLM is involved — the selector is fully deterministic so reruns produce identical assignments. After this phase, jobs reach TAILORED with `resume_variant_key` populated.

### Task 7.1: Manifest schema + loader

**Files:**
- Create: `src/knockknock/resume/__init__.py`
- Create: `src/knockknock/resume/manifest.py`
- Create: `resumes/manifest.yaml` (seed)
- Create: `resumes/README.md` (instructions for user)
- Create: `tests/test_resume/__init__.py`
- Create: `tests/test_resume/test_manifest.py`

The manifest describes 5–10 PDFs the user has hand-built. Each variant declares:
- `key` — stable identifier (e.g. `backend-distributed`) stored in `job_applications.resume_variant_key`
- `pdf_path` — relative to `resumes/`
- `tags` — set of tag tokens (matching the dictionary at `tag_dictionary`)
- `priority` — tie-breaker when two variants match equally (higher wins)

A `tag_dictionary` maps **synonyms** → canonical tag, e.g. `k8s → kubernetes` so the selector treats them as equivalent when comparing job tokens to variant tags.

- [ ] **Step 1: Write the seed manifest**

Create `resumes/README.md`:

```markdown
# Resume variants

Place your hand-built PDFs in this folder and register them in `manifest.yaml`.

- `key` is a stable id; do not rename without updating any `email_drafts` that already point at it.
- `tags` should be lowercase canonical tokens. Use `tag_dictionary` to declare synonyms.
- `priority` breaks ties; higher beats lower. Use 100 for "most-targeted" and 50 for "generic".

PDFs are NEVER committed to git unless you want to share them publicly; this folder is in
`.gitignore` for the actual files, with only `manifest.yaml` and `README.md` tracked.
```

Edit `.gitignore` to add (if not already covered):

```
resumes/*.pdf
```

Create `resumes/manifest.yaml`:

```yaml
# Resume variant manifest. Loaded at startup; the tailor stage picks the
# best-matching variant for each job based on tag overlap.
#
# `tag_dictionary` maps job-token synonyms → canonical variant tags so we
# can compare "k8s in JD" against "kubernetes" tag without hardcoding equivalence.

tag_dictionary:
  k8s: kubernetes
  "distributed systems": distributed
  ml: llm
  "machine learning": llm
  golang: go
  nodejs: node
  typescript: ts
  reactjs: react
  postgres: postgresql
  pg: postgresql
  gcp: cloud
  aws: cloud
  azure: cloud

variants:
  - key: backend-distributed
    pdf_path: backend-distributed.pdf
    priority: 100
    tags: [backend, distributed, python, go, kafka, kubernetes, postgresql, microservices]

  - key: backend-llm
    pdf_path: backend-llm.pdf
    priority: 100
    tags: [backend, python, llm, fastapi, rag, langchain]

  - key: backend-fintech
    pdf_path: backend-fintech.pdf
    priority: 90
    tags: [backend, java, python, postgresql, kafka, payments, ledger]

  - key: backend-generic
    pdf_path: backend-generic.pdf
    priority: 50
    tags: [backend, python, postgresql, rest, api]
```

> **Note:** the user will replace these PDFs and tag sets with their actual variants before going live. The manifest must always contain at least one variant whose tag set is broad enough to match any backend role (so the selector never returns None for an in-scope job).

- [ ] **Step 2: Write failing loader tests**

Create `tests/test_resume/__init__.py` as empty file.

Create `tests/test_resume/test_manifest.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from knockknock.resume.manifest import ResumeManifest, load_manifest


def _write(tmp_path: Path, yaml_text: str) -> Path:
    p = tmp_path / "manifest.yaml"
    p.write_text(yaml_text)
    return p


def test_load_manifest_parses_seed() -> None:
    manifest = load_manifest(Path("resumes/manifest.yaml"))
    assert isinstance(manifest, ResumeManifest)
    assert len(manifest.variants) >= 1
    # Tags are lowercased canonical tokens.
    keys = {v.key for v in manifest.variants}
    assert "backend-generic" in keys


def test_load_manifest_normalises_tags_to_lowercase(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
tag_dictionary: {}
variants:
  - key: A
    pdf_path: a.pdf
    priority: 10
    tags: [Backend, PYTHON]
        """,
    )
    manifest = load_manifest(path)
    assert manifest.variants[0].tags == frozenset({"backend", "python"})


def test_load_manifest_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
tag_dictionary: {}
variants:
  - { key: x, pdf_path: x.pdf, priority: 1, tags: [a] }
  - { key: x, pdf_path: y.pdf, priority: 2, tags: [b] }
        """,
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_manifest(path)


def test_load_manifest_rejects_empty_variants(tmp_path: Path) -> None:
    path = _write(tmp_path, "tag_dictionary: {}\nvariants: []\n")
    with pytest.raises(ValueError, match="at least one"):
        load_manifest(path)


def test_canonicalise_tag_uses_dictionary(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
tag_dictionary:
  k8s: kubernetes
  golang: go
variants:
  - { key: g, pdf_path: g.pdf, priority: 1, tags: [backend] }
        """,
    )
    manifest = load_manifest(path)
    assert manifest.canonicalise_token("K8s") == "kubernetes"
    assert manifest.canonicalise_token("Golang") == "go"
    assert manifest.canonicalise_token("python") == "python"  # passes through
```

- [ ] **Step 3: Run tests to confirm failure**

```bash
uv run pytest tests/test_resume -v
```

Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 4: Implement the loader**

Create `src/knockknock/resume/__init__.py` as empty file.

Create `src/knockknock/resume/manifest.py`:

```python
"""Resume manifest loader: YAML → typed value objects."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class ResumeVariant:
    key: str
    pdf_path: str  # relative to resumes/
    tags: frozenset[str]
    priority: int


@dataclass(frozen=True, slots=True)
class ResumeManifest:
    """In-memory representation of `resumes/manifest.yaml`."""

    variants: tuple[ResumeVariant, ...]
    tag_dictionary: dict[str, str]  # synonym (lowercase) → canonical (lowercase)

    def canonicalise_token(self, token: str) -> str:
        """Apply synonym → canonical mapping; pass through unknown tokens."""
        normalised = token.strip().lower()
        return self.tag_dictionary.get(normalised, normalised)

    def variant_by_key(self, key: str) -> ResumeVariant | None:
        for variant in self.variants:
            if variant.key == key:
                return variant
        return None


def load_manifest(path: Path) -> ResumeManifest:
    """Parse a manifest YAML. Raise `ValueError` on structural problems."""
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"Manifest {path} must be a mapping at the top level.")

    raw_variants = raw.get("variants") or []
    if not isinstance(raw_variants, list) or not raw_variants:
        raise ValueError(f"Manifest {path} must declare at least one variant.")

    raw_dict = raw.get("tag_dictionary") or {}
    if not isinstance(raw_dict, dict):
        raise ValueError(f"Manifest {path}: `tag_dictionary` must be a mapping.")
    tag_dictionary: dict[str, str] = {
        str(syn).strip().lower(): str(canon).strip().lower()
        for syn, canon in raw_dict.items()
    }

    seen: set[str] = set()
    variants: list[ResumeVariant] = []
    for idx, entry in enumerate(raw_variants):
        if not isinstance(entry, dict):
            raise ValueError(f"Manifest {path}: variant #{idx} is not a mapping.")
        key = str(entry.get("key") or "").strip()
        if not key:
            raise ValueError(f"Manifest {path}: variant #{idx} missing 'key'.")
        if key in seen:
            raise ValueError(f"Manifest {path}: duplicate variant key {key!r}.")
        seen.add(key)
        pdf_path = str(entry.get("pdf_path") or "").strip()
        if not pdf_path:
            raise ValueError(f"Manifest {path}: variant {key!r} missing 'pdf_path'.")
        raw_tags = entry.get("tags") or []
        if not isinstance(raw_tags, list):
            raise ValueError(f"Manifest {path}: variant {key!r} 'tags' must be a list.")
        tags = frozenset(str(t).strip().lower() for t in raw_tags if str(t).strip())
        priority_raw = entry.get("priority", 0)
        try:
            priority = int(priority_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Manifest {path}: variant {key!r} priority not an int: {priority_raw!r}"
            ) from exc
        variants.append(
            ResumeVariant(key=key, pdf_path=pdf_path, tags=tags, priority=priority)
        )

    return ResumeManifest(variants=tuple(variants), tag_dictionary=tag_dictionary)
```

- [ ] **Step 5: Run tests until green**

```bash
uv run pytest tests/test_resume -v
```

Expected: PASS for all 5 tests.

- [ ] **Step 6: Commit**

```bash
git add resumes/ src/knockknock/resume/__init__.py src/knockknock/resume/manifest.py tests/test_resume .gitignore
git commit -m "feat(resume): manifest schema + loader"
```

### Task 7.2: Tokenizer + variant selector

**Files:**
- Create: `src/knockknock/resume/tokens.py`
- Create: `src/knockknock/resume/selector.py`
- Create: `tests/test_resume/test_tokens.py`
- Create: `tests/test_resume/test_selector.py`

The tokenizer extracts canonical tags from a job's title + description. It splits on word boundaries, lowercases, drops 1-char tokens and a small stoplist, then runs every multi-word phrase (e.g. `"distributed systems"`) through `manifest.canonicalise_token` first, falling back to single-token canonicalisation.

The selector scores each variant by:

```
score = |variant.tags ∩ job_tokens| + (variant.priority / 1000)
```

The priority is divided by 1000 so it only matters as a tie-breaker, never overwhelming actual tag overlap. Variants with zero overlap are skipped (we don't pick something completely unrelated). If no variant has any overlap, the selector falls back to the highest-priority variant — this is the "must always return something for an in-scope job" rule from the manifest seed.

- [ ] **Step 1: Write failing tokenizer tests**

Create `tests/test_resume/test_tokens.py`:

```python
from __future__ import annotations

from pathlib import Path

from knockknock.resume.manifest import load_manifest
from knockknock.resume.tokens import extract_job_tokens


def _manifest():
    return load_manifest(Path("resumes/manifest.yaml"))


def test_extract_tokens_lowercases_and_dedupes() -> None:
    manifest = _manifest()
    tokens = extract_job_tokens(
        manifest,
        title="Senior Backend Engineer",
        description="Python, Python, Kafka, AWS.",
    )
    assert "backend" in tokens
    assert "python" in tokens
    assert "kafka" in tokens
    assert "cloud" in tokens  # aws → cloud per seed dictionary


def test_extract_tokens_applies_multi_word_synonyms() -> None:
    manifest = _manifest()
    tokens = extract_job_tokens(
        manifest,
        title="Engineer",
        description="Experience with distributed systems and machine learning.",
    )
    assert "distributed" in tokens  # "distributed systems" → distributed
    assert "llm" in tokens  # "machine learning" → llm


def test_extract_tokens_drops_short_words_and_stopwords() -> None:
    manifest = _manifest()
    tokens = extract_job_tokens(
        manifest,
        title="A B C",
        description="The and we you with for at on in is are be to of by an or so",
    )
    assert tokens == frozenset()


def test_extract_tokens_handles_empty_inputs() -> None:
    manifest = _manifest()
    assert extract_job_tokens(manifest, title="", description="") == frozenset()
```

- [ ] **Step 2: Write failing selector tests**

Create `tests/test_resume/test_selector.py`:

```python
from __future__ import annotations

from knockknock.resume.manifest import ResumeManifest, ResumeVariant
from knockknock.resume.selector import select_variant


def _manifest(*variants: ResumeVariant) -> ResumeManifest:
    return ResumeManifest(variants=tuple(variants), tag_dictionary={})


def test_select_returns_highest_overlap() -> None:
    a = ResumeVariant("a", "a.pdf", frozenset({"python", "kafka"}), priority=10)
    b = ResumeVariant("b", "b.pdf", frozenset({"python", "kafka", "kubernetes"}), priority=10)
    manifest = _manifest(a, b)
    chosen = select_variant(
        manifest, frozenset({"python", "kafka", "kubernetes"})
    )
    assert chosen is not None
    assert chosen.key == "b"


def test_select_breaks_ties_by_priority() -> None:
    a = ResumeVariant("a", "a.pdf", frozenset({"python"}), priority=10)
    b = ResumeVariant("b", "b.pdf", frozenset({"python"}), priority=99)
    manifest = _manifest(a, b)
    chosen = select_variant(manifest, frozenset({"python"}))
    assert chosen is not None
    assert chosen.key == "b"


def test_select_falls_back_to_highest_priority_when_no_overlap() -> None:
    a = ResumeVariant("a", "a.pdf", frozenset({"x"}), priority=10)
    b = ResumeVariant("b", "b.pdf", frozenset({"y"}), priority=50)
    manifest = _manifest(a, b)
    chosen = select_variant(manifest, frozenset({"unrelated"}))
    assert chosen is not None
    assert chosen.key == "b"


def test_select_returns_none_when_no_variants() -> None:
    manifest = ResumeManifest(variants=(), tag_dictionary={})
    assert select_variant(manifest, frozenset({"python"})) is None


def test_select_is_deterministic_across_calls() -> None:
    a = ResumeVariant("a", "a.pdf", frozenset({"python"}), priority=10)
    b = ResumeVariant("b", "b.pdf", frozenset({"python"}), priority=10)
    manifest = _manifest(a, b)
    chosen1 = select_variant(manifest, frozenset({"python"}))
    chosen2 = select_variant(manifest, frozenset({"python"}))
    assert chosen1 is not None and chosen2 is not None
    assert chosen1.key == chosen2.key  # tie → stable order from manifest
```

- [ ] **Step 3: Run tests to confirm failure**

```bash
uv run pytest tests/test_resume -v
```

Expected: FAIL.

- [ ] **Step 4: Implement the tokenizer**

Create `src/knockknock/resume/tokens.py`:

```python
"""Canonical-tag extractor for job postings. No I/O. Deterministic."""

from __future__ import annotations

import re
from collections.abc import Iterable

from knockknock.resume.manifest import ResumeManifest

# Common low-signal words we don't want polluting the overlap calculation.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "and", "for", "with", "you", "your", "our", "we", "are", "is",
        "be", "to", "of", "an", "or", "in", "on", "at", "by", "as", "so",
        "this", "that", "it", "from", "into", "have", "has", "will", "shall",
        "any", "all", "but", "not", "no", "yes",
    }
)
_WORD_RE = re.compile(r"[a-z][a-z0-9+#.\-]{1,}", re.IGNORECASE)


def extract_job_tokens(
    manifest: ResumeManifest, *, title: str, description: str
) -> frozenset[str]:
    """Build the canonical-token set for a job, ready for variant scoring.

    Applies multi-word synonyms first (so "distributed systems" → "distributed"
    survives) then single-token canonicalisation.
    """
    text = f"{title or ''} {description or ''}".lower()
    if not text.strip():
        return frozenset()

    # Apply multi-word synonyms greedily before single-token scan.
    multi_word = {
        syn: canon
        for syn, canon in manifest.tag_dictionary.items()
        if " " in syn
    }
    canonical_from_multi: set[str] = set()
    for synonym, canonical in multi_word.items():
        if synonym in text:
            canonical_from_multi.add(canonical)
            # Erase the matched phrase to keep its tokens out of the single-word pass.
            text = text.replace(synonym, " ")

    tokens: set[str] = set(canonical_from_multi)
    for match in _WORD_RE.findall(text):
        word = match.lower()
        if word in _STOPWORDS:
            continue
        if len(word) < 2:
            continue
        canonical = manifest.canonicalise_token(word)
        if canonical in _STOPWORDS:
            continue
        tokens.add(canonical)
    return frozenset(tokens)


def stoplist() -> Iterable[str]:
    """Exposed for tests / inspection."""
    return _STOPWORDS
```

- [ ] **Step 5: Implement the selector**

Create `src/knockknock/resume/selector.py`:

```python
"""Deterministic variant selector — tag-overlap + priority tie-break."""

from __future__ import annotations

from knockknock.resume.manifest import ResumeManifest, ResumeVariant

_PRIORITY_TIE_BREAK_DENOMINATOR = 1000.0


def select_variant(
    manifest: ResumeManifest, job_tokens: frozenset[str]
) -> ResumeVariant | None:
    """Pick the best resume variant for a job, or None if manifest empty.

    Score = |variant.tags ∩ job_tokens| + priority/1000 so overlap dominates
    and priority is only a tie-breaker. If no variant has any overlap, the
    highest-priority variant is returned as a safe default (manifest authors
    must guarantee at least one "generic" variant — see resumes/README.md).
    Stable across runs: ties resolve by manifest order.
    """
    if not manifest.variants:
        return None

    scored: list[tuple[float, int, ResumeVariant]] = []
    for idx, variant in enumerate(manifest.variants):
        overlap = len(variant.tags & job_tokens)
        score = overlap + (variant.priority / _PRIORITY_TIE_BREAK_DENOMINATOR)
        scored.append((score, idx, variant))

    # Sort: highest score first; lower manifest index breaks score ties so result
    # is deterministic across calls.
    scored.sort(key=lambda triple: (-triple[0], triple[1]))
    best_score, _, best_variant = scored[0]

    # If best overlap is zero, fall back to the highest-priority variant.
    if int(best_score) == 0:
        return max(manifest.variants, key=lambda v: (v.priority, -manifest.variants.index(v)))

    return best_variant
```

- [ ] **Step 6: Run tests until green**

```bash
uv run pytest tests/test_resume -v
```

Expected: PASS for all tokenizer + selector tests.

- [ ] **Step 7: Type-check + lint + commit**

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
git add src/knockknock/resume/tokens.py src/knockknock/resume/selector.py tests/test_resume/test_tokens.py tests/test_resume/test_selector.py
git commit -m "feat(resume): tokenizer + tag-overlap variant selector"
```

### Task 7.3: Tailor stage

**Files:**
- Create: `src/knockknock/pipeline/tailor.py`
- Create: `tests/test_pipeline/test_tailor_stage.py`

The stage:
1. Selects ENRICHED jobs ordered by `score DESC, discovered_at ASC` (same priority order as enrich).
2. For each: build tokens via `extract_job_tokens(manifest, title=..., description=...)`, call `select_variant`, write `resume_variant_key` on the job, advance to TAILORED.
3. If the manifest is empty or selector returns None → mark ENRICH_FAILED is wrong (we're past enrich); instead, leave at ENRICHED, log error, count as `errors`. This is an operator problem (broken manifest), not a data problem.

- [ ] **Step 1: Write failing stage tests**

Create `tests/test_pipeline/test_tailor_stage.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session

from knockknock.db.enums import JobStatus, PipelineStage
from knockknock.db.models import Company, JobApplication, JobApplicationEvent
from knockknock.pipeline.tailor import TailorStage
from knockknock.resume.manifest import load_manifest


def _make_enriched_job(
    db_session: Session,
    *,
    title: str,
    description: str,
    score: int = 8,
    source_job_id: str = "j-1",
) -> JobApplication:
    company = Company(name="Acme", domain="acme.io")
    db_session.add(company)
    db_session.flush()
    job = JobApplication(
        company_id=company.id,
        title=title,
        location="Bangalore",
        description=description,
        apply_url="https://acme.io/jobs/1",
        source="HN_WHO_IS_HIRING",
        source_job_id=source_job_id,
        status=JobStatus.ENRICHED,
        score=score,
        discovered_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.flush()
    return job


def test_tailor_assigns_distributed_variant_for_distributed_job(db_session: Session) -> None:
    manifest = load_manifest(Path("resumes/manifest.yaml"))
    job = _make_enriched_job(
        db_session,
        title="Senior Backend Engineer",
        description="Build distributed systems with Python, Kafka, and Kubernetes on AWS.",
    )
    stage = TailorStage(manifest=manifest)
    result = stage.run(session=db_session, run_id=1)
    db_session.refresh(job)
    assert job.status == JobStatus.TAILORED
    assert job.resume_variant_key == "backend-distributed"
    assert result.advanced == 1


def test_tailor_assigns_llm_variant_for_llm_job(db_session: Session) -> None:
    manifest = load_manifest(Path("resumes/manifest.yaml"))
    job = _make_enriched_job(
        db_session,
        title="Applied AI Engineer",
        description="Build RAG pipelines with LangChain and FastAPI. ML and LLM experience required.",
        source_job_id="j-llm",
    )
    stage = TailorStage(manifest=manifest)
    stage.run(session=db_session, run_id=1)
    db_session.refresh(job)
    assert job.resume_variant_key == "backend-llm"


def test_tailor_falls_back_to_generic_for_unrelated_job(db_session: Session) -> None:
    manifest = load_manifest(Path("resumes/manifest.yaml"))
    job = _make_enriched_job(
        db_session,
        title="Junior Tester",
        description="Manual QA work on legacy mainframe systems.",
        source_job_id="j-generic",
    )
    stage = TailorStage(manifest=manifest)
    stage.run(session=db_session, run_id=1)
    db_session.refresh(job)
    # No overlap → highest-priority fallback.
    assert job.resume_variant_key in {"backend-distributed", "backend-llm"}
    # Either is fine; priority is 100 for both — selector picks the first by manifest order.
    assert job.status == JobStatus.TAILORED


def test_tailor_writes_audit_event(db_session: Session) -> None:
    manifest = load_manifest(Path("resumes/manifest.yaml"))
    job = _make_enriched_job(
        db_session,
        title="Backend Engineer",
        description="Python, Postgres, REST APIs",
        source_job_id="j-evt",
    )
    stage = TailorStage(manifest=manifest)
    stage.run(session=db_session, run_id=1)
    event = (
        db_session.query(JobApplicationEvent)
        .filter_by(job_application_id=job.id)
        .one()
    )
    assert event.stage == PipelineStage.TAILOR
    assert event.to_status == JobStatus.TAILORED
    assert event.from_status == JobStatus.ENRICHED


def test_tailor_processes_in_score_desc_order(db_session: Session) -> None:
    manifest = load_manifest(Path("resumes/manifest.yaml"))
    low = _make_enriched_job(
        db_session,
        title="Backend Engineer",
        description="python postgres",
        score=6,
        source_job_id="lo",
    )
    high = _make_enriched_job(
        db_session,
        title="Backend Engineer",
        description="python postgres",
        score=9,
        source_job_id="hi",
    )
    stage = TailorStage(manifest=manifest)
    stage.run(session=db_session, run_id=1)
    events = (
        db_session.query(JobApplicationEvent)
        .order_by(JobApplicationEvent.id.asc())
        .all()
    )
    assert events[0].job_application_id == high.id
    assert events[1].job_application_id == low.id
```

- [ ] **Step 2: Confirm failure**

```bash
uv run pytest tests/test_pipeline/test_tailor_stage.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement the stage**

Create `src/knockknock/pipeline/tailor.py`:

```python
"""Tailor stage: ENRICHED → TAILORED via deterministic resume selector."""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlmodel import Session, select

from knockknock.db.enums import JobStatus, PipelineStage
from knockknock.db.models import JobApplication, JobApplicationEvent
from knockknock.pipeline.stage import StageResult
from knockknock.resume.manifest import ResumeManifest
from knockknock.resume.selector import select_variant
from knockknock.resume.tokens import extract_job_tokens

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class TailorStage:
    """Assign a resume variant to every ENRICHED job.

    Pure-Python: no LLM, no I/O beyond the DB. Identical inputs produce
    identical outputs — safe to rerun.
    """

    manifest: ResumeManifest
    name: str = "tailor"

    def run(self, *, session: Session, run_id: int) -> StageResult:
        stmt = (
            select(JobApplication)
            .where(JobApplication.status == JobStatus.ENRICHED)
            .order_by(
                JobApplication.score.desc(),
                JobApplication.discovered_at.asc(),
            )
        )
        advanced = 0
        errors = 0
        for job in session.exec(stmt).all():
            tokens = extract_job_tokens(
                self.manifest,
                title=job.title or "",
                description=job.description or "",
            )
            chosen = select_variant(self.manifest, tokens)
            if chosen is None:
                # Operator config error — broken/empty manifest. Don't mutate the job.
                log.error(
                    "tailor.no_variant_selected",
                    job_id=job.id,
                    manifest_variants=len(self.manifest.variants),
                )
                errors += 1
                continue
            from_status = job.status
            job.resume_variant_key = chosen.key
            job.status = JobStatus.TAILORED
            session.add(
                JobApplicationEvent(
                    job_application_id=job.id,
                    pipeline_run_id=run_id,
                    stage=PipelineStage.TAILOR,
                    from_status=from_status,
                    to_status=JobStatus.TAILORED,
                    detail=f"variant={chosen.key}",
                )
            )
            session.flush()
            advanced += 1

        log.info("tailor.summary", advanced=advanced, errors=errors)
        return StageResult(
            stage=PipelineStage.TAILOR,
            advanced=advanced,
            rejected=0,
            errors=errors,
        )
```

- [ ] **Step 4: Run tests until green**

```bash
uv run pytest tests/test_pipeline/test_tailor_stage.py -v
```

Expected: PASS for all 5 tests.

- [ ] **Step 5: Run full suite + lint + commit**

```bash
uv run pytest -v
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
git add src/knockknock/pipeline/tailor.py tests/test_pipeline/test_tailor_stage.py
git commit -m "feat(pipeline): tailor stage with deterministic variant selection"
```

### Task 7.4: Wire tailor stage into CLI

**Files:**
- Modify: `src/knockknock/__main__.py`

- [ ] **Step 1: Load manifest once at CLI startup and pass to `TailorStage`**

Edit `src/knockknock/__main__.py`. Near the top of the `pipeline run --once` command, after preferences are loaded, add:

```python
from pathlib import Path

from knockknock.pipeline.tailor import TailorStage
from knockknock.resume.manifest import load_manifest


# ...inside the pipeline command...
manifest = load_manifest(Path("resumes/manifest.yaml"))
# ...later, after EnrichStage is appended...
stages.append(TailorStage(manifest=manifest))
```

> **Path note:** `Path("resumes/manifest.yaml")` is relative to the CWD. In Phase 11 we'll plumb manifest_path through `Settings` (as we did for `preferences_path`). For now we rely on running the CLI from the repo root.

- [ ] **Step 2: Smoke-run CLI**

```bash
uv run knockknock pipeline run --once
```

Expected: pipeline finishes; `tailor` stage reports `advanced=0` (no ENRICHED rows yet).

- [ ] **Step 3: Commit**

```bash
git add src/knockknock/__main__.py
git commit -m "feat(cli): wire tailor stage with manifest loader"
```

---

← [Index](00-index.md) · [Prev: phase-06-phonebook-enrich.md](phase-06-phonebook-enrich.md) · [Next: phase-08-email-draft.md](phase-08-email-draft.md)
