"""CLI entrypoint."""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(help="Knockknock CLI", no_args_is_help=True)
pipeline_app = typer.Typer(help="Pipeline commands", no_args_is_help=True)
app.add_typer(pipeline_app, name="pipeline")


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
    """Run the discovery/score/draft pipeline.

    Wires Phase-3 stages (currently: discover) inside a single
    ``session_scope`` transaction so commit/rollback is atomic per run.
    """
    from knockknock.config.preferences import load_preferences
    from knockknock.config.settings import Settings
    from knockknock.db.engine import make_sync_engine
    from knockknock.db.session import session_scope
    from knockknock.logging import configure_logging
    from knockknock.observability.metrics import begin_run, finalize_run
    from knockknock.pipeline.discover import DiscoverStage
    from knockknock.pipeline.runner import PipelineRunner
    from knockknock.pipeline.stage import Stage, StageContext
    from knockknock.scrapers.registry import build_scrapers

    settings = Settings()
    configure_logging(level=settings.log_level, json=settings.runtime == "cloud")
    prefs = load_preferences(Path(settings.preferences_path))
    engine = make_sync_engine(settings.database_url)

    with session_scope(engine) as session:
        run_id = begin_run(session)
        scrapers = build_scrapers(prefs)
        stages: list[Stage] = [
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
