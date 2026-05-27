"""CLI entrypoint; populated in Phase 11."""

from __future__ import annotations

import typer

app = typer.Typer(help="Knockknock CLI", no_args_is_help=True)


@app.callback()
def _root() -> None:
    """Root callback so subcommands are not collapsed when only one exists."""


@app.command()
def version() -> None:
    """Print the package version."""
    from knockknock import __version__

    typer.echo(__version__)


if __name__ == "__main__":
    app()
