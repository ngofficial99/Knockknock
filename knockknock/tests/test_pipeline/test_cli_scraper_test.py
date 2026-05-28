from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime

from typer.testing import CliRunner

from knockknock import __main__ as cli_main
from knockknock.db.enums import CompanySizeBucket, JobSource
from knockknock.scrapers.base import ScrapedJob

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


class _StubHN:
    """Stand-in scraper that yields a fixed list of jobs.

    Quacks like ``knockknock.scrapers.hn.HNScraper`` for ``build_scrapers``'s
    eyes — we monkeypatch ``build_scrapers`` itself so we never touch HTTP.
    """

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


def test_scraper_test_help_lists_flags() -> None:
    result = runner.invoke(cli_main.app, ["scraper", "test", "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    out = _strip_ansi(result.stdout)
    assert "--source" in out
    assert "--limit" in out
    assert "--dry-run" in out
    assert "--write" in out


def test_scraper_test_dry_run_emits_jobs(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`scraper test --dry-run` prints jobs to stdout without touching the DB."""
    fake_scrapers = [_StubHN([_make_job("hn-1"), _make_job("hn-2")])]
    monkeypatch.setattr(cli_main, "_scraper_test_write", lambda *a, **kw: None)
    monkeypatch.setattr("knockknock.scrapers.registry.build_scrapers", lambda prefs: fake_scrapers)

    result = runner.invoke(
        cli_main.app,
        ["scraper", "test", "--source", "hn", "--limit", "5", "--dry-run"],
        env={
            "COLUMNS": "200",
            "KNOCKKNOCK_DATABASE_URL": "postgresql+psycopg://stub:stub@localhost:5432/stub",
            "KNOCKKNOCK_PREFERENCES_PATH": "config/job_preferences.yaml",
        },
    )

    assert result.exit_code == 0, result.stdout
    out = _strip_ansi(result.stdout)
    assert "Backend Engineer" in out
    assert "acme.test" in out
    assert "hn-1" in out and "hn-2" in out
    assert "Dry-run complete. 2 job(s) emitted" in out


def test_scraper_test_unknown_source_exits_nonzero(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("knockknock.scrapers.registry.build_scrapers", lambda prefs: [])

    result = runner.invoke(
        cli_main.app,
        ["scraper", "test", "--source", "doesnotexist"],
        env={
            "COLUMNS": "200",
            "KNOCKKNOCK_DATABASE_URL": "postgresql+psycopg://stub:stub@localhost:5432/stub",
            "KNOCKKNOCK_PREFERENCES_PATH": "config/job_preferences.yaml",
        },
    )

    assert result.exit_code == 2
