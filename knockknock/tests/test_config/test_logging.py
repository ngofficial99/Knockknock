from __future__ import annotations

import structlog

from knockknock.logging import configure_logging


def test_configure_logging_returns_logger() -> None:
    logger = configure_logging(level="INFO", json=False)
    assert logger is not None
    assert isinstance(logger, structlog.stdlib.BoundLogger)


def test_configure_logging_json_mode() -> None:
    logger = configure_logging(level="DEBUG", json=True)
    assert logger is not None
