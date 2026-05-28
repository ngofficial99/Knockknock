"""Structured logging configuration.

Bridges stdlib logging into structlog so any third-party library that uses
``logging`` flows through the same pipeline. Console renderer in dev, JSON in
production (Cloud Run, which captures stdout as structured logs).
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.stdlib import BoundLogger


def configure_logging(*, level: str = "INFO", json: bool = False) -> BoundLogger:
    """Configure structlog with stdlib bridge. Returns a root bound logger."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper()),
    )

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if json:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    # ``get_logger`` returns a lazy proxy; ``.bind()`` materialises a real
    # ``BoundLogger`` using the configured wrapper_class.
    logger: BoundLogger = structlog.get_logger("knockknock").bind()
    return logger
