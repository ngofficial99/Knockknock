from __future__ import annotations

from typer.testing import CliRunner

from knockknock.__main__ import app

runner = CliRunner()


def test_pipeline_run_once_help() -> None:
    """`knockknock pipeline run --help` should advertise the --once flag."""
    result = runner.invoke(app, ["pipeline", "run", "--help"])
    assert result.exit_code == 0
    assert "--once" in result.stdout
