"""Top-level exception hierarchy."""

from __future__ import annotations


class KnockknockError(Exception):
    """Base for all Knockknock-raised errors."""


class ConfigError(KnockknockError):
    """Raised when configuration is missing or malformed."""


class ExternalServiceError(KnockknockError):
    """Raised when an external API call ultimately fails after retries."""


class RateLimitedError(ExternalServiceError):
    """Raised when an upstream provider returns a hard rate-limit response."""


class PipelineError(KnockknockError):
    """Raised when the pipeline cannot progress a job for a known reason."""
