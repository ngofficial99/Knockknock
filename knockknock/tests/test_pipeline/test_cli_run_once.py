from __future__ import annotations

import re

from typer.testing import CliRunner

from knockknock.__main__ import app

# Wider terminal so Rich does not wrap `--once` across lines under CI.
runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def test_pipeline_run_once_help() -> None:
    """`knockknock pipeline run --help` should advertise the --once flag."""
    result = runner.invoke(app, ["pipeline", "run", "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    assert "--once" in _strip_ansi(result.stdout)
