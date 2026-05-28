"""CLI entrypoint."""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(help="Knockknock CLI", no_args_is_help=True)
pipeline_app = typer.Typer(help="Pipeline commands", no_args_is_help=True)
scraper_app = typer.Typer(help="Scraper utilities (manual smoke tests).", no_args_is_help=True)
app.add_typer(pipeline_app, name="pipeline")
app.add_typer(scraper_app, name="scraper")


@app.callback()
def _root() -> None:
    """Root callback so subcommands are not collapsed when only one exists."""


@app.command()
def version() -> None:
    """Print the package version."""
    from knockknock import __version__

    typer.echo(__version__)


@pipeline_app.command("run")
def pipeline_run(
    once: bool = typer.Option(False, "--once", help="Run a single pipeline pass and exit."),
) -> None:
    """Run the discovery → pre_filter pipeline (more stages added in later phases).

    Wires Phase-3 + Phase-4 stages inside a single ``session_scope``
    transaction so commit/rollback is atomic per run. Before stages run, the
    blacklist YAML is synced into ``companies_blacklist`` and a
    ``BlacklistMatcher`` snapshot is loaded for the ``RuleEngine``.
    """
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
    from knockknock.pipeline.stage import Stage, StageContext
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
        stages: list[Stage] = [
            DiscoverStage(
                session=session,
                scrapers=scrapers,
                hourly_cap=prefs.limits.hourly_discover_cap,
            ),
            PreFilterStage(session=session, engine=rule_engine),
        ]
        summary = PipelineRunner(stages=stages).run_once(StageContext(run_id=run_id))
        finalize_run(session, run_id, summary.results)

    if not once:
        typer.echo("non-once mode not implemented yet; phase 12 will add scheduler integration")


