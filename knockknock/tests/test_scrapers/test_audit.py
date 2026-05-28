"""Tests for the audit harness.

We use stub scrapers that yield deterministic mixes of good/bad jobs so
we can verify each heuristic finding fires exactly when it should.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from knockknock.db.enums import CompanySizeBucket, JobSource
from knockknock.scrapers.audit import (
    NULL_RATE_DRIFT_THRESHOLD,
    audit_scraper,
)
from knockknock.scrapers.base import ScrapedJob


def _job(
    *,
    sid: str = "1",
    title: str = "Backend Engineer",
    location: str = "Bengaluru",
    company_name: str = "Acme",
    company_domain: str = "acme.test",
    apply_url: str = "https://acme.test/jobs/1",
    description: str = "Build stuff",
    posted_at: datetime | None = None,
    salary_min: int | None = None,
    salary_max: int | None = None,
    salary_currency: str | None = None,
    salary_period: str | None = None,
    salary_raw: str | None = None,
) -> ScrapedJob:
    return ScrapedJob(
        source=JobSource.HN,
        source_job_id=sid,
        company_name=company_name,
        company_domain=company_domain,
        company_size_bucket=CompanySizeBucket.UNKNOWN,
        title=title,
        location=location,
        apply_url=apply_url,
        description=description,
        posted_at=posted_at if posted_at is not None else datetime.now(UTC),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        salary_period=salary_period,
        salary_raw=salary_raw,
    )


class _StubScraper:
    """Stable stub: every call to fetch() yields the same fixed jobs."""

    source = JobSource.HN

    def __init__(self, jobs: list[ScrapedJob]) -> None:
        self._jobs = jobs

    def fetch(self) -> Iterator[ScrapedJob]:
        yield from self._jobs


class _DriftScraper:
    """Stub whose fetch() output changes across runs (simulates regression)."""

    source = JobSource.HN

    def __init__(self, run_batches: list[list[ScrapedJob]]) -> None:
        self._batches = run_batches
        self._call_index = 0

    def fetch(self) -> Iterator[ScrapedJob]:
        batch = self._batches[self._call_index]
        self._call_index += 1
        yield from batch


def test_audit_emits_per_run_artifacts(tmp_path: Path) -> None:
    """Each run should produce a JSONL dump, plus a Markdown + JSON report at the end."""
    scraper = _StubScraper([_job(sid="1"), _job(sid="2")])
    report = audit_scraper(scraper, runs=2, output_dir=tmp_path)

    assert len(report.runs) == 2
    jsonl_files = list(tmp_path.glob("hn-run*.jsonl"))
    assert len(jsonl_files) == 2
    # Each JSONL has exactly the expected number of rows.
    for jf in jsonl_files:
        rows = jf.read_text().strip().splitlines()
        assert len(rows) == 2
        # And each row deserialises into something with our expected keys.
        sample = json.loads(rows[0])
        assert sample["source"] == "HN"
        assert "salary_min" in sample
        assert "company_domain" in sample

    md_files = list(tmp_path.glob("hn-*-report.md"))
    json_files = list(tmp_path.glob("hn-*-report.json"))
    assert len(md_files) == 1
    assert len(json_files) == 1
    md = md_files[0].read_text()
    assert "Scraper Audit" in md
    assert "null rate" in md


def test_audit_flags_title_that_looks_like_location(tmp_path: Path) -> None:
    scraper = _StubScraper(
        [_job(sid="1", title="Berlin, Germany"), _job(sid="2", title="Backend Engineer")]
    )
    report = audit_scraper(scraper, runs=1, output_dir=tmp_path)

    findings = report.runs[0].findings
    title_findings = [f for f in findings if f.field == "title"]
    assert title_findings, "expected a title finding for a location-looking value"
    assert "location" in title_findings[0].message.lower()


def test_audit_flags_malformed_url(tmp_path: Path) -> None:
    # Use a stub that bypasses the ScrapedJob __post_init__ URL check by
    # constructing ScrapedJob with a valid URL and then mutating via tag.
    # Easier: just construct with a URL that passes __post_init__ (non-empty)
    # but fails the audit's stricter scheme/netloc check.
    job = _job(sid="1", apply_url="not-a-real-url")
    scraper = _StubScraper([job])
    report = audit_scraper(scraper, runs=1, output_dir=tmp_path)

    url_findings = [f for f in report.runs[0].findings if f.field == "apply_url"]
    assert url_findings
    assert url_findings[0].severity == "ERROR"


def test_audit_flags_duplicate_source_ids(tmp_path: Path) -> None:
    scraper = _StubScraper([_job(sid="dup"), _job(sid="dup"), _job(sid="unique")])
    report = audit_scraper(scraper, runs=1, output_dir=tmp_path)

    dup_findings = [f for f in report.runs[0].findings if f.field == "source_job_id"]
    assert dup_findings
    assert dup_findings[0].severity == "ERROR"
    assert "dup" in dup_findings[0].examples


def test_audit_flags_generic_redirector_domain(tmp_path: Path) -> None:
    scraper = _StubScraper(
        [_job(sid="1", company_domain="linkedin.com"), _job(sid="2", company_domain="acme.test")]
    )
    report = audit_scraper(scraper, runs=1, output_dir=tmp_path)

    domain_findings = [f for f in report.runs[0].findings if f.field == "company_domain"]
    assert domain_findings
    assert "redirector" in domain_findings[0].message.lower()


def test_audit_flags_stale_posted_at(tmp_path: Path) -> None:
    old = datetime.now(UTC) - timedelta(days=400)
    scraper = _StubScraper([_job(sid=str(i), posted_at=old) for i in range(5)])
    report = audit_scraper(scraper, runs=1, output_dir=tmp_path)

    stale = [f for f in report.runs[0].findings if f.field == "posted_at"]
    assert stale
    assert "stale" in stale[0].message.lower()


def test_audit_cross_run_drift_above_threshold(tmp_path: Path) -> None:
    """Salary-currency goes from 100% null to 0% null between runs: flag."""
    run_a = [_job(sid="a1"), _job(sid="a2")]  # both salary-null
    run_b = [
        _job(sid="b1", salary_min=100, salary_currency="USD", salary_period="annual"),
        _job(sid="b2", salary_min=200, salary_currency="USD", salary_period="annual"),
    ]
    scraper = _DriftScraper([run_a, run_b])
    report = audit_scraper(scraper, runs=2, output_dir=tmp_path)

    assert report.cross_run_findings, "expected cross-run drift findings"
    # At least one finding should mention a salary field.
    salary_drift = [f for f in report.cross_run_findings if "salary" in f.field]
    assert salary_drift


def test_audit_no_findings_for_clean_data(tmp_path: Path) -> None:
    scraper = _StubScraper(
        [
            _job(
                sid=f"clean-{i}",
                title="Backend Engineer",
                location="Bengaluru",
                apply_url=f"https://acme.test/jobs/{i}",
                posted_at=datetime.now(UTC),
            )
            for i in range(5)
        ]
    )
    report = audit_scraper(scraper, runs=1, output_dir=tmp_path)
    assert report.runs[0].findings == []


def test_audit_field_stats_compute_null_rate(tmp_path: Path) -> None:
    """Per-field stats reflect null counts correctly."""
    scraper = _StubScraper(
        [
            _job(sid="1", salary_min=150_000, salary_currency="USD", salary_period="annual"),
            _job(sid="2"),  # salary_min=None
        ]
    )
    report = audit_scraper(scraper, runs=1, output_dir=tmp_path)
    salary_stats = report.runs[0].fields["salary_min"]
    assert salary_stats.null_count == 1
    assert salary_stats.total_count == 2
    assert salary_stats.null_rate == 0.5


def test_null_rate_drift_threshold_constant_sane() -> None:
    """Sanity: the threshold is between 1% and 50%."""
    assert 0.01 < NULL_RATE_DRIFT_THRESHOLD < 0.50