@scraper_app.command("test")
def scraper_test(
    source: str = typer.Option(
        "hn",
        "--source",
        help="Which scraper to run. Currently only 'hn' is supported.",
    ),
    limit: int = typer.Option(
        10,
        "--limit",
        help="Max jobs to fetch (overrides preferences.limits.hourly_discover_cap).",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--write",
        help="Dry-run prints jobs; --write upserts into the configured database.",
    ),
) -> None:
    """Run a single scraper standalone for manual smoke-testing.

    Defaults to ``--dry-run`` so it is safe to invoke without a database.
    Use ``--write`` to actually upsert jobs through ``DiscoverStage``; this
    requires ``KNOCKKNOCK_DATABASE_URL`` and writes to the real DB inside a
    ``PipelineRun`` row (status PARTIAL/SUCCESS).
    """
    import os

    from knockknock.config.preferences import load_preferences
    from knockknock.logging import configure_logging
    from knockknock.scrapers.registry import build_scrapers

    # Preferences path can be overridden with KNOCKKNOCK_PREFERENCES_PATH;
    # this lets ``--dry-run`` work without requiring KNOCKKNOCK_DATABASE_URL.
    prefs_path = Path(os.environ.get("KNOCKKNOCK_PREFERENCES_PATH", "config/job_preferences.yaml"))
    log_level = os.environ.get("KNOCKKNOCK_LOG_LEVEL", "INFO")
    runtime = os.environ.get("KNOCKKNOCK_RUNTIME", "local")
    configure_logging(level=log_level, json=runtime == "cloud")
    prefs = load_preferences(prefs_path)

    all_scrapers = build_scrapers(prefs)
    selected = [s for s in all_scrapers if s.source.value.lower() == source.lower()]
    if not selected:
        typer.secho(
            f"No enabled scraper matches --source={source!r}. "
            f"Enabled sources: {[s.source.value for s in all_scrapers] or '<none>'}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    if dry_run:
        _scraper_test_dry_run(selected, limit=limit)
    else:
        # --write requires a full Settings (database_url is mandatory there).
        from knockknock.config.settings import Settings

        settings = Settings()
        _scraper_test_write(selected, limit=limit, settings=settings)


@scraper_app.command("audit")
def scraper_audit(
    source: str = typer.Option(
        "hn",
        "--source",
        help="Which scraper to audit.",
    ),
    runs: int = typer.Option(
        3,
        "--runs",
        min=1,
        help="Number of audit runs (>=2 enables cross-run regression detection).",
    ),
    output: str = typer.Option(
        "audits",
        "--output",
        help="Directory for JSONL dumps and Markdown/JSON reports.",
    ),
) -> None:
    """Field-level data-quality audit for a scraper.

    Runs the scraper N times, persists each run's full payload as JSONL,
    and emits a Markdown + JSON report covering per-field null rates,
    distinct-value counts, length distributions, and known data-quality
    heuristics (title-looks-like-location, malformed URLs, duplicate
    source_job_ids, generic redirector domains, stale posted_at).

    Cross-run regression: any field whose null-rate drifts >10% between
    consecutive runs is flagged WARN.
    """
    import os

    from knockknock.config.preferences import load_preferences
    from knockknock.logging import configure_logging
    from knockknock.scrapers.audit import audit_scraper
    from knockknock.scrapers.registry import build_scrapers

    prefs_path = Path(os.environ.get("KNOCKKNOCK_PREFERENCES_PATH", "config/job_preferences.yaml"))
    log_level = os.environ.get("KNOCKKNOCK_LOG_LEVEL", "INFO")
    runtime = os.environ.get("KNOCKKNOCK_RUNTIME", "local")
    configure_logging(level=log_level, json=runtime == "cloud")
    prefs = load_preferences(prefs_path)

    all_scrapers = build_scrapers(prefs)
    selected = [s for s in all_scrapers if s.source.value.lower() == source.lower()]
    if not selected:
        typer.secho(
            f"No enabled scraper matches --source={source!r}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    typer.secho(
        f"Running {runs} audit run(s) for source={source!r}; output={output}",
        fg=typer.colors.CYAN,
        bold=True,
    )
    report = audit_scraper(selected[0], runs=runs, output_dir=Path(output))

    # Brief stdout summary so an interactive run gives signal immediately.
    for r in report.runs:
        typer.echo(f"\nRun {r.run_index}: jobs={r.jobs_emitted}")
        for f_ in r.findings:
            typer.secho(
                f"  [{f_.severity}] {f_.field}: {f_.message}",
                fg=typer.colors.YELLOW if f_.severity == "WARN" else typer.colors.RED,
            )
    if report.cross_run_findings:
        typer.echo("\nCross-run regressions:")
        for f_ in report.cross_run_findings:
            typer.secho(f"  [{f_.severity}] {f_.field}: {f_.message}", fg=typer.colors.YELLOW)
    typer.secho(f"\nReports written under {output}/", fg=typer.colors.GREEN)


def _scraper_test_dry_run(selected: list, limit: int) -> None:  # type: ignore[type-arg]
    """Print scraped jobs to stdout without touching the DB."""
    from knockknock.scrapers.base import ScrapedJob

    count = 0
    for scraper in selected:
        typer.secho(f"\n=== {scraper.source.value} ===", fg=typer.colors.CYAN, bold=True)
        for job in scraper.fetch():
            if count >= limit:
                break
            count += 1
            _print_job(count, job)
        if count >= limit:
            break
    typer.secho(
        f"\nDry-run complete. {count} job(s) emitted (limit={limit}).",
        fg=typer.colors.GREEN,
    )

    # Reference import so static checkers know which type we expect.
    _: type[ScrapedJob] = ScrapedJob


def _print_job(idx: int, job) -> None:  # type: ignore[no-untyped-def]
    typer.echo(f"\n[{idx}] {job.title}")
    typer.echo(f"    company  : {job.company_name} ({job.company_domain})")
    typer.echo(f"    location : {job.location}")
    typer.echo(f"    source   : {job.source.value}/{job.source_job_id}")
    typer.echo(f"    apply_url: {job.apply_url}")
    if job.salary_min is not None:
        if job.salary_max is not None and job.salary_max != job.salary_min:
            comp = f"{job.salary_min:,}-{job.salary_max:,}"
        else:
            comp = f"{job.salary_min:,}"
        typer.echo(
            f"    salary   : {comp} {job.salary_currency} ({job.salary_period}) "
            f"-- raw: {job.salary_raw!r}"
        )
    if job.posted_at is not None:
        typer.echo(f"    posted   : {job.posted_at.isoformat()}")
    if job.description:
        snippet = job.description.replace("\n", " ")[:160]
        typer.echo(f"    excerpt  : {snippet}")


def _scraper_test_write(selected: list, limit: int, settings) -> None:  # type: ignore[type-arg,no-untyped-def]
    """Run the scraper through ``DiscoverStage`` against the real DB."""
    from knockknock.db.engine import make_sync_engine
    from knockknock.db.session import session_scope
    from knockknock.observability.metrics import begin_run, finalize_run
    from knockknock.pipeline.discover import DiscoverStage
    from knockknock.pipeline.runner import PipelineRunner
    from knockknock.pipeline.stage import Stage, StageContext

    engine = make_sync_engine(settings.database_url)
    with session_scope(engine) as session:
        run_id = begin_run(session)
        stages: list[Stage] = [
            DiscoverStage(session=session, scrapers=selected, hourly_cap=limit),
        ]
        summary = PipelineRunner(stages=stages).run_once(StageContext(run_id=run_id))
        finalize_run(session, run_id, summary.results)

    for r in summary.results:
        typer.echo(
            f"stage={r.stage} processed={r.processed} advanced={r.advanced} "
            f"rejected={r.rejected} errors={r.errors}"
        )
    typer.secho(f"pipeline_run id={run_id} committed.", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
